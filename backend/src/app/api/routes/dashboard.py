from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from statistics import mean
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.db.session import get_session
from app.models.ai_usage import AIUsageLog
from app.models.checkin import Checkin
from app.models.message import Message, MessageDirection, MessageSenderType
from app.models.outcome import OutcomeEvent, OutcomeSnapshot
from app.models.reminder import CHURN_RECALL_RULE_ID, ReminderEvent
from app.models.service_case import ServiceCase, ServiceCaseGroupType, ServiceCaseStatus
from app.models.user import User
from app.models.questionnaire import QuestionnaireSubmission
from app.models.user_document import ACTIVE_DOCUMENT_TYPES, DocumentStatus, DocumentType, UserDocument
from app.models.user_profile import UserProfile
from app.models.user_state_profile import UserStateProfile
from app.schemas.dashboard import (
    AIBalanceSummary,
    AICostCaseItem,
    AICostSummary,
    BodyChangeMetricWindow,
    BodyChangeSample,
    BodyChangeSegment,
    BodyChangeSegmentBucket,
    BodyChangeStat,
    BodyChangeSummary,
    BodyChangeWindow,
    CaseHealthItem,
    DashboardCaseCheckins,
    DashboardCaseInsightItem,
    DashboardCaseMessageEvents,
    DashboardCasePendingDocuments,
    DashboardCaseRef,
    DashboardRiskReason,
    DashboardCheckinEvent,
    DashboardMetric,
    DashboardMessageEvent,
    DashboardPendingDocument,
    DashboardSummary,
    EmotionalAlertCaseItem,
    EmotionalAlertMessage,
    OutcomeDashboardSummary,
    OutcomeTrendSummary,
    PopulationDistribution,
    PopulationDistributionBucket,
    PopulationGeography,
    PopulationGeoCity,
    PopulationGeoProvince,
    PopulationInsightResponse,
    PopulationPortrait,
)
from app.services.body_change_stats import (
    EXCLUDE_BELOW_RATE,
    WINDOW_DAYS,
    CaseWindowChange,
    compute_case_window_changes,
    summarize_deltas,
)
from app.services.checkin_completion import is_complete_checkin
from app.services.china_regions import match_city
from app.schemas.service_case import ServiceCaseRead
from app.services.intake_facts import (
    build_intake_fact_summary,
    customer_type_for_profile,
    structured_answer_values,
    weight_loss_band_from_profile,
)
from app.services.health_taxonomy import health_profile_dimension_labels
# 成效表收全方向；"改善率"类指标只能取正向，复用写入侧的同一份定义避免口径漂移。
from app.services.outcome_engine import POSITIVE_EVENT_TYPES as POSITIVE_OUTCOME_EVENT_TYPES
from app.services.outcome_trends import build_outcome_trend_summary
from app.services.questionnaire_template import get_active_questionnaire_fields
from app.services.population_scope import select_population_service_cases
from app.services.population_insight import (
    PopulationInsightGenerationError,
    get_population_insight,
    refresh_population_insight,
)
from app.services.ai_service.balance import fetch_deepseek_balance
from app.services.service_case_queries import (
    active_service_case_condition,
    effective_start_at,
    last_user_inbound_subquery,
)

router = APIRouter()


def _stats_case_condition():
    """看板统计口径：只统计已通过本系统入营问卷的新群；老群不进统计避免稀释。

    服务/聊天链路保持全量（老群照常被服务），仅看板与群体画像默认只看新群。
    """
    return and_(
        active_service_case_condition(),
        ServiceCase.group_type == ServiceCaseGroupType.NEW_GROUP.value,
    )


@router.get("/deepseek-balance", response_model=AIBalanceSummary)
async def dashboard_deepseek_balance() -> AIBalanceSummary:
    """DeepSeek 账户实时余额（独立于逐 token 估算成本）。

    余额是外部实时调用，特意不塞进 /summary，避免拖慢整个看板；balance.py 内有
    60s 缓存，看板频繁加载也不会频繁请求 DeepSeek。查不到时 available=False，前端兜底显示。
    """
    settings = get_settings()
    threshold = float(settings.ai_balance_alert_threshold_yuan or 0)
    snap = await fetch_deepseek_balance(settings)
    if snap is None or not snap.available or snap.balance_yuan is None:
        return AIBalanceSummary(available=False, alert_threshold_yuan=threshold)
    return AIBalanceSummary(
        available=True,
        balance_yuan=snap.balance_yuan,
        currency=snap.currency,
        is_available=snap.is_available,
        is_low=(snap.balance_yuan < threshold) or (not snap.is_available),
        alert_threshold_yuan=threshold,
        fetched_at=datetime.fromtimestamp(snap.fetched_at, tz=timezone.utc),
    )


@router.get("/outcome-trends", response_model=OutcomeTrendSummary)
async def dashboard_outcome_trends(
    months: int = 6,
    session: AsyncSession = Depends(get_session),
) -> OutcomeTrendSummary:
    """按服务开始月份返回同成熟阶段趋势，避免新老批次分母不可比。"""

    return OutcomeTrendSummary(
        **await build_outcome_trend_summary(session, months=months)
    )


def _today_bounds() -> tuple[datetime, datetime]:
    china_tz = ZoneInfo("Asia/Shanghai")
    today = datetime.now(china_tz).date()
    today_start = datetime.combine(today, time.min, tzinfo=china_tz).astimezone(timezone.utc)
    return today_start, today_start + timedelta(days=1)


def _requires_human_intervention_expr():
    """入站用户消息是否触发转人工/@班主任：analysis_json.requires_human_intervention=true。"""
    return Message.analysis_json["requires_human_intervention"].as_boolean().is_(True)


@router.get("/summary", response_model=DashboardSummary)
async def dashboard_summary(
    session: AsyncSession = Depends(get_session),
) -> DashboardSummary:
    total = await session.scalar(
        select(func.count(ServiceCase.id)).where(_stats_case_condition())
    )
    in_service = await session.scalar(
        select(func.count(ServiceCase.id)).where(
            _stats_case_condition(),
            ServiceCase.status == ServiceCaseStatus.IN_SERVICE,
        )
    )
    today_start, today_end = _today_bounds()
    today = today_start.astimezone(ZoneInfo("Asia/Shanghai")).date()
    today_checkins = list(
        await session.scalars(
            select(Checkin)
            .join(ServiceCase, Checkin.service_case_id == ServiceCase.id)
            .where(
                _stats_case_condition(),
                Checkin.deleted_at.is_(None),
                Checkin.checkin_date == today,
            )
        )
    )
    checkins_today = len(today_checkins)
    complete_checkins_today = sum(is_complete_checkin(row) for row in today_checkins)
    messages_today = await session.scalar(
        select(func.count(Message.id))
        .join(ServiceCase, Message.service_case_id == ServiceCase.id)
        .where(
            _stats_case_condition(),
            Message.created_at >= today_start,
            Message.created_at < today_end,
            Message.direction == MessageDirection.INBOUND,
            Message.sender_type == MessageSenderType.USER,
        )
    ) or 0
    messages_outbound_today = await session.scalar(
        select(func.count(Message.id))
        .join(ServiceCase, Message.service_case_id == ServiceCase.id)
        .where(
            _stats_case_condition(),
            Message.created_at >= today_start,
            Message.created_at < today_end,
            Message.direction == MessageDirection.OUTBOUND,
        )
    ) or 0
    # High-performance DISTINCT ON DB-level count of the latest documents to prevent loading all documents in memory
    from sqlalchemy import text
    doc_subq = (
        select(UserDocument.status)
        .distinct(
            UserDocument.service_case_id,
            UserDocument.document_type,
            text("content_json->>'stage'")
        )
        .join(ServiceCase, UserDocument.service_case_id == ServiceCase.id)
        .where(
            _stats_case_condition(),
            UserDocument.document_type.in_(ACTIVE_DOCUMENT_TYPES),
        )
        .order_by(
            UserDocument.service_case_id,
            UserDocument.document_type,
            text("content_json->>'stage'"),
            UserDocument.created_at.desc()
        )
        .subquery()
    )
    pending_documents = await session.scalar(
        select(func.count())
        .select_from(doc_subq)
        .where(doc_subq.c.status.in_([DocumentStatus.DRAFT, DocumentStatus.REVIEWING]))
    ) or 0
    new_users_today = await session.scalar(
        select(func.count(ServiceCase.id)).where(
            _stats_case_condition(),
            ServiceCase.created_at >= today_start,
            ServiceCase.created_at < today_end,
        )
    )
    risk_users = await session.scalar(
        select(func.count(ServiceCase.id)).where(
            _stats_case_condition(),
            ServiceCase.risk_level == "high",
        )
    )
    # 今日转人工事件数：同一用户多次触发算多条；这不是服务单上的持续状态。
    # 口径与「今日用户消息/今日有打卡」一致，每日自然滚动，无需人工清零机制。
    # 判定信号：入站用户消息的 analysis_json.requires_human_intervention（由 analyze_message_insight 写入，
    # 正是 engine 触发 @班主任 handoff 的条件）。注：旧的「待审核 AI 回复(PENDING)」机制已废弃——
    # 现凡需介入一律直接 @班主任(status=SENT)，不再生成 PENDING 回复，故不能再用它统计。
    handoff_events_today = await session.scalar(
        select(func.count(Message.id))
        .join(ServiceCase, Message.service_case_id == ServiceCase.id)
        .where(
            _stats_case_condition(),
            Message.created_at >= today_start,
            Message.created_at < today_end,
            Message.direction == MessageDirection.INBOUND,
            Message.sender_type == MessageSenderType.USER,
            _requires_human_intervention_expr(),
        )
    )
    now = datetime.now(timezone.utc)
    # 未跟进/流失只看「用户本人」的发言：班主任在群里说话不算用户还活着。
    last_message_subq = last_user_inbound_subquery()
    last_activity_at = func.coalesce(last_message_subq.c.last_at, ServiceCase.created_at)
    unfollowed_users = await session.scalar(
        select(func.count(ServiceCase.id))
        .outerjoin(last_message_subq, last_message_subq.c.service_case_id == ServiceCase.id)
        .where(
            _stats_case_condition(),
            ServiceCase.status == ServiceCaseStatus.IN_SERVICE,
            last_activity_at < now - timedelta(days=2),
            last_activity_at >= now - timedelta(days=7),
        )
    )
    lost_users = await session.scalar(
        select(func.count(ServiceCase.id))
        .outerjoin(last_message_subq, last_message_subq.c.service_case_id == ServiceCase.id)
        .where(
            _stats_case_condition(),
            ServiceCase.status == ServiceCaseStatus.IN_SERVICE,
            last_activity_at < now - timedelta(days=7),
        )
    )
    renewal_followup_users = await session.scalar(
        select(func.count(ServiceCase.id)).where(
            _stats_case_condition(),
            ServiceCase.status == ServiceCaseStatus.IN_SERVICE,
            ServiceCase.started_at.is_not(None),
            ServiceCase.started_at <= now - timedelta(days=18),
            ServiceCase.started_at >= now - timedelta(days=28),
        )
    )
    renewal_intent_users = await _count_flagged_cases(
        session,
        ServiceCase.renewal_intent_flags.is_not(None),
    )
    refund_intent_users = await _count_flagged_cases(
        session,
        ServiceCase.refund_intent_flags.is_not(None),
    )
    refund_pending_users = await _count_flagged_cases(
        session,
        and_(
            ServiceCase.refund_pending_confirmation.is_(True),
            ServiceCase.status != ServiceCaseStatus.REFUNDED,
        ),
    )

    today_user_messages = list(
        await session.scalars(
            select(Message)
            .join(ServiceCase, Message.service_case_id == ServiceCase.id)
            .where(
                _stats_case_condition(),
                Message.created_at >= today_start,
                Message.created_at < today_end,
                Message.direction == MessageDirection.INBOUND,
                Message.sender_type == MessageSenderType.USER,
            )
        )
    )
    emotional_users_today = len(
        {
            message.service_case_id
            for message in today_user_messages
            if message.service_case_id and _has_emotion_signal(message)
        }
    )
    outcome_summary = await _build_outcome_summary(
        session,
        total_cases=total or 0,
        renewal_intent_users=renewal_intent_users,
        refund_intent_users=refund_intent_users,
        now=now,
    )
    ai_cost_summary = await _build_ai_cost_summary(session)

    return DashboardSummary(
        service_cases_total=total or 0,
        service_cases_in_service=in_service or 0,
        checkins_today=checkins_today or 0,
        complete_checkins_today=complete_checkins_today,
        messages_today=messages_today or 0,
        messages_outbound_today=messages_outbound_today or 0,
        pending_documents=pending_documents or 0,
        new_users_today=new_users_today or 0,
        risk_users=risk_users or 0,
        handoff_events_today=handoff_events_today or 0,
        unfollowed_users=unfollowed_users or 0,
        renewal_followup_users=renewal_followup_users or 0,
        repurchase_soon_users=renewal_followup_users or 0,
        renewal_intent_users=renewal_intent_users,
        refund_intent_users=refund_intent_users,
        refund_pending_users=refund_pending_users,
        lost_users=lost_users or 0,
        emotional_users_today=emotional_users_today or 0,
        outcome_summary=outcome_summary,
        ai_cost_summary=ai_cost_summary,
    )


