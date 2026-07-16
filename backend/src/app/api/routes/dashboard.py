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
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user import User
from app.models.questionnaire import QuestionnaireSubmission
from app.models.user_document import DocumentStatus, DocumentType, UserDocument
from app.models.user_profile import UserProfile
from app.schemas.dashboard import (
    AICostCaseItem,
    AICostSummary,
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
    PopulationDistribution,
    PopulationDistributionBucket,
    PopulationGeography,
    PopulationGeoCity,
    PopulationGeoProvince,
    PopulationPortrait,
)
from app.services.china_regions import match_city
from app.schemas.service_case import ServiceCaseRead
from app.services.intake_facts import (
    build_intake_fact_summary,
    customer_type_for_profile,
    weight_loss_band_from_profile,
)
from app.services.questionnaire_template import get_active_questionnaire_fields
from app.services.service_case_queries import active_service_case_condition, effective_start_at

router = APIRouter()


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
        select(func.count(ServiceCase.id)).where(active_service_case_condition())
    )
    in_service = await session.scalar(
        select(func.count(ServiceCase.id)).where(
            active_service_case_condition(),
            ServiceCase.status == ServiceCaseStatus.IN_SERVICE,
        )
    )
    today_start, today_end = _today_bounds()
    today = today_start.astimezone(ZoneInfo("Asia/Shanghai")).date()
    checkins_today = await session.scalar(
        select(func.count(Checkin.id))
        .join(ServiceCase, Checkin.service_case_id == ServiceCase.id)
        .where(
            active_service_case_condition(),
            Checkin.checkin_date == today,
        )
    )
    messages_today = await session.scalar(
        select(func.count(Message.id))
        .join(ServiceCase, Message.service_case_id == ServiceCase.id)
        .where(
            active_service_case_condition(),
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
            active_service_case_condition(),
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
        .where(active_service_case_condition())
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
            active_service_case_condition(),
            ServiceCase.created_at >= today_start,
            ServiceCase.created_at < today_end,
        )
    )
    risk_users = await session.scalar(
        select(func.count(ServiceCase.id)).where(
            active_service_case_condition(),
            ServiceCase.risk_level == "high",
        )
    )
    # 『需关注事件』数：今日触发转人工/@班主任 的事件数（事件级，同一用户多次触发算多条）。
    # 口径与「今日用户消息/今日打卡」一致，每日自然滚动，无需人工清零机制。
    # 判定信号：入站用户消息的 analysis_json.requires_human_intervention（由 analyze_intervention 写入，
    # 正是 engine 触发 @班主任 handoff 的条件）。注：旧的「待审核 AI 回复(PENDING)」机制已废弃——
    # 现凡需介入一律直接 @班主任(status=SENT)，不再生成 PENDING 回复，故不能再用它统计。
    human_intervention_users = await session.scalar(
        select(func.count(Message.id))
        .join(ServiceCase, Message.service_case_id == ServiceCase.id)
        .where(
            active_service_case_condition(),
            Message.created_at >= today_start,
            Message.created_at < today_end,
            Message.direction == MessageDirection.INBOUND,
            Message.sender_type == MessageSenderType.USER,
            _requires_human_intervention_expr(),
        )
    )
    now = datetime.now(timezone.utc)
    last_message_subq = (
        select(Message.service_case_id, func.max(Message.created_at).label("last_at"))
        .where(Message.direction == MessageDirection.INBOUND)
        .group_by(Message.service_case_id)
        .subquery()
    )
    last_activity_at = func.coalesce(last_message_subq.c.last_at, ServiceCase.created_at)
    unfollowed_users = await session.scalar(
        select(func.count(ServiceCase.id))
        .outerjoin(last_message_subq, last_message_subq.c.service_case_id == ServiceCase.id)
        .where(
            active_service_case_condition(),
            ServiceCase.status == ServiceCaseStatus.IN_SERVICE,
            last_activity_at < now - timedelta(days=2),
            last_activity_at >= now - timedelta(days=7),
        )
    )
    lost_users = await session.scalar(
        select(func.count(ServiceCase.id))
        .outerjoin(last_message_subq, last_message_subq.c.service_case_id == ServiceCase.id)
        .where(
            active_service_case_condition(),
            ServiceCase.status == ServiceCaseStatus.IN_SERVICE,
            last_activity_at < now - timedelta(days=7),
        )
    )
    renewal_followup_users = await session.scalar(
        select(func.count(ServiceCase.id)).where(
            active_service_case_condition(),
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
                active_service_case_condition(),
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
        messages_today=messages_today or 0,
        messages_outbound_today=messages_outbound_today or 0,
        pending_documents=pending_documents or 0,
        new_users_today=new_users_today or 0,
        risk_users=risk_users or 0,
        human_intervention_users=human_intervention_users or 0,
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

# 群体画像维度元数据：画像端点与下钻端点共用，保证顺序/标签/分桶完全一致。
# numeric=True 的维度带 edges/labels（升序左闭分桶，含 0 计数桶）；分类维度按计数降序。
_PORTRAIT_DIMENSIONS: list[dict[str, Any]] = [
    {
        "key": "customer_type",
        "label": "客户分型",
        "description": "按风险与减重带确定性投影的单一分型标签，用于快速定调。",
        "numeric": False,
    },
    {
        "key": "weight_loss_band",
        "label": "减重带分布",
        "description": "按 BMI 划分的减重目标带位。",
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
    """群体画像：活跃服务单的量化区间分布 + 省→市地域层级。

    数据来源：每个活跃服务单取最新画像(customer_type/减重带) + 最新入营问卷(城市/年龄/BMI/体脂/腰围/身高)。
    每个分布都返回有效样本与『未采集』计数，绝不静默丢弃缺失。分桶逻辑与下钻端点共用同一套 case 标签。
    """
    total_users, case_labels = await _compute_case_portrait_labels(session)
    if total_users == 0:
        return PopulationPortrait(total_users=0, distributions=[], geography=None)
    distributions = [_build_distribution(dim, case_labels) for dim in _PORTRAIT_DIMENSIONS]
    return PopulationPortrait(
        total_users=total_users,
        distributions=distributions,
        geography=_build_geography(case_labels),
    )


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
    matched_ids = [cid for cid, labels in case_labels.items() if labels.get(key) == bucket]
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
) -> tuple[int, dict[int, dict[str, str]]]:
    """逐服务单算出各画像维度的分桶标签（画像端点与下钻端点共用，保证一致）。

    只统计已建档的活跃服务单（未建档空单不计入，避免兜底标签虚高）。
    返回 (total_users, {case_id: {dimension_key: bucket_label}})；缺失统一归一为 UNKNOWN_LABEL。
    """
    # 活跃服务单 id → risk_level
    case_rows = (
        await session.execute(
            select(ServiceCase.id, ServiceCase.risk_level).where(active_service_case_condition())
        )
    ).all()
    risk_by_case = {cid: risk for cid, risk in case_rows}
    active_ids = list(risk_by_case.keys())
    if not active_ids:
        return 0, {}

    # 每个 case 最新画像
    latest_profile_json: dict[int, dict] = {}
    for profile in await session.scalars(
        select(UserProfile)
        .where(UserProfile.service_case_id.in_(active_ids))
        .order_by(UserProfile.service_case_id, UserProfile.created_at.desc())
        .distinct(UserProfile.service_case_id)
    ):
        latest_profile_json[profile.service_case_id] = profile.profile_json or {}

    case_ids = list(latest_profile_json.keys())
    if not case_ids:
        return 0, {}

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

    fields = await get_active_questionnaire_fields(session, "intake")

    case_labels: dict[int, dict[str, str]] = {}
    for cid in case_ids:
        profile_json = latest_profile_json.get(cid) or {}
        risk_level = risk_by_case.get(cid)
        # 客户分型：优先已落库标签，缺失则即时推导（兜底）
        ctype = profile_json.get("customer_type")
        if not isinstance(ctype, str) or not ctype.strip():
            ctype = customer_type_for_profile(profile_json, risk_level)
        band = _band_short(weight_loss_band_from_profile(profile_json) or "未采集")

        submission = latest_submission.get(cid)
        facts = build_intake_fact_summary(fields, submission.answers_json) if submission else {}
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
        labels: dict[str, str] = {
            "customer_type": _category_label(ctype),
            "weight_loss_band": _category_label(band),
            "city": _category_label(city),
            "province": _category_label(province),
        }
        for dim in _PORTRAIT_DIMENSIONS:
            if dim["numeric"]:
                labels[dim["key"]] = _bucket_numeric(
                    numeric_values[dim["key"]], dim["edges"], dim["labels"]
                )
        case_labels[cid] = labels

    return len(case_ids), case_labels


def _build_geography(case_labels: dict[int, dict[str, str]]) -> PopulationGeography:
    """从逐 case 城市标签构造省→市树；未匹配地址单独计数，绝不静默丢弃。"""
    cities_by_province: dict[str, dict[str, int]] = {}
    unknown = 0
    for labels in case_labels.values():
        province = labels.get("province", UNKNOWN_LABEL)
        city = labels.get("city", UNKNOWN_LABEL)
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
    dim: dict[str, Any], case_labels: dict[int, dict[str, str]]
) -> PopulationDistribution:
    """从逐-case 标签统计单个维度的分布。数值维度按固定顺序（含 0 桶），分类维度按计数降序。"""
    counts: dict[str, int] = {}
    unknown = 0
    for labels in case_labels.values():
        value = labels.get(dim["key"], UNKNOWN_LABEL)
        if value == UNKNOWN_LABEL:
            unknown += 1
            continue
        counts[value] = counts.get(value, 0) + 1
    if dim["numeric"]:
        buckets = [
            PopulationDistributionBucket(label=name, count=counts.get(name, 0))
            for name in dim["labels"]
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
        total=sum(counts.values()),
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
                active_service_case_condition(),
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
                active_service_case_condition(),
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
            .where(active_service_case_condition(), ServiceCase.risk_level == "high")
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
    """服务中、且最近入站消息距今 [min_days, max_days) 天的服务单（含闲置天数）。"""
    now = datetime.now(timezone.utc)
    last_message_subq = (
        select(Message.service_case_id, func.max(Message.created_at).label("last_at"))
        .where(Message.direction == MessageDirection.INBOUND)
        .group_by(Message.service_case_id)
        .subquery()
    )
    last_activity_at = func.coalesce(last_message_subq.c.last_at, ServiceCase.created_at)
    conditions = [
        active_service_case_condition(),
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
    items: list[DashboardCaseInsightItem] = []
    for service_case, user, last_at in rows:
        basis = last_at or service_case.created_at
        if basis is not None and basis.tzinfo is None:
            basis = basis.replace(tzinfo=timezone.utc)
        idle_days = (now - basis).days if basis is not None else None
        items.append(
            DashboardCaseInsightItem(
                case=_case_ref(service_case, user),
                risk_level=service_case.risk_level,
                idle_days=idle_days,
                last_inbound_at=last_at,
            )
        )
    return items


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
                active_service_case_condition(),
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
            .where(active_service_case_condition(), flag_column.is_not(None))
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
                active_service_case_condition(),
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
            .where(active_service_case_condition(), Checkin.checkin_date == today)
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
                created_at=checkin.created_at,
            )
        )
    return list(grouped.values())


@router.get("/pending-documents", response_model=list[DashboardCasePendingDocuments])
async def dashboard_pending_documents(
    session: AsyncSession = Depends(get_session),
) -> list[DashboardCasePendingDocuments]:
    from sqlalchemy import text

    latest_doc_ids = (
        select(UserDocument.id)
        .distinct(
            UserDocument.service_case_id,
            UserDocument.document_type,
            text("content_json->>'stage'"),
        )
        .join(ServiceCase, UserDocument.service_case_id == ServiceCase.id)
        .where(active_service_case_condition())
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
        item.documents.append(
            DashboardPendingDocument(
                document_id=document.id,
                document_type=document.document_type.value,
                title=document.title,
                status=document.status.value,
                created_at=document.created_at,
                updated_at=document.updated_at,
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
        .where(active_service_case_condition(), AIUsageLog.is_diagnostic.is_(False))
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
            (AIUsageLog.service_case_id.is_(None)) | active_service_case_condition(),
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
            active_service_case_condition(),
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
    body_feedback_cases = await _outcome_event_case_count(
        session,
        {
            "energy_change",
            "sleep_change",
            "bowel_change",
            "positive_body_signal",
        },
    )
    referral_cases = await _outcome_event_case_count(session, {"referral_signal"})
    day28_weight_avg, day28_weight_count, day28_waist_avg, day28_waist_count = await _day28_change_averages(
        session,
        now=now,
    )

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
                label="28天打卡完成率",
                numerator=day28_complete_checkins,
                denominator=day28_eligible,
                description="已到第28天的服务单中，至少沉淀20天打卡记录的占比。",
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
                description="已收集到睡眠、排便、精神能量等体感反馈证据的用户占比。",
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
            _value_metric(
                key="avg_day28_weight_change",
                label="28天平均体重变化",
                value=day28_weight_avg,
                unit="kg",
                sample_count=day28_weight_count,
                description="来自第28天阶段快照的体重变化均值；样本不足时不展示结论。",
            ),
            _value_metric(
                key="avg_day28_waist_change",
                label="28天平均腰围变化",
                value=day28_waist_avg,
                unit="cm",
                sample_count=day28_waist_count,
                description="来自第28天阶段快照的腰围变化均值；样本不足时不展示结论。",
            ),
        ],
    )


async def _eligible_case_count(session: AsyncSession, *, now: datetime, day: int) -> int:
    eligible_before = _service_day_eligible_before(now, day)
    count = await session.scalar(
        select(func.count(ServiceCase.id)).where(
            active_service_case_condition(),
            ServiceCase.started_at.is_not(None),
            ServiceCase.started_at < eligible_before,
        )
    )
    return count or 0


async def _distinct_active_case_count(session: AsyncSession, service_case_column) -> int:
    count = await session.scalar(
        select(func.count(distinct(service_case_column)))
        .join(ServiceCase, service_case_column == ServiceCase.id)
        .where(active_service_case_condition())
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
            active_service_case_condition(),
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
            active_service_case_condition(),
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
            active_service_case_condition(),
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


async def _outcome_event_case_count(session: AsyncSession, field_keys: set[str]) -> int:
    count = await session.scalar(
        select(func.count(distinct(OutcomeEvent.service_case_id)))
        .join(ServiceCase, OutcomeEvent.service_case_id == ServiceCase.id)
        .where(
            active_service_case_condition(),
            OutcomeEvent.field_key.in_(sorted(field_keys)),
        )
    )
    return count or 0


async def _day28_change_averages(
    session: AsyncSession,
    *,
    now: datetime,
) -> tuple[float | None, int, float | None, int]:
    eligible_before = _service_day_eligible_before(now, 28)
    snapshots = list(
        await session.scalars(
            select(OutcomeSnapshot)
            .join(ServiceCase, OutcomeSnapshot.service_case_id == ServiceCase.id)
            .where(
                active_service_case_condition(),
                ServiceCase.started_at.is_not(None),
                ServiceCase.started_at < eligible_before,
                OutcomeSnapshot.milestone == "day28",
            )
        )
    )
    weight_changes = [_float_metric((item.metrics_json or {}).get("weight_change")) for item in snapshots]
    waist_changes = [_float_metric((item.metrics_json or {}).get("waist_change")) for item in snapshots]
    weight_values = [item for item in weight_changes if item is not None]
    waist_values = [item for item in waist_changes if item is not None]
    return (
        round(mean(weight_values), 2) if weight_values else None,
        len(weight_values),
        round(mean(waist_values), 2) if waist_values else None,
        len(waist_values),
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
) -> DashboardMetric:
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
            .where(active_service_case_condition(), ServiceCase.status == ServiceCaseStatus.IN_SERVICE)
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
            UserDocument.document_type.in_([DocumentType.STAGE_REPORT, DocumentType.GRADUATION_REPORT]),
        )
    ):
        if doc.document_type == DocumentType.GRADUATION_REPORT:
            stage = "graduation"
        else:
            stage = (doc.content_json or {}).get("stage") if isinstance(doc.content_json, dict) else None
        if stage:
            stages_by_case.setdefault(doc.service_case_id, set()).add(str(stage))

    # 『需关注』事件级：今日触发转人工/@班主任 的 case（与看板卡片同口径）。
    today_start, today_end = _today_bounds()
    pending_review_ids: set[int] = set(
        (
            await session.scalars(
                select(Message.service_case_id)
                .where(
                    Message.service_case_id.in_(case_ids),
                    Message.created_at >= today_start,
                    Message.created_at < today_end,
                    Message.direction == MessageDirection.INBOUND,
                    Message.sender_type == MessageSenderType.USER,
                    _requires_human_intervention_expr(),
                )
                .distinct()
            )
        ).all()
    )

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
                needs_human_intervention=case.id in pending_review_ids,
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