# 缺失/未采集统一归一到该标签；画像分布计入 unknown，下钻端点也按它命中"未采集"桶。
UNKNOWN_LABEL = "未采集"
# 分类维度的历史哨兵值（含各处"未提供/待完善"等），一律视为未采集。
_CATEGORY_UNKNOWNS = {"", "未采集", "未提供", "待完善", "需补充身高体重后判断"}

_SUPPLEMENT_STATUS_LABELS = {
    "using": "正在使用",
    "considering": "咨询/考虑使用",
    "stopped": "已停用",
}

# 补剂品类：档案官结构化输出 supplement_category，画像按人数分桶。
_SUPPLEMENT_CATEGORY_LABELS = {
    "micronutrient": "维生素等微量营养补充",
    "macronutrient": "蛋白粉/膳食纤维/健康油脂等宏量营养补充",
    "health_supplement": "保健品补品（如人参制品）",
    "herbal": "中式草本（药材/代用茶）",
    "other": "其他补剂",
}

_PORTRAIT_OPTION_LABELS: dict[str, dict[str, str]] = {
    "main_need": {
        "感觉总是累、没精神": "疲劳、没精神",
        "总是容易饿、吃完还想吃": "容易饿、饱腹感不足",
        "晚上嘴馋、控制不住": "晚间嘴馋、控制不住",
        "作息乱、睡不好": "作息乱、睡不好",
        "白天脑子不清楚、专注度差": "清醒感与专注度差",
        "腰腹部肥胖明显、身体发沉": "腰腹肥胖、身体发沉",
        "吃得不算多，但状态和体型都不理想": "食量不多但状态体型不佳",
        "情绪不稳、压力一大就容易乱": "情绪与压力易失控",
        "便秘、胀气、肠胃消化总是不舒服": "消化与排便不适",
        "没有明显健康问题，想进入更规律、更高能量的生活状态": "追求规律、高能量状态",
    },
    "expected_change": {
        "白天更有精神": "白天更有精神",
        "晚上更容易入睡，不起夜，睡眠质量明显提高": "睡眠质量提高",
        "白天饥饿感更稳定": "饥饿感更稳定",
        "晚上不容易乱吃": "晚间不乱吃",
        "身体更轻盈、腰腹部维度缩减": "身体轻盈、腰腹缩减",
        "每天节律更稳定、情绪更稳": "节律与情绪更稳定",
        "脑子更清醒、专注更好": "清醒与专注改善",
        "饮食更容易控制": "饮食更易控制",
        "开始进入“可持续变好”的状态": "进入可持续变好状态",
        "体重体态更匀称，您也可以在“其他”选项填写您的体重和腰围数字管理目标（如X个月减重X公斤，减少XX厘米）": "体重体态更匀称",
    },
    "diet_pattern": {
        "我执行的是排除型饮食，很多食物都主动不吃": "排除型饮食",
        "我以肉类 / 动物性食物为核心，种类很少": "动物性食物为主、种类少",
        "我在认真控碳，希望保持稳定燃脂状态": "控碳燃脂模式",
        "我追求低糖、少加工、蛋白优先、主食适量": "低糖少加工、蛋白优先",
        "我主食偏多，是典型高碳饮食": "高碳饮食",
        "我甜的很多，是典型高糖饮食": "高糖饮食",
        "我整体都比较乱，经常外卖、宵夜、炸物、酒精混着来": "饮食较乱、外卖宵夜等",
    },
}

# 这两道单选题的选项本身就是有意义的业务梯度，群体画像应跟随
# 当前启用问卷的选项顺序，而不是被各分桶的实时人数重排。
_ORDERED_QUESTIONNAIRE_DIMENSIONS: dict[str, tuple[str, ...]] = {
    "diet_pattern": ("长期饮食模式",),
    "sleep_quality": ("睡眠质量的主观评价",),
}

# 群体画像维度元数据：画像端点与下钻端点共用，保证顺序/标签/分桶完全一致。
# numeric=True 的维度带 edges/labels（升序左闭分桶，含 0 计数桶）；
# 有业务梯度的问卷分类跟随当前问卷选项顺序，其余分类维度按计数降序。
_PORTRAIT_DIMENSIONS: list[dict[str, Any]] = [
    {
        "key": "customer_type",
        "label": "客户分型",
        "description": "按风险与减重带确定性投影的单一分型标签，用于快速定调。",
        "numeric": False,
    },
    {
        "key": "main_need",
        "label": "最想先解决",
        "description": "用户在入营问卷中明确选择的首要问题；可多选，不用 AI 猜测深层动机。",
        "numeric": False,
        "multi_select": True,
    },
    {
        "key": "expected_change",
        "label": "28天期待变化",
        "description": "用户明确希望在本服务周期看到的变化；可多选，其他自由填写统一归入补充项。",
        "numeric": False,
        "multi_select": True,
    },
    {
        "key": "diet_pattern",
        "label": "长期饮食模式",
        "description": "入营问卷中最符合用户长期饮食习惯的一项，不等同于当前产品使用情况。",
        "numeric": False,
    },
    {
        "key": "sleep_problem",
        "label": "常见睡眠问题",
        "description": "用户最近更常见的睡眠困扰；可多选，暂无明显问题作为独立分桶。",
        "numeric": False,
        "multi_select": True,
    },
    {
        "key": "sleep_quality",
        "label": "主观睡眠质量",
        "description": "用户对最近睡眠质量的主观评价。",
        "numeric": False,
    },
    {
        "key": "supplement_habit",
        "label": "补剂使用与咨询",
        "description": (
            "从群聊原话沉淀的具体补剂及当前状态；"
            "‘咨询/考虑使用’不等同于已在使用。"
        ),
        "numeric": False,
        "multi_select": True,
    },
    {
        "key": "supplement_category",
        "label": "补剂品类分布",
        "description": (
            "按品类汇总用户自发使用/咨询的补剂：微量营养、宏量营养、保健补品、中式草本等；"
            "同一用户可进入多个品类，按人数去重计数。"
        ),
        "numeric": False,
        "multi_select": True,
    },
    {
        "key": "health_domain",
        "label": "健康领域",
        "description": "已明确健康情况对应的领域；同一用户可进入多个领域，人数按服务单去重。",
        "numeric": False,
        "multi_select": True,
    },
    {
        "key": "health_condition",
        "label": "具体健康情况",
        "description": "用户已明确披露的具体疾病或健康情况，不包含待检查和可能性结论。",
        "numeric": False,
        "multi_select": True,
    },
    {
        "key": "medication_category",
        "label": "长期用药类别",
        "description": "当前长期使用的处方药类别；作为服务注意项，不重复计算为疾病人数。",
        "numeric": False,
        "multi_select": True,
    },
    {
        "key": "special_stage",
        "label": "特殊阶段",
        "description": "已确认孕哺期或近期术后恢复等会改变服务方式的阶段。",
        "numeric": False,
        "multi_select": True,
    },
    {
        "key": "pending_condition",
        "label": "待确认健康情况",
        "description": "用户仅表示可能、疑似或待进一步检查的情况，不计入确诊疾病和持续高风险。",
        "numeric": False,
        "multi_select": True,
    },
    {
        "key": "allergy_category",
        "label": "明确过敏史",
        "description": "用户明确披露的过敏类型；是否影响产品仍需结合具体过敏物判断。",
        "numeric": False,
        "multi_select": True,
    },
    {
        "key": "weight_loss_band",
        "label": "减重带分布",
        "description": "综合身体指标、近期变化与生活状态判断的最终减重层级。",
        "numeric": False,
    },
    {
        "key": "age",
        "label": "年龄段",
        "description": "入营问卷填报年龄。",
        "numeric": True,
        "edges": [25, 35, 45, 55],
        "labels": ["<25 岁", "25-34 岁", "35-44 岁", "45-54 岁", "55 岁以上"],
    },
    {
        "key": "bmi",
        "label": "BMI 区间",
        "description": "由身高体重计算，阈值与减重带一致。",
        "numeric": True,
        "edges": [18.5, 24, 28, 32],
        "labels": ["偏瘦 <18.5", "正常 18.5-24", "超重 24-28", "肥胖 28-32", "重度 ≥32"],
    },
    {
        "key": "bodyfat",
        "label": "体脂率区间",
        "description": "入营问卷填报体脂率（%），部分用户未测量。",
        "numeric": True,
        "edges": [20, 25, 30, 35],
        "labels": ["<20%", "20-25%", "25-30%", "30-35%", "≥35%"],
    },
    {
        "key": "waist_height_ratio",
        "label": "腰高比区间",
        "description": "腰围/身高，>0.5 提示中心性肥胖风险。",
        "numeric": True,
        "edges": [0.4, 0.5, 0.6],
        "labels": ["<0.4", "0.4-0.5 健康", "0.5-0.6 偏高", "≥0.6 偏高风险"],
    },
]
_DIMENSION_BY_KEY = {dim["key"]: dim for dim in _PORTRAIT_DIMENSIONS}


@router.get("/population-portrait", response_model=PopulationPortrait)
async def dashboard_population_portrait(
    session: AsyncSession = Depends(get_session),
) -> PopulationPortrait:
    """群体画像：未删除、未退款用户的量化区间分布 + 省→市地域层级。

    每个用户只取最新未删除服务单；若该服务单已确认退款，则该用户不纳入。
    数据来源：AI 画像(customer_type/减重带，可缺失) + 最新入营问卷(诉求/饮食/睡眠/城市/身体指标)
    + 服务单结构化健康标签 + 档案官从群聊投影的稳定习惯。没有 AI 画像不会排除用户，只会令对应维度进入『未采集』。
    每个分布都返回有效样本与『未采集』计数，绝不静默丢弃缺失。分桶逻辑与下钻端点共用同一套 case 标签。
    """
    questionnaire_fields = await get_active_questionnaire_fields(session, "intake")
    total_users, case_labels = await _compute_case_portrait_labels(
        session,
        questionnaire_fields=questionnaire_fields,
    )
    if total_users == 0:
        return PopulationPortrait(total_users=0, distributions=[], geography=None)
    questionnaire_orders = {
        dimension_key: _questionnaire_portrait_option_order(
            questionnaire_fields,
            dimension_key,
            *title_parts,
        )
        for dimension_key, title_parts in _ORDERED_QUESTIONNAIRE_DIMENSIONS.items()
    }
    distributions = [
        _build_distribution(
            dim,
            case_labels,
            ordered_labels=questionnaire_orders.get(dim["key"]),
        )
        for dim in _PORTRAIT_DIMENSIONS
    ]
    return PopulationPortrait(
        total_users=total_users,
        distributions=distributions,
        geography=_build_geography(case_labels),
    )


@router.get("/population-insight", response_model=PopulationInsightResponse)
async def dashboard_population_insight(
    session: AsyncSession = Depends(get_session),
) -> PopulationInsightResponse:
    """只读最新共同洞察快照；打开页面不触发模型调用。"""

    return await get_population_insight(session)


@router.post("/population-insight/refresh", response_model=PopulationInsightResponse)
async def dashboard_refresh_population_insight(
    session: AsyncSession = Depends(get_session),
) -> PopulationInsightResponse:
    """人工立即刷新：一次请求最多一次模型调用，成功后追加快照。"""

    try:
        result = await refresh_population_insight(session, force=True)
    except PopulationInsightGenerationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if result.reason == "insufficient_profiles":
        raise HTTPException(status_code=409, detail="至少需要 2 位完整入营画像才能提炼共同洞察")
    return await get_population_insight(session)


@router.get("/population-portrait/members", response_model=list[ServiceCaseRead])
async def dashboard_population_portrait_members(
    key: str,
    bucket: str,
    session: AsyncSession = Depends(get_session),
) -> list[ServiceCase]:
    """群体画像下钻：返回某维度某分桶下的服务单（bucket 传『未采集』也能命中未采集桶）。

    与画像端点复用同一份 case 标签，保证条形图数字与下钻列表一一对应。返回结构同 /service-cases。
    """
    if key not in _DIMENSION_BY_KEY and key not in {"city", "province"}:
        raise HTTPException(status_code=404, detail=f"未知画像维度: {key}")
    _, case_labels = await _compute_case_portrait_labels(session)
    matched_ids = [
        cid
        for cid, labels in case_labels.items()
        if (
            bucket in labels.get(key, [])
            if isinstance(labels.get(key), list)
            else labels.get(key) == bucket
        )
    ]
    if not matched_ids:
        return []
    result = await session.scalars(
        select(ServiceCase)
        .where(ServiceCase.id.in_(matched_ids))
        .options(selectinload(ServiceCase.user))
        .order_by(ServiceCase.created_at.desc())
    )
    return list(result)


async def _compute_case_portrait_labels(
    session: AsyncSession,
    *,
    questionnaire_fields: list[dict[str, Any]] | None = None,
) -> tuple[int, dict[int, dict[str, str | list[str]]]]:
    """逐服务单算出各画像维度的分桶标签（画像端点与下钻端点共用，保证一致）。

    统计未软删除用户，每个用户只取最新未删除服务单；最新服务单已确认退款则排除。
    AI 画像不是入选条件：各维度按问卷、健康标签和画像的实际可用情况独立计数。
    返回 (total_users, {case_id: {dimension_key: bucket_label}})；缺失统一归一为 UNKNOWN_LABEL。
    """
    latest_cases = await select_population_service_cases(session)
    risk_by_case = {service_case.id: service_case.risk_level for service_case in latest_cases}
    health_by_case = {service_case.id: service_case.health_profile for service_case in latest_cases}
    case_ids = list(risk_by_case)
    if not case_ids:
        return 0, {}

    # 每个 case 最新 AI 画像（可缺失；缺失时用户仍进入总数）。
    latest_profile_json: dict[int, dict] = {}
    for profile in await session.scalars(
        select(UserProfile)
        .where(UserProfile.service_case_id.in_(case_ids))
        .order_by(UserProfile.service_case_id, UserProfile.created_at.desc())
        .distinct(UserProfile.service_case_id)
    ):
        latest_profile_json[profile.service_case_id] = profile.profile_json or {}

    # 每个 case 一条动态状态画像（表上有唯一约束）；补剂使用与咨询只读 current，
    # 让“已停用”替换旧的“正在使用”，不把历史状态重复计入当前分布。
    state_json_by_case = {
        state_profile.service_case_id: state_profile.state_json or {}
        for state_profile in await session.scalars(
            select(UserStateProfile).where(UserStateProfile.service_case_id.in_(case_ids))
        )
    }

    # 每个 case 最新入营问卷提交
    latest_submission: dict[int, QuestionnaireSubmission] = {}
    for submission in await session.scalars(
        select(QuestionnaireSubmission)
        .where(
            QuestionnaireSubmission.service_case_id.in_(case_ids),
            QuestionnaireSubmission.template_key == "intake",
        )
        .order_by(QuestionnaireSubmission.service_case_id, QuestionnaireSubmission.created_at.desc())
        .distinct(QuestionnaireSubmission.service_case_id)
    ):
        latest_submission[submission.service_case_id] = submission

    fields = (
        questionnaire_fields
        if questionnaire_fields is not None
        else await get_active_questionnaire_fields(session, "intake")
    )

    case_labels: dict[int, dict[str, str | list[str]]] = {}
    for cid in case_ids:
        has_ai_profile = cid in latest_profile_json
        profile_json = latest_profile_json.get(cid) or {}
        risk_level = risk_by_case.get(cid)
        # 客户分型和最终减重带依赖 AI 画像；没有画像时保留为『未采集』，不混入 BMI 初判。
        ctype = profile_json.get("customer_type") if has_ai_profile else None
        if has_ai_profile and (not isinstance(ctype, str) or not ctype.strip()):
            ctype = customer_type_for_profile(profile_json, risk_level)
        band = (
            _band_short(weight_loss_band_from_profile(profile_json) or "未采集")
            if has_ai_profile
            else "未采集"
        )

        submission = latest_submission.get(cid)
        facts = build_intake_fact_summary(fields, submission.answers_json) if submission else {}
        answers = submission.answers_json if submission and isinstance(submission.answers_json, dict) else {}
        numeric_values = {
            "age": _num(facts.get("age")),
            "bmi": _num(facts.get("bmi")),
            "bodyfat": _num(facts.get("bodyfat")),
        }
        waist = _num(facts.get("waist"))
        height = _num(facts.get("height_cm"))
        numeric_values["waist_height_ratio"] = (
            round(waist / height, 3) if waist and height else None
        )

        province, city = match_city(facts.get("city"))
        labels: dict[str, str | list[str]] = {
            "customer_type": _category_label(ctype),
            "weight_loss_band": _category_label(band),
            "city": _category_label(city),
            "province": _category_label(province),
            "main_need": _questionnaire_portrait_labels(
                fields, answers, "main_need", "最想先解决"
            ),
            "expected_change": _questionnaire_portrait_labels(
                fields, answers, "expected_change", "最希望 28 天后"
            ),
            "diet_pattern": _single_questionnaire_portrait_label(
                fields, answers, "diet_pattern", "长期饮食模式"
            ),
            "sleep_problem": _questionnaire_portrait_labels(
                fields, answers, "sleep_problem", "常见的睡眠问题"
            ),
            "sleep_quality": _single_questionnaire_portrait_label(
                fields, answers, "sleep_quality", "睡眠质量的主观评价"
            ),
            "supplement_habit": _supplement_portrait_labels(state_json_by_case.get(cid)),
            "supplement_category": _supplement_category_portrait_labels(
                state_json_by_case.get(cid)
            ),
        }
        labels.update(health_profile_dimension_labels(health_by_case.get(cid)))
        for dim in _PORTRAIT_DIMENSIONS:
            if dim["numeric"]:
                labels[dim["key"]] = _bucket_numeric(
                    numeric_values[dim["key"]], dim["edges"], dim["labels"]
                )
        case_labels[cid] = labels

    return len(case_ids), case_labels


def _questionnaire_portrait_labels(
    fields: list[dict[str, Any]],
    answers: dict[str, Any],
    dimension_key: str,
    *title_parts: str,
) -> list[str]:
    aliases = _PORTRAIT_OPTION_LABELS.get(dimension_key, {})
    return [
        aliases.get(value, value)
        for value in structured_answer_values(fields, answers, *title_parts)
        if value
    ]


def _single_questionnaire_portrait_label(
    fields: list[dict[str, Any]],
    answers: dict[str, Any],
    dimension_key: str,
    *title_parts: str,
) -> str:
    values = _questionnaire_portrait_labels(
        fields, answers, dimension_key, *title_parts
    )
    return _category_label(values[0] if values else None)


def _questionnaire_portrait_option_order(
    fields: list[dict[str, Any]],
    dimension_key: str,
    *title_parts: str,
) -> list[str]:
    """读取当前启用问卷的原始选项顺序，并转换为群体画像展示标签。"""

    aliases = _PORTRAIT_OPTION_LABELS.get(dimension_key, {})
    for field in fields:
        title = str(field.get("title") or field.get("label") or "")
        if not all(part in title for part in title_parts):
            continue
        options = field.get("options")
        if not isinstance(options, list):
            return []
        labels: list[str] = []
        for option in options:
            value = str(option).strip()
            label = aliases.get(value, value)
            if label and label not in labels:
                labels.append(label)
        return labels
    return []


def _supplement_current_facts(state_json: Any) -> list[dict[str, Any]]:
    """从动态画像取出当前补剂事实列表（供明细与品类两个维度共用）。"""

    state = state_json if isinstance(state_json, dict) else {}
    domains = state.get("domains") if isinstance(state.get("domains"), dict) else {}
    habits = domains.get("lifestyle_habits") if isinstance(domains.get("lifestyle_habits"), dict) else {}
    current = habits.get("current") if isinstance(habits.get("current"), dict) else {}
    current_value = current.get("value") if isinstance(current.get("value"), dict) else {}
    raw_facts = current_value.get("supplement_habit")
    facts = raw_facts if isinstance(raw_facts, list) else [raw_facts]
    return [fact for fact in facts if isinstance(fact, dict)]


def _supplement_portrait_labels(state_json: Any) -> list[str]:
    """把动态画像中当前补剂事实转成群体画像分桶标签。

    具体补剂名和使用状态由档案官结构化工具输出，这里不从自由文本
    或关键词反向猜测；缺少结构化字段的旧数据按未采集处理。
    """

    labels: list[str] = []
    for fact in _supplement_current_facts(state_json):
        supplement_name = str(fact.get("supplement_name") or "").strip()
        status_label = _SUPPLEMENT_STATUS_LABELS.get(str(fact.get("habit_status") or "").strip())
        if not supplement_name or not status_label:
            continue
        label = f"{supplement_name} · {status_label}"
        if label not in labels:
            labels.append(label)
    return labels


def _supplement_category_portrait_labels(state_json: Any) -> list[str]:
    """把当前补剂事实按品类去重成群体画像分桶标签（每单每品类计一次）。

    品类由档案官结构化字段 supplement_category 决定；缺该字段的旧数据
    归入『其他补剂』，不从补剂名反向猜测品类。
    """

    labels: list[str] = []
    for fact in _supplement_current_facts(state_json):
        supplement_name = str(fact.get("supplement_name") or "").strip()
        if not supplement_name:
            continue
        category = str(fact.get("supplement_category") or "").strip()
        label = _SUPPLEMENT_CATEGORY_LABELS.get(category, _SUPPLEMENT_CATEGORY_LABELS["other"])
        if label not in labels:
            labels.append(label)
    return labels


def _build_geography(case_labels: dict[int, dict[str, str | list[str]]]) -> PopulationGeography:
    """从逐 case 城市标签构造省→市树；未匹配地址单独计数，绝不静默丢弃。"""
    cities_by_province: dict[str, dict[str, int]] = {}
    unknown = 0
    for labels in case_labels.values():
        province = labels.get("province", UNKNOWN_LABEL)
        city = labels.get("city", UNKNOWN_LABEL)
        if not isinstance(province, str) or not isinstance(city, str):
            unknown += 1
            continue
        if province == UNKNOWN_LABEL or city == UNKNOWN_LABEL:
            unknown += 1
            continue
        province_cities = cities_by_province.setdefault(province, {})
        province_cities[city] = province_cities.get(city, 0) + 1

    provinces = [
        PopulationGeoProvince(
            province=province,
            count=sum(cities.values()),
            cities=[
                PopulationGeoCity(city=city, count=count)
                for city, count in sorted(cities.items(), key=lambda item: (-item[1], item[0]))
            ],
        )
        for province, cities in cities_by_province.items()
    ]
    provinces.sort(key=lambda item: (-item.count, item.province))
    return PopulationGeography(
        total=sum(province.count for province in provinces), unknown=unknown, provinces=provinces
    )


def _build_distribution(
    dim: dict[str, Any],
    case_labels: dict[int, dict[str, str | list[str]]],
    *,
    ordered_labels: list[str] | None = None,
) -> PopulationDistribution:
    """从逐-case 标签统计单个维度的分布。

    数值维度按固定区间顺序（含 0 桶）；有业务梯度的问卷维度按问卷顺序；
    其余分类维度仍按计数降序。
    """
    counts: dict[str, int] = {}
    unknown = 0
    valid_cases = 0
    for labels in case_labels.values():
        value = labels.get(dim["key"], UNKNOWN_LABEL)
        if dim.get("multi_select"):
            values = list(dict.fromkeys(item for item in value if item)) if isinstance(value, list) else []
            if not values:
                unknown += 1
                continue
            valid_cases += 1
            for item in values:
                counts[item] = counts.get(item, 0) + 1
            continue
        if value == UNKNOWN_LABEL:
            unknown += 1
            continue
        if not isinstance(value, str):
            unknown += 1
            continue
        valid_cases += 1
        counts[value] = counts.get(value, 0) + 1
    if dim["numeric"]:
        buckets = [
            PopulationDistributionBucket(label=name, count=counts.get(name, 0))
            for name in dim["labels"]
        ]
    elif ordered_labels is not None:
        ordered_set = set(ordered_labels)
        names = [name for name in ordered_labels if name in counts]
        names.extend(
            name
            for name, _ in sorted(counts.items(), key=lambda kv: -kv[1])
            if name not in ordered_set
        )
        buckets = [
            PopulationDistributionBucket(label=name, count=counts[name])
            for name in names
        ]
    else:
        buckets = [
            PopulationDistributionBucket(label=name, count=count)
            for name, count in sorted(counts.items(), key=lambda kv: -kv[1])
        ]
    return PopulationDistribution(
        key=dim["key"],
        label=dim["label"],
        description=dim["description"],
        multi_select=bool(dim.get("multi_select")),
        total=valid_cases,
        unknown=unknown,
        buckets=buckets,
    )


def _num(value: Any) -> float | None:
    """从事实字段（可能是 '34岁' / '21.2' / '未提供' / '暂无数据'）解析出数值。"""
    if value is None:
        return None
    import re

    match = re.search(r"\d+(?:\.\d+)?", str(value))
    return float(match.group()) if match else None


def _band_short(band: str) -> str:
    """把完整带位名压成短标签，便于分布展示。"""
    if band == "未采集":
        return "未采集"
    for tag in ("增肌增能带", "A带", "B带", "C带", "D带"):
        if tag in band:
            return "增肌增能带" if tag == "增肌增能带" else tag
    return "待完善"


def _category_label(value: str | None) -> str:
    """分类维度的单值归一：空/历史哨兵值 → UNKNOWN_LABEL，否则原样（去空白）。"""
    text = (value or "").strip()
    return UNKNOWN_LABEL if text in _CATEGORY_UNKNOWNS else text


def _bucket_numeric(value: float | None, edges: list[float], labels: list[str]) -> str:
    """把数值按升序 edges 左闭分桶为标签；len(labels) 必须为 len(edges)+1。缺失 → UNKNOWN_LABEL。"""
    if value is None:
        return UNKNOWN_LABEL
    index = len(edges)
    for i, edge in enumerate(edges):
        if value < edge:
            index = i
            break
    return labels[index]


@router.get("/emotional-alerts", response_model=list[EmotionalAlertCaseItem])
async def dashboard_emotional_alerts(
    session: AsyncSession = Depends(get_session),
) -> list[EmotionalAlertCaseItem]:
    today_start, today_end = _today_bounds()

    rows = (
        await session.execute(
            select(Message, ServiceCase, User)
            .join(ServiceCase, Message.service_case_id == ServiceCase.id)
            .outerjoin(User, ServiceCase.user_id == User.id)
            .where(
                _stats_case_condition(),
                Message.created_at >= today_start,
                Message.created_at < today_end,
                Message.direction == MessageDirection.INBOUND,
                Message.sender_type == MessageSenderType.USER,
            )
            .order_by(Message.created_at.desc(), Message.id.desc())
        )
    ).all()

    cases: dict[int, EmotionalAlertCaseItem] = {}
    for message, service_case, user in rows:
        if not _has_emotion_signal(message):
            continue
        item = cases.get(service_case.id)
        if item is None:
            item = EmotionalAlertCaseItem(
                service_case_id=service_case.id,
                case_no=service_case.case_no,
                user_id=user.id if user else None,
                user_name=user.name if user else None,
                group_id=service_case.group_id,
                group_name=service_case.group_name,
                messages=[],
            )
            cases[service_case.id] = item
        item.messages.append(_build_emotional_alert_message(message))
    return list(cases.values())


@router.get("/attention-events", response_model=list[DashboardCaseMessageEvents])
async def dashboard_attention_events(
    session: AsyncSession = Depends(get_session),
) -> list[DashboardCaseMessageEvents]:
    """今日触发转人工/@班主任 的事件：以入站用户消息(触发点)为主，附带 @班主任/AI 回复。"""
    today_start, today_end = _today_bounds()
    rows = (
        await session.execute(
            select(Message, ServiceCase, User)
            .join(ServiceCase, Message.service_case_id == ServiceCase.id)
            .outerjoin(User, ServiceCase.user_id == User.id)
            .where(
                _stats_case_condition(),
                Message.created_at >= today_start,
                Message.created_at < today_end,
                Message.direction == MessageDirection.INBOUND,
                Message.sender_type == MessageSenderType.USER,
                _requires_human_intervention_expr(),
            )
            .order_by(Message.created_at.desc(), Message.id.desc())
        )
    ).all()

    # 按 trigger_id 找每个触发点对应的出站回复(@班主任 handoff / AI 回复)。
    trigger_ids = [trigger.id for trigger, _, _ in rows]
    replies: dict[int, Message] = {}
    if trigger_ids:
        for reply in await session.scalars(
            select(Message)
            .where(
                Message.trigger_id.in_(trigger_ids),
                Message.direction == MessageDirection.OUTBOUND,
            )
            .order_by(Message.id.asc())
        ):
            if reply.trigger_id is not None:
                replies[reply.trigger_id] = reply  # 同一触发点多条时取最新一条

    cases: dict[int, DashboardCaseMessageEvents] = {}
    for trigger, service_case, user in rows:
        detail = _attention_event_detail(trigger)
        reply = replies.get(trigger.id)
        event = DashboardMessageEvent(
            event_id=f"attention-{trigger.id}",
            message_id=trigger.id,
            content=trigger.content,
            occurred_at=trigger.created_at,
            reason=detail.get("reason"),
            evidence=detail.get("evidence"),
            level=detail.get("level"),
            ai_reply_message_id=reply.id if reply else None,
            ai_reply_content=reply.content if reply else None,
            ai_reply_created_at=reply.created_at if reply else None,
        )
        _append_case_event(cases, service_case=service_case, user=user, event=event)
    return list(cases.values())


def _risk_reasons(service_case: ServiceCase) -> list[DashboardRiskReason]:
    """风险原因：优先 risk_flags 里 level in {high,medium}，为空则回退 intervention_reasons。"""
    sources: list[dict] = []
    if isinstance(service_case.risk_flags, list):
        sources = [
            item
            for item in service_case.risk_flags
            if isinstance(item, dict) and item.get("level") in {"high", "medium"}
        ]
    if not sources and isinstance(service_case.intervention_reasons, list):
        sources = [item for item in service_case.intervention_reasons if isinstance(item, dict)]
    reasons: list[DashboardRiskReason] = []
    seen: set[tuple[str | None, str | None]] = set()
    for item in sources:
        reason = _string_or_none(item.get("reason") or item.get("label"))
        evidence = _string_or_none(item.get("evidence"))
        key = (reason, evidence)
        if key in seen:
            continue
        seen.add(key)
        reasons.append(
            DashboardRiskReason(level=_string_or_none(item.get("level")), reason=reason, evidence=evidence)
        )
    return reasons


@router.get("/risk-cases", response_model=list[DashboardCaseInsightItem])
async def dashboard_risk_cases(
    session: AsyncSession = Depends(get_session),
) -> list[DashboardCaseInsightItem]:
    """高风险服务单：展示具体风险原因 + 用户/服务单。"""
    rows = (
        await session.execute(
            select(ServiceCase, User)
            .outerjoin(User, ServiceCase.user_id == User.id)
            .where(_stats_case_condition(), ServiceCase.risk_level == "high")
            .order_by(ServiceCase.updated_at.desc(), ServiceCase.id.desc())
        )
    ).all()
    return [
        DashboardCaseInsightItem(
            case=_case_ref(service_case, user),
            risk_level=service_case.risk_level,
            reasons=_risk_reasons(service_case),
        )
        for service_case, user in rows
    ]


async def _idle_cases(
    session: AsyncSession,
    *,
    min_days: int,
    max_days: int | None,
) -> list[DashboardCaseInsightItem]:
    """服务中、且用户本人最近发言距今 [min_days, max_days) 天的服务单（含闲置天数）。"""
    now = datetime.now(timezone.utc)
    last_message_subq = last_user_inbound_subquery()
    last_activity_at = func.coalesce(last_message_subq.c.last_at, ServiceCase.created_at)
    conditions = [
        _stats_case_condition(),
        ServiceCase.status == ServiceCaseStatus.IN_SERVICE,
        last_activity_at < now - timedelta(days=min_days),
    ]
    if max_days is not None:
        conditions.append(last_activity_at >= now - timedelta(days=max_days))
    rows = (
        await session.execute(
            select(ServiceCase, User, last_message_subq.c.last_at)
            .outerjoin(User, ServiceCase.user_id == User.id)
            .outerjoin(last_message_subq, last_message_subq.c.service_case_id == ServiceCase.id)
            .where(*conditions)
            .order_by(last_activity_at.asc(), ServiceCase.id.desc())
        )
    ).all()
    # 系统这一轮沉默里已经自动召回过几次：让运营一眼看出机器已经动过手，别再重复跟一遍。
    recalls_by_case = await _churn_recalls_by_case(
        session, [service_case.id for service_case, _, _ in rows]
    )
    items: list[DashboardCaseInsightItem] = []
    for service_case, user, last_at in rows:
        basis = last_at or service_case.created_at
        if basis is not None and basis.tzinfo is None:
            basis = basis.replace(tzinfo=timezone.utc)
        idle_days = (now - basis).days if basis is not None else None
        # 只数「最后一次用户发言之后」的召回，与调度器的计数口径保持一致。
        recalls = [
            stamp
            for stamp in recalls_by_case.get(service_case.id, [])
            if basis is None or stamp > basis
        ]
        items.append(
            DashboardCaseInsightItem(
                case=_case_ref(service_case, user),
                risk_level=service_case.risk_level,
                idle_days=idle_days,
                last_inbound_at=last_at,
                recall_count=len(recalls),
                last_recall_at=max(recalls) if recalls else None,
            )
        )
    return items


async def _churn_recalls_by_case(
    session: AsyncSession, case_ids: list[int]
) -> dict[int, list[datetime]]:
    """一次取回这批服务单的全部召回时间戳，避免逐行查库。"""
    if not case_ids:
        return {}
    rows = (
        await session.execute(
            select(ReminderEvent.service_case_id, ReminderEvent.created_at).where(
                ReminderEvent.service_case_id.in_(case_ids),
                ReminderEvent.rule_id == CHURN_RECALL_RULE_ID,
            )
        )
    ).all()
    grouped: dict[int, list[datetime]] = {}
    for case_id, created_at in rows:
        if created_at is not None and created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        grouped.setdefault(case_id, []).append(created_at)
    return grouped


@router.get("/unfollowed-cases", response_model=list[DashboardCaseInsightItem])
async def dashboard_unfollowed_cases(
    session: AsyncSession = Depends(get_session),
) -> list[DashboardCaseInsightItem]:
    return await _idle_cases(session, min_days=2, max_days=7)


@router.get("/lost-cases", response_model=list[DashboardCaseInsightItem])
async def dashboard_lost_cases(
    session: AsyncSession = Depends(get_session),
) -> list[DashboardCaseInsightItem]:
    return await _idle_cases(session, min_days=7, max_days=None)


@router.get("/renewal-followup-cases", response_model=list[DashboardCaseInsightItem])
async def dashboard_renewal_followup_cases(
    session: AsyncSession = Depends(get_session),
) -> list[DashboardCaseInsightItem]:
    """续购跟进窗口：已服务 18-28 天的服务单（含服务天数）。"""
    now = datetime.now(timezone.utc)
    china_tz = ZoneInfo("Asia/Shanghai")
    today = now.astimezone(china_tz).date()
    rows = (
        await session.execute(
            select(ServiceCase, User)
            .outerjoin(User, ServiceCase.user_id == User.id)
            .where(
                _stats_case_condition(),
                ServiceCase.status == ServiceCaseStatus.IN_SERVICE,
                ServiceCase.started_at.is_not(None),
                ServiceCase.started_at <= now - timedelta(days=18),
                ServiceCase.started_at >= now - timedelta(days=28),
            )
            .order_by(ServiceCase.started_at.asc(), ServiceCase.id.desc())
        )
    ).all()
    return [
        DashboardCaseInsightItem(
            case=_case_ref(service_case, user),
            risk_level=service_case.risk_level,
            service_day=_case_service_day(service_case, today, china_tz),
            started_at=service_case.started_at,
        )
        for service_case, user in rows
    ]


@router.get("/intent-events/{intent_type}", response_model=list[DashboardCaseMessageEvents])
async def dashboard_intent_events(
    intent_type: str,
    session: AsyncSession = Depends(get_session),
) -> list[DashboardCaseMessageEvents]:
    if intent_type not in {"renewal", "refund"}:
        return []
    flag_column = ServiceCase.renewal_intent_flags if intent_type == "renewal" else ServiceCase.refund_intent_flags
    rows = (
        await session.execute(
            select(ServiceCase, User)
            .outerjoin(User, ServiceCase.user_id == User.id)
            .where(_stats_case_condition(), flag_column.is_not(None))
            .order_by(ServiceCase.updated_at.desc(), ServiceCase.id.desc())
        )
    ).all()
    case_ids = [service_case.id for service_case, _ in rows]
    messages_by_case: dict[int, list[Message]] = {case_id: [] for case_id in case_ids}
    if case_ids:
        messages = await session.scalars(
            select(Message)
            .where(
                Message.service_case_id.in_(case_ids),
                Message.direction == MessageDirection.INBOUND,
                Message.sender_type == MessageSenderType.USER,
            )
            .order_by(Message.created_at.desc(), Message.id.desc())
        )
        for message in messages:
            if _message_has_intent(message, intent_type):
                messages_by_case.setdefault(message.service_case_id or 0, []).append(message)

    grouped: dict[int, DashboardCaseMessageEvents] = {}
    for service_case, user in rows:
        flags = service_case.renewal_intent_flags if intent_type == "renewal" else service_case.refund_intent_flags
        events = _intent_events_from_messages(messages_by_case.get(service_case.id, []), intent_type)
        if not events:
            events = _intent_events_from_flags(flags or [], intent_type)
        for event in events:
            _append_case_event(grouped, service_case=service_case, user=user, event=event)
    return list(grouped.values())


@router.get("/today-messages", response_model=list[DashboardCaseMessageEvents])
async def dashboard_today_messages(
    session: AsyncSession = Depends(get_session),
) -> list[DashboardCaseMessageEvents]:
    today_start, today_end = _today_bounds()
    rows = (
        await session.execute(
            select(Message, ServiceCase, User)
            .join(ServiceCase, Message.service_case_id == ServiceCase.id)
            .outerjoin(User, ServiceCase.user_id == User.id)
            .where(
                _stats_case_condition(),
                Message.created_at >= today_start,
                Message.created_at < today_end,
                Message.direction == MessageDirection.INBOUND,
                Message.sender_type == MessageSenderType.USER,
            )
            .order_by(Message.created_at.desc(), Message.id.desc())
        )
    ).all()
    grouped: dict[int, DashboardCaseMessageEvents] = {}
    for message, service_case, user in rows:
        _append_case_event(
            grouped,
            service_case=service_case,
            user=user,
            event=DashboardMessageEvent(
                event_id=f"message-{message.id}",
                message_id=message.id,
                content=message.content,
                occurred_at=message.created_at,
            ),
        )
    return list(grouped.values())


@router.get("/today-checkins", response_model=list[DashboardCaseCheckins])
async def dashboard_today_checkins(
    session: AsyncSession = Depends(get_session),
) -> list[DashboardCaseCheckins]:
    today = _today_bounds()[0].astimezone(ZoneInfo("Asia/Shanghai")).date()
    rows = (
        await session.execute(
            select(Checkin, ServiceCase, User)
            .join(ServiceCase, Checkin.service_case_id == ServiceCase.id)
            .outerjoin(User, ServiceCase.user_id == User.id)
            .where(
                _stats_case_condition(),
                Checkin.deleted_at.is_(None),
                Checkin.checkin_date == today,
            )
            .order_by(Checkin.created_at.desc(), Checkin.id.desc())
        )
    ).all()
    grouped: dict[int, DashboardCaseCheckins] = {}
    for checkin, service_case, user in rows:
        item = grouped.get(service_case.id)
        if item is None:
            item = DashboardCaseCheckins(case=_case_ref(service_case, user), checkins=[])
            grouped[service_case.id] = item
        item.checkins.append(
            DashboardCheckinEvent(
                checkin_id=checkin.id,
                checkin_date=checkin.checkin_date.isoformat(),
                content=checkin.content,
                parsed_json=checkin.parsed_json,
                is_complete=is_complete_checkin(checkin),
                created_at=checkin.created_at,
            )
        )
    return list(grouped.values())


@router.get("/pending-documents", response_model=list[DashboardCasePendingDocuments])
async def dashboard_pending_documents(
    session: AsyncSession = Depends(get_session),
) -> list[DashboardCasePendingDocuments]:
    """审核控制台的待审文件队列：**全量**，不按新老群过滤。

    这是待办清单而不是统计口径。新老群隔离(_stats_case_condition)的既定意图只覆盖
    "看板与群体画像"——老群照常被服务，它们的待审文件运营必须看得到。队列一旦沿用统计
    过滤，任何被标成老群的服务单，其待审文件就会静默消失：不报错、不提示、红点也不计数。

    2026-08-16 三位新客户正是这样被隐藏的：ingress 跑的旧代码没写"提交问卷即标记新群"，
    三个单子留在 legacy，6 份文件在控制台完全不可见，直到 8/17 客户主动来问。
    """
    from sqlalchemy import text

    latest_doc_ids = (
        select(UserDocument.id)
        .distinct(
            UserDocument.service_case_id,
            UserDocument.document_type,
            text("content_json->>'stage'"),
        )
        .join(ServiceCase, UserDocument.service_case_id == ServiceCase.id)
        .where(
            active_service_case_condition(),
            UserDocument.document_type.in_(ACTIVE_DOCUMENT_TYPES),
        )
        .order_by(
            UserDocument.service_case_id,
            UserDocument.document_type,
            text("content_json->>'stage'"),
            UserDocument.created_at.desc(),
        )
        .subquery()
    )
    rows = (
        await session.execute(
            select(UserDocument, ServiceCase, User)
            .join(ServiceCase, UserDocument.service_case_id == ServiceCase.id)
            .outerjoin(User, ServiceCase.user_id == User.id)
            .where(
                UserDocument.id.in_(select(latest_doc_ids.c.id)),
                UserDocument.status.in_([DocumentStatus.DRAFT, DocumentStatus.REVIEWING]),
            )
            .order_by(UserDocument.updated_at.desc(), UserDocument.id.desc())
        )
    ).all()
    grouped: dict[int, DashboardCasePendingDocuments] = {}
    for document, service_case, user in rows:
        item = grouped.get(service_case.id)
        if item is None:
            item = DashboardCasePendingDocuments(case=_case_ref(service_case, user), documents=[])
            grouped[service_case.id] = item
        internal_review = (
            document.content_json.get("internal_review")
            if isinstance(document.content_json, dict)
            else None
        )
        internal_review = internal_review if isinstance(internal_review, dict) else {}
        reasons = internal_review.get("reasons")
        item.documents.append(
            DashboardPendingDocument(
                document_id=document.id,
                document_type=document.document_type.value,
                title=document.title,
                status=document.status.value,
                created_at=document.created_at,
                updated_at=document.updated_at,
                manual_review_required=bool(internal_review.get("manual_review_required")),
                manual_review_reasons=[
                    str(reason).strip() for reason in reasons if str(reason).strip()
                ]
                if isinstance(reasons, list)
                else [],
            )
        )
    return list(grouped.values())


@router.get("/ai-costs", response_model=list[AICostCaseItem])
async def dashboard_ai_costs(
    session: AsyncSession = Depends(get_session),
) -> list[AICostCaseItem]:
    total_cost_expr = func.coalesce(func.sum(AIUsageLog.estimated_cost_yuan), 0.0)
    vision_cost_expr = func.coalesce(
        func.sum(AIUsageLog.estimated_cost_yuan).filter(AIUsageLog.scenario == "qwen_vl_translate"),
        0.0,
    )
    deepseek_cost_expr = func.coalesce(
        func.sum(AIUsageLog.estimated_cost_yuan).filter(AIUsageLog.scenario != "qwen_vl_translate"),
        0.0,
    )
    rows = await session.execute(
        select(
            ServiceCase.id,
            ServiceCase.case_no,
            ServiceCase.user_id,
            User.name,
            ServiceCase.group_id,
            func.count(AIUsageLog.id),
            func.coalesce(func.sum(AIUsageLog.total_tokens), 0),
            func.coalesce(func.sum(AIUsageLog.prompt_tokens), 0),
            func.coalesce(func.sum(AIUsageLog.completion_tokens), 0),
            total_cost_expr,
            deepseek_cost_expr,
            vision_cost_expr,
            func.max(AIUsageLog.created_at),
        )
        .join(ServiceCase, AIUsageLog.service_case_id == ServiceCase.id)
        .outerjoin(User, ServiceCase.user_id == User.id)
        .where(_stats_case_condition(), AIUsageLog.is_diagnostic.is_(False))
        .group_by(ServiceCase.id, ServiceCase.case_no, ServiceCase.user_id, User.name, ServiceCase.group_id)
        .order_by(total_cost_expr.desc(), func.max(AIUsageLog.created_at).desc())
    )
    return [
        AICostCaseItem(
            service_case_id=service_case_id,
            case_no=case_no,
            user_id=user_id,
            user_name=user_name,
            group_id=group_id,
            total_calls=total_calls or 0,
            total_tokens=total_tokens or 0,
            prompt_tokens=prompt_tokens or 0,
            completion_tokens=completion_tokens or 0,
            estimated_cost_yuan=round(float(cost or 0), 6),
            deepseek_cost_yuan=round(float(deepseek_cost or 0), 6),
            vision_cost_yuan=round(float(vision_cost or 0), 6),
            last_call_at=last_call_at,
        )
        for (
            service_case_id,
            case_no,
            user_id,
            user_name,
            group_id,
            total_calls,
            total_tokens,
            prompt_tokens,
            completion_tokens,
            cost,
            deepseek_cost,
            vision_cost,
            last_call_at,
        ) in rows
    ]


def _has_emotion_signal(message: Message) -> bool:
    analysis = message.analysis_json if isinstance(message.analysis_json, dict) else None
    if analysis is not None:
        emotion_signal = analysis.get("emotion_signal")
        if isinstance(emotion_signal, dict):
            return bool(emotion_signal.get("detected"))
        return _analysis_has_emotion_code(analysis)
    return False


def _case_ref(service_case: ServiceCase, user: User | None) -> DashboardCaseRef:
    return DashboardCaseRef(
        service_case_id=service_case.id,
        case_no=service_case.case_no,
        user_id=user.id if user else None,
        user_name=user.name if user else None,
        user_phone=user.phone if user else None,
        group_id=service_case.group_id,
        group_name=service_case.group_name,
    )


def _append_case_event(
    grouped: dict[int, DashboardCaseMessageEvents],
    *,
    service_case: ServiceCase,
    user: User | None,
    event: DashboardMessageEvent,
) -> None:
    item = grouped.get(service_case.id)
    if item is None:
        item = DashboardCaseMessageEvents(case=_case_ref(service_case, user), events=[])
        grouped[service_case.id] = item
    item.events.append(event)


def _attention_event_detail(message: Message | None) -> dict[str, str | None]:
    if message is None:
        return {}
    analysis = message.analysis_json if isinstance(message.analysis_json, dict) else None
    if analysis is None:
        return {}
    for key in ("intervention_reasons", "risk_flags"):
        values = analysis.get(key)
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, dict):
                continue
            return {
                "level": _string_or_none(item.get("level")),
                "reason": _string_or_none(item.get("reason")),
                "evidence": _string_or_none(item.get("evidence")),
            }
    return {}


def _message_has_intent(message: Message, intent_type: str) -> bool:
    analysis = message.analysis_json if isinstance(message.analysis_json, dict) else None
    if analysis is None:
        return False
    key = "renewal_intent" if intent_type == "renewal" else "refund_intent"
    value = analysis.get(key)
    return isinstance(value, dict) and bool(value.get("detected"))


def _intent_events_from_messages(messages: list[Message], intent_type: str) -> list[DashboardMessageEvent]:
    key = "renewal_intent" if intent_type == "renewal" else "refund_intent"
    events: list[DashboardMessageEvent] = []
    for message in messages:
        analysis = message.analysis_json if isinstance(message.analysis_json, dict) else None
        value = analysis.get(key) if analysis else None
        if not isinstance(value, dict) or not value.get("detected"):
            continue
        events.append(
            DashboardMessageEvent(
                event_id=f"{intent_type}-message-{message.id}",
                message_id=message.id,
                content=message.content,
                occurred_at=message.created_at,
                reason=_string_or_none(value.get("reason")),
                evidence=_string_or_none(value.get("evidence")),
            )
        )
    return events


def _intent_events_from_flags(flags: list[dict], intent_type: str) -> list[DashboardMessageEvent]:
    events: list[DashboardMessageEvent] = []
    seen: set[tuple[str | None, str | None]] = set()
    for index, flag in enumerate(flags):
        if not isinstance(flag, dict) or not flag.get("detected", True):
            continue
        reason = _string_or_none(flag.get("reason"))
        evidence = _string_or_none(flag.get("evidence"))
        key = (reason, evidence)
        if key in seen:
            continue
        seen.add(key)
        events.append(
            DashboardMessageEvent(
                event_id=f"{intent_type}-flag-{index}",
                content=evidence or reason or "已标记意向，但没有保存原话证据。",
                reason=reason,
                evidence=evidence,
            )
        )
    return events


def _build_emotional_alert_message(message: Message) -> EmotionalAlertMessage:
    detail = _emotion_signal_detail(message)
    return EmotionalAlertMessage(
        message_id=message.id,
        content=message.content,
        detected_at=message.created_at,
        level=detail.get("level"),
        reason=detail.get("reason"),
        evidence=detail.get("evidence"),
    )


def _emotion_signal_detail(message: Message) -> dict[str, str | None]:
    analysis = message.analysis_json if isinstance(message.analysis_json, dict) else None
    if analysis is None:
        return {}
    emotion_signal = analysis.get("emotion_signal")
    if isinstance(emotion_signal, dict):
        return {
            "level": _string_or_none(emotion_signal.get("level")),
            "reason": _string_or_none(emotion_signal.get("reason")),
            "evidence": _string_or_none(emotion_signal.get("evidence")),
        }
    for key in ("intervention_reasons", "risk_flags"):
        values = analysis.get(key)
        if not isinstance(values, list):
            continue
        for item in values:
            if isinstance(item, dict) and item.get("code") in {"emotional_distress", "depression", "self_harm"}:
                return {
                    "level": _string_or_none(item.get("level")),
                    "reason": _string_or_none(item.get("label") or item.get("reason")),
                    "evidence": _string_or_none(item.get("evidence")),
                }
    return {}


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


async def _build_ai_cost_summary(session: AsyncSession) -> AICostSummary:
    total_cost_expr = func.coalesce(func.sum(AIUsageLog.estimated_cost_yuan), 0.0)
    vision_cost_expr = func.coalesce(
        func.sum(AIUsageLog.estimated_cost_yuan).filter(AIUsageLog.scenario == "qwen_vl_translate"),
        0.0,
    )
    deepseek_cost_expr = func.coalesce(
        func.sum(AIUsageLog.estimated_cost_yuan).filter(AIUsageLog.scenario != "qwen_vl_translate"),
        0.0,
    )
    row = await session.execute(
        select(
            func.count(AIUsageLog.id),
            func.coalesce(func.sum(AIUsageLog.total_tokens), 0),
            func.coalesce(func.sum(AIUsageLog.prompt_tokens), 0),
            func.coalesce(func.sum(AIUsageLog.completion_tokens), 0),
            total_cost_expr,
            deepseek_cost_expr,
            vision_cost_expr,
        )
        .outerjoin(ServiceCase, AIUsageLog.service_case_id == ServiceCase.id)
        .where(
            AIUsageLog.is_diagnostic.is_(False),
            (AIUsageLog.service_case_id.is_(None)) | _stats_case_condition(),
        )
    )
    total_calls, total_tokens, prompt_tokens, completion_tokens, cost, deepseek_cost, vision_cost = row.one()
    return AICostSummary(
        total_calls=total_calls or 0,
        total_tokens=total_tokens or 0,
        prompt_tokens=prompt_tokens or 0,
        completion_tokens=completion_tokens or 0,
        estimated_cost_yuan=round(float(cost or 0), 6),
        deepseek_cost_yuan=round(float(deepseek_cost or 0), 6),
        vision_cost_yuan=round(float(vision_cost or 0), 6),
    )


def _analysis_has_emotion_code(analysis: dict) -> bool:
    emotion_codes = {"emotional_distress", "depression", "self_harm"}
    for key in ("intervention_reasons", "risk_flags"):
        values = analysis.get(key)
        if not isinstance(values, list):
            continue
        for item in values:
            if isinstance(item, dict) and item.get("code") in emotion_codes:
                return True
    return False

async def _count_flagged_cases(session: AsyncSession, condition) -> int:
    count = await session.scalar(
        select(func.count(ServiceCase.id)).where(
            _stats_case_condition(),
            condition,
        )
    )
    return count or 0


async def _build_outcome_summary(
    session: AsyncSession,
    *,
    total_cases: int,
    renewal_intent_users: int,
    refund_intent_users: int,
    now: datetime,
) -> OutcomeDashboardSummary:
    active_cases = total_cases
    day7_eligible = await _eligible_case_count(session, now=now, day=7)
    day28_eligible = await _eligible_case_count(session, now=now, day=28)

    profile_cases = await _distinct_active_case_count(session, UserProfile.service_case_id)
    core_document_cases = await _core_document_case_count(session)
    day7_snapshots = await _snapshot_case_count(session, milestone="day7", now=now, day=7)
    day28_complete_checkins = await _checkin_completion_case_count(session, now=now, day=28, minimum_days=20)
    result_signal_cases = await _outcome_event_case_count(
        session,
        {
            "weight_change",
            "waist_or_body_change",
            "weight_result",
            "waist_result",
            "challenge_goal_result",
        },
    )
    body_feedback_fields = {
        "energy_change",
        "sleep_change",
        "bowel_change",
        "positive_body_signal",
    }
    body_feedback_cases = await _outcome_event_case_count(session, body_feedback_fields)
    body_improved_cases = await _outcome_event_case_count(
        session,
        body_feedback_fields,
        event_types=POSITIVE_OUTCOME_EVENT_TYPES,
    )
    referral_cases = await _outcome_event_case_count(session, {"referral_signal"})
    sleep_improved_cases = await _outcome_event_case_count(
        session,
        {"sleep_change"},
        event_types=POSITIVE_OUTCOME_EVENT_TYPES,
    )
    sleep_reported_cases, sleep_improved_with_checkin = await _sleep_checkin_coverage(session)
    day28_weight, day28_waist = await _day28_body_change(session, now=now)

    return OutcomeDashboardSummary(
        active_cases=active_cases,
        eligible_day7_cases=day7_eligible,
        eligible_day28_cases=day28_eligible,
        metrics=[
            _rate_metric(
                key="profile_completion_rate",
                label="建档完成率",
                numerator=profile_cases,
                denominator=active_cases,
                description="已有用户画像的服务单占比，用来判断获客数据与建档质量。",
            ),
            _rate_metric(
                key="core_document_generation_rate",
                label="核心交付生成率",
                numerator=core_document_cases,
                denominator=active_cases,
                description="已生成两份核心交付文件的服务单占比，用来判断系统承接效率。",
            ),
            _rate_metric(
                key="day7_snapshot_rate",
                label="7日成效快照覆盖率",
                numerator=day7_snapshots,
                denominator=day7_eligible,
                description="已到第7天的服务单中，完成阶段成效沉淀的占比。",
            ),
            _rate_metric(
                key="day28_checkin_completion_rate",
                label="28天有打卡达标率",
                numerator=day28_complete_checkins,
                denominator=day28_eligible,
                description="已到第28天的服务单中，至少有20天打卡记录的占比；不代表完整打卡。",
            ),
            _rate_metric(
                key="result_signal_rate",
                label="体重/围度/目标结果记录率",
                numerator=result_signal_cases,
                denominator=active_cases,
                description="聊天或阶段快照中已沉淀体重、围度或目标结果证据的用户占比。",
            ),
            _rate_metric(
                key="body_feedback_rate",
                label="睡眠/排便/能量反馈率",
                numerator=body_feedback_cases,
                denominator=active_cases,
                description=(
                    "愿意向我们讲身体感受的用户占比：睡眠、排便、精神能量等维度留下过反馈证据"
                    "就算，说变好、变差、没变化都算。反映沟通深度，不反映服务效果。"
                ),
            ),
            _rate_metric(
                key="renewal_intent_rate",
                label="明确复购意向率",
                numerator=renewal_intent_users,
                denominator=active_cases,
                description="用户聊天中明确表达继续购买、续期或推荐购买的占比。",
            ),
            _rate_metric(
                key="refund_intent_rate",
                label="退款/退出意向率",
                numerator=refund_intent_users,
                denominator=active_cases,
                description="用户聊天中明确表达退款、退群、不继续等意向的占比。",
            ),
            _rate_metric(
                key="referral_signal_rate",
                label="推荐线索率",
                numerator=referral_cases,
                denominator=active_cases,
                description="聊天中出现推荐朋友、同事或家人参与的真实线索占比。",
            ),
            # 两个体征变化刻意用中位数而不是均值：样本只有几十人，个别极端个案
            # （或没被清洗规则拦住的录入问题）会把均值整体带偏。均值、分层和剔除原因
            # 都在 /dashboard/body-change 详情页展示。
            _value_metric(
                key="day28_weight_change_median",
                label="28天体重变化（中位数）",
                value=day28_weight.stat.median,
                unit="kg",
                sample_count=day28_weight.stat.sample_count,
                min_sample=_BODY_CHANGE_MIN_PUBLIC_SAMPLE,
                description=(
                    "负数=瘦了。起点取入营问卷填的体重，终点取第28天前后的打卡体重，"
                    f"服务已满28天的{day28_weight.eligible_cases}人里有"
                    f"{day28_weight.stat.sample_count}人两头都有可用数据。"
                    "点开可看 3/7/14/28 天分窗口、分年龄段与减重带的结果和样本口径。"
                ),
            ),
            _value_metric(
                key="day28_waist_change_median",
                label="28天腰围变化（中位数）",
                value=day28_waist.stat.median,
                unit="cm",
                sample_count=day28_waist.stat.sample_count,
                min_sample=_BODY_CHANGE_MIN_PUBLIC_SAMPLE,
                description=(
                    "负数=小了。腰围每 7 天才量一次、很多人不量，"
                    f"服务已满28天的{day28_waist.eligible_cases}人里只有"
                    f"{day28_waist.stat.sample_count}人两头都有可用数据，"
                    f"不足{_BODY_CHANGE_MIN_PUBLIC_SAMPLE}人时只报人数、不给结论。"
                ),
            ),
            # 两个"改善"口径放在经营信号末尾、由粗到细相邻展示：
            # 体感改善率（睡眠/排便/精神能量合并）→ 睡眠质量提升率（单看睡眠）。
            # 对应的"体感反馈率"是上方主指标大卡，收全方向，两者差额即反馈了但没变好的人。
            _rate_metric(
                key="body_improvement_rate",
                label="体感改善率",
                numerator=body_improved_cases,
                denominator=active_cases,
                description=(
                    "睡眠、排便、精神能量等维度出现过改善证据的用户占比。上方"
                    "「睡眠/排便/能量反馈率」收的是全方向反馈，两者差额即反馈了但没变好的人。"
                ),
            ),
            _rate_metric(
                key="sleep_improvement_rate",
                label="睡眠质量提升率",
                numerator=sleep_improved_cases,
                denominator=active_cases,
                description=_sleep_improvement_description(
                    improved_cases=sleep_improved_cases,
                    reported_cases=sleep_reported_cases,
                    improved_with_checkin=sleep_improved_with_checkin,
                ),
            ),
        ],
    )


def _sleep_improvement_description(
    *,
    improved_cases: int,
    reported_cases: int,
    improved_with_checkin: int,
) -> str:
    base = (
        "聊天或打卡中出现睡眠变好证据（入睡变快、夜醒减少、睡眠评分提高等）的用户占全部有效服务单的比例。"
    )
    if reported_cases <= 0:
        return base
    rate = round(improved_with_checkin * 100 / reported_cases, 1)
    return (
        f"{base}另一个角度：打卡里记录过睡眠的 {reported_cases} 人中有 "
        f"{improved_with_checkin} 人变好（{rate}%）。"
    )


async def _eligible_case_count(session: AsyncSession, *, now: datetime, day: int) -> int:
    eligible_before = _service_day_eligible_before(now, day)
    count = await session.scalar(
        select(func.count(ServiceCase.id)).where(
            _stats_case_condition(),
            ServiceCase.started_at.is_not(None),
            ServiceCase.started_at < eligible_before,
        )
    )
    return count or 0


async def _distinct_active_case_count(session: AsyncSession, service_case_column) -> int:
    count = await session.scalar(
        select(func.count(distinct(service_case_column)))
        .join(ServiceCase, service_case_column == ServiceCase.id)
        .where(_stats_case_condition())
    )
    return count or 0


async def _core_document_case_count(session: AsyncSession) -> int:
    doc_counts = (
        select(
            UserDocument.service_case_id.label("service_case_id"),
            func.count(distinct(UserDocument.document_type)).label("document_count"),
        )
        .join(ServiceCase, UserDocument.service_case_id == ServiceCase.id)
        .where(
            _stats_case_condition(),
            UserDocument.document_type.in_(
                [
                    DocumentType.ENERGY_RESTART_PRESCRIPTION,
                    DocumentType.DAY28_EXECUTION_STANDARD,
                ]
            ),
        )
        .group_by(UserDocument.service_case_id)
        .subquery()
    )
    count = await session.scalar(
        select(func.count()).select_from(doc_counts).where(doc_counts.c.document_count >= 2)
    )
    return count or 0


async def _snapshot_case_count(session: AsyncSession, *, milestone: str, now: datetime, day: int) -> int:
    eligible_before = _service_day_eligible_before(now, day)
    count = await session.scalar(
        select(func.count(distinct(ServiceCase.id)))
        .join(OutcomeSnapshot, OutcomeSnapshot.service_case_id == ServiceCase.id)
        .where(
            _stats_case_condition(),
            ServiceCase.started_at.is_not(None),
            ServiceCase.started_at < eligible_before,
            OutcomeSnapshot.milestone == milestone,
        )
    )
    return count or 0


async def _checkin_completion_case_count(
    session: AsyncSession,
    *,
    now: datetime,
    day: int,
    minimum_days: int,
) -> int:
    eligible_before = _service_day_eligible_before(now, day)
    completed_cases = (
        select(
            Checkin.service_case_id.label("service_case_id"),
            func.count(distinct(Checkin.checkin_date)).label("checkin_days"),
        )
        .join(ServiceCase, Checkin.service_case_id == ServiceCase.id)
        .where(
            _stats_case_condition(),
            ServiceCase.started_at.is_not(None),
            ServiceCase.started_at < eligible_before,
        )
        .group_by(Checkin.service_case_id)
        .subquery()
    )
    count = await session.scalar(
        select(func.count()).select_from(completed_cases).where(completed_cases.c.checkin_days >= minimum_days)
    )
    return count or 0


async def _outcome_event_case_count(
    session: AsyncSession,
    field_keys: set[str],
    *,
    event_types: set[str] | None = None,
) -> int:
    conditions = [
        _stats_case_condition(),
        OutcomeEvent.field_key.in_(sorted(field_keys)),
    ]
    if event_types:
        conditions.append(OutcomeEvent.event_type.in_(sorted(event_types)))
    count = await session.scalar(
        select(func.count(distinct(OutcomeEvent.service_case_id)))
        .join(ServiceCase, OutcomeEvent.service_case_id == ServiceCase.id)
        .where(*conditions)
    )
    return count or 0


async def _sleep_checkin_coverage(session: AsyncSession) -> tuple[int, int]:
    """打卡里真正记录过睡眠的人数，以及其中睡眠被判定变好的人数。

    成效表只收正向事件，负向/无变化改由状态画像承接，所以"聊到过睡眠的人"无法从成效表
    稳定统计（越往后越会只剩变好的人，比例虚高）。打卡里的睡眠字段与判定口径无关，是可长期
    对比的分母。
    """

    sleep_value = Checkin.parsed_json["sleep"].as_string()
    reported_cases = (
        select(distinct(Checkin.service_case_id).label("service_case_id"))
        .join(ServiceCase, Checkin.service_case_id == ServiceCase.id)
        .where(
            _stats_case_condition(),
            Checkin.deleted_at.is_(None),
            sleep_value.is_not(None),
            func.btrim(sleep_value) != "",
        )
        .subquery()
    )
    reported_count = await session.scalar(select(func.count()).select_from(reported_cases))

    improved_count = await session.scalar(
        select(func.count(distinct(OutcomeEvent.service_case_id)))
        .join(ServiceCase, OutcomeEvent.service_case_id == ServiceCase.id)
        .join(reported_cases, reported_cases.c.service_case_id == OutcomeEvent.service_case_id)
        .where(
            _stats_case_condition(),
            OutcomeEvent.field_key == "sleep_change",
            OutcomeEvent.event_type.in_(sorted(POSITIVE_OUTCOME_EVENT_TYPES)),
        )
    )
    return reported_count or 0, improved_count or 0


# 执行度必须用互斥区间，不能再把“全部”与“≥60%”两个重叠人群当成效果分层。
_BODY_CHANGE_SEGMENT_KEYS = (
    "checkin_rate_band",
    "age",
    "weight_loss_band",
    "customer_type",
)
_CHECKIN_RATE_BANDS = ("低于60%", "60%–79%", "80%及以上")
# 一个分桶少于这个人数就只报人数、不给结论——三个人的中位数没有解读价值。
_BODY_CHANGE_MIN_SEGMENT_SAMPLE = 3
# 主看板大卡对外展示的最低样本量。这两个数字要拿去做项目介绍，不到这个人数只报人数。
_BODY_CHANGE_MIN_PUBLIC_SAMPLE = 10


async def _day28_body_change(
    session: AsyncSession,
    *,
    now: datetime,
) -> tuple[BodyChangeMetricWindow, BodyChangeMetricWindow]:
    """主看板两张大卡的数据源，与详情页同一套计算，避免两处口径漂移。"""

    changes, _ = await compute_case_window_changes(session, now=now)
    rows = [item for item in changes if item.window_day == 28]
    return (
        _body_change_metric(
            rows,
            metric="weight",
            min_rate=0.0,
            case_labels={},
            with_segments=False,
            with_samples=False,
        ),
        _body_change_metric(
            rows,
            metric="waist",
            min_rate=0.0,
            case_labels={},
            with_segments=False,
            with_samples=False,
        ),
    )


@router.get("/body-change", response_model=BodyChangeSummary)
async def dashboard_body_change(
    min_checkin_rate: float = 0.0,
    session: AsyncSession = Depends(get_session),
) -> BodyChangeSummary:
    """体重/腰围变化的完整口径：3/7/14/28 天四个窗口 + 人群分层 + 样本交代。

    为什么要分窗口：前 7 天几乎所有人都能坚持，第二周开始分化，只看 28 天一个总数会把
    认真执行的人和早就放弃的人混成一个平均值。

    ``min_checkin_rate`` 是打卡率门槛（0.6 即客户说的 60%）。分母始终是"服务天数已经
    走到该窗口的人"，被门槛筛掉的人计入未纳入原因，方便看出门槛滤掉了多少。
    """

    rate = min(max(float(min_checkin_rate), 0.0), 1.0)
    now = datetime.now(timezone.utc)
    changes, quality = await compute_case_window_changes(session, now=now)
    _, case_labels = await _compute_case_portrait_labels(session)
    changed_case_ids = sorted({item.service_case_id for item in changes})
    case_refs = {
        service_case.id: _case_ref(service_case, user)
        for service_case, user in (
            await session.execute(
                select(ServiceCase, User)
                .outerjoin(User, ServiceCase.user_id == User.id)
                .where(ServiceCase.id.in_(changed_case_ids))
            )
        ).all()
    }
    return BodyChangeSummary(
        min_checkin_rate=rate,
        windows=[
            BodyChangeWindow(
                window_day=window,
                weight=_body_change_metric(
                    [item for item in changes if item.window_day == window],
                    metric="weight",
                    min_rate=rate,
                    case_labels=case_labels,
                    case_refs=case_refs,
                ),
                waist=_body_change_metric(
                    [item for item in changes if item.window_day == window],
                    metric="waist",
                    min_rate=rate,
                    case_labels=case_labels,
                    case_refs=case_refs,
                ),
            )
            for window in WINDOW_DAYS
        ],
        weight_record_reasons=quality.weight_reasons,
        waist_record_reasons=quality.waist_reasons,
        generated_at=now,
    )


def _body_change_metric(
    rows: list[CaseWindowChange],
    *,
    metric: str,
    min_rate: float,
    case_labels: dict[int, dict[str, str | list[str]]],
    case_refs: dict[int, DashboardCaseRef] | None = None,
    with_segments: bool = True,
    with_samples: bool = True,
) -> BodyChangeMetricWindow:
    eligible = len(rows)
    excluded: dict[str, int] = {}
    included: list[tuple[int, float, float]] = []
    samples: list[BodyChangeSample] = []
    unverified = 0
    for row in rows:
        outcome = row.weight if metric == "weight" else row.waist
        status = outcome.status
        if min_rate > 0 and row.checkin_rate < min_rate:
            status = EXCLUDE_BELOW_RATE
        if status != "ok":
            excluded[status] = excluded.get(status, 0) + 1
        else:
            assert outcome.delta is not None
            included.append((row.service_case_id, outcome.delta, row.checkin_rate))
            if not outcome.unit_verified:
                unverified += 1
        case_ref = (case_refs or {}).get(row.service_case_id)
        if with_samples and case_ref is not None:
            samples.append(
                BodyChangeSample(
                    case=case_ref,
                    checkin_days=row.checkin_days,
                    checkin_rate=row.checkin_rate,
                    baseline=outcome.baseline,
                    baseline_day=outcome.baseline_day,
                    baseline_source=outcome.baseline_source,
                    endpoint=outcome.endpoint,
                    endpoint_day=outcome.endpoint_day,
                    delta=outcome.delta,
                    status=status,
                    unit_verified=outcome.unit_verified,
                )
            )
    return BodyChangeMetricWindow(
        stat=BodyChangeStat(**summarize_deltas(delta for _, delta, _ in included)),
        eligible_cases=eligible,
        coverage_rate=round(len(included) / eligible, 4) if eligible else None,
        unverified_unit_count=unverified,
        excluded=excluded,
        segments=[
            _body_change_segment(key, included, case_labels)
            for key in _BODY_CHANGE_SEGMENT_KEYS
        ]
        if with_segments
        else [],
        samples=sorted(
            samples,
            key=lambda item: (
                item.status != "ok",
                item.delta if item.delta is not None else float("inf"),
                item.case.case_no,
            ),
        ),
    )


def _body_change_segment(
    key: str,
    included: list[tuple[int, float, float]],
    case_labels: dict[int, dict[str, str | list[str]]],
) -> BodyChangeSegment:
    """按人群分桶给出各自的变化，复用群体画像那套分桶标签，保证两个页面口径一致。"""

    dim = (
        {
            "label": "有打卡执行度",
            "description": "按当前窗口内有打卡的天数占比互斥分组，三组人数不会重复；不要求五项完整。",
            "numeric": True,
            "labels": _CHECKIN_RATE_BANDS,
        }
        if key == "checkin_rate_band"
        else _DIMENSION_BY_KEY[key]
    )
    grouped: dict[str, list[float]] = {}
    for case_id, delta, checkin_rate in included:
        if key == "checkin_rate_band":
            bucket = (
                _CHECKIN_RATE_BANDS[0]
                if checkin_rate < 0.6
                else _CHECKIN_RATE_BANDS[1]
                if checkin_rate < 0.8
                else _CHECKIN_RATE_BANDS[2]
            )
        else:
            label = case_labels.get(case_id, {}).get(key)
            bucket = label if isinstance(label, str) and label else UNKNOWN_LABEL
        grouped.setdefault(bucket, []).append(delta)
    if dim["numeric"]:
        names = [name for name in dim["labels"] if name in grouped]
    else:
        names = sorted(grouped, key=lambda name: (-len(grouped[name]), name))
    if UNKNOWN_LABEL in grouped and UNKNOWN_LABEL not in names:
        names.append(UNKNOWN_LABEL)
    elif UNKNOWN_LABEL in names:
        names = [name for name in names if name != UNKNOWN_LABEL] + [UNKNOWN_LABEL]
    return BodyChangeSegment(
        key=key,
        label=dim["label"],
        description=dim["description"],
        buckets=[
            BodyChangeSegmentBucket(
                stat=BodyChangeStat(**summarize_deltas(grouped[name])),
                bucket=name,
                conclusive=len(grouped[name]) >= _BODY_CHANGE_MIN_SEGMENT_SAMPLE,
            )
            for name in names
        ],
    )


def _rate_metric(
    *,
    key: str,
    label: str,
    numerator: int,
    denominator: int,
    description: str,
) -> DashboardMetric:
    if denominator <= 0:
        return DashboardMetric(
            key=key,
            label=label,
            value=None,
            unit="%",
            numerator=numerator,
            denominator=denominator,
            description=description,
            status="no_sample",
        )
    return DashboardMetric(
        key=key,
        label=label,
        value=round(numerator * 100 / denominator, 1),
        unit="%",
        numerator=numerator,
        denominator=denominator,
        description=description,
    )


def _value_metric(
    *,
    key: str,
    label: str,
    value: float | None,
    unit: str,
    sample_count: int,
    description: str,
    min_sample: int = 0,
) -> DashboardMetric:
    """绝对数值型指标。``min_sample`` 以下只显示"样本不足"，不给数字。

    客户明确要求样本不足时不硬下结论：线上腰围能算出变化的只有二十几人，
    再拆窗口后个别窗口只剩几个人，那种数字放在对外看板上比不给更糟。
    """

    if value is not None and sample_count < min_sample:
        value = None
    return DashboardMetric(
        key=key,
        label=label,
        value=value,
        unit=unit,
        denominator=sample_count,
        description=description,
        status="ok" if value is not None else "no_sample",
    )


def _service_day_eligible_before(now: datetime, day: int) -> datetime:
    """Return the UTC boundary for natural China-date service-day eligibility."""
    china_tz = ZoneInfo("Asia/Shanghai")
    latest_start_date = now.astimezone(china_tz).date() - timedelta(days=day - 1)
    next_day_start = datetime.combine(
        latest_start_date + timedelta(days=1),
        time.min,
        tzinfo=china_tz,
    )
    return next_day_start.astimezone(timezone.utc)


def _float_metric(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


@router.get("/case-health", response_model=list[CaseHealthItem])
async def dashboard_case_health(session: AsyncSession = Depends(get_session)) -> list[CaseHealthItem]:
    china_tz = ZoneInfo("Asia/Shanghai")
    today = datetime.now(china_tz).date()
    cases = list(
        await session.scalars(
            select(ServiceCase)
            .where(_stats_case_condition(), ServiceCase.status == ServiceCaseStatus.IN_SERVICE)
            .order_by(ServiceCase.started_at.asc())
        )
    )
    if not cases:
        return []
    case_ids = [case.id for case in cases]

    users: dict[int, str] = {}
    user_ids = [case.user_id for case in cases if case.user_id]
    if user_ids:
        for uid, name in await session.execute(select(User.id, User.name).where(User.id.in_(user_ids))):
            users[uid] = name

    last_checkin: dict[int, Any] = {}
    for cid, checkin_date in await session.execute(
        select(Checkin.service_case_id, func.max(Checkin.checkin_date))
        .where(Checkin.service_case_id.in_(case_ids))
        .group_by(Checkin.service_case_id)
    ):
        last_checkin[cid] = checkin_date

    last_inbound: dict[int, datetime] = {}
    for cid, created_at in await session.execute(
        select(Message.service_case_id, func.max(Message.created_at))
        .where(
            Message.service_case_id.in_(case_ids),
            Message.direction == MessageDirection.INBOUND,
            Message.sender_type == MessageSenderType.USER,
        )
        .group_by(Message.service_case_id)
    ):
        last_inbound[cid] = created_at

    stages_by_case: dict[int, set[str]] = {}
    for doc in await session.scalars(
        select(UserDocument).where(
            UserDocument.service_case_id.in_(case_ids),
            UserDocument.document_type == DocumentType.STAGE_REPORT,
        )
    ):
        stage = (doc.content_json or {}).get("stage") if isinstance(doc.content_json, dict) else None
        if stage:
            stages_by_case.setdefault(doc.service_case_id, set()).add(str(stage))

    items: list[CaseHealthItem] = []
    for case in cases:
        last = last_checkin.get(case.id)
        items.append(
            CaseHealthItem(
                service_case_id=case.id,
                case_no=case.case_no,
                user_name=users.get(case.user_id) if case.user_id else None,
                group_id=case.group_id,
                service_day=_case_service_day(case, today, china_tz),
                last_checkin_date=last.isoformat() if last else None,
                days_since_checkin=(today - last).days if last else None,
                risk_level=case.risk_level,
                stage_reports=sorted(stages_by_case.get(case.id, set())),
                last_inbound_at=last_inbound.get(case.id),
            )
        )
    return items


def _case_service_day(service_case, today, tz: ZoneInfo) -> int:
    """看板服务天数：计划开始当天=第1天；计划日未到返回 <=0(前端显示『未开始』)；无起点返回 0。"""
    start = effective_start_at(service_case)
    if start is None:
        return 0
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    return (today - start.astimezone(tz).date()).days + 1
