"""按服务开始月份、同一成熟阶段聚合客户成效趋势。

趋势不是把当前看板比例按月份简单切片：不同月份的用户服务天数不同，直接比较会把
“还没走到第 28 天”误读成“效果更差”。本模块给每个指标声明固定成熟阶段，只有已经
走到该阶段的服务单才进入分母，并且只读取该服务阶段内发生的打卡、聊天和成效证据。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.checkin import Checkin
from app.models.message import Message, MessageDirection, MessageSenderType
from app.models.outcome import OutcomeEvent, OutcomeSnapshot
from app.models.service_case import ServiceCase
from app.models.user_document import DocumentType, UserDocument
from app.models.user_profile import UserProfile
from app.services.outcome_engine import POSITIVE_EVENT_TYPES
from app.services.service_case_queries import active_service_case_condition, effective_start_at

MIN_TREND_SAMPLE = 10

RESULT_FIELDS = {
    "weight_change",
    "waist_or_body_change",
    "weight_result",
    "waist_result",
    "challenge_goal_result",
}
BODY_FEEDBACK_FIELDS = {
    "energy_change",
    "sleep_change",
    "bowel_change",
    "positive_body_signal",
}


@dataclass(frozen=True)
class TrendMetricSpec:
    key: str
    label: str
    group: str
    stage_day: int
    direction: str
    description: str


TREND_GROUPS: tuple[dict[str, str], ...] = (
    {
        "key": "efficiency",
        "label": "承接效率",
        "description": "同批用户开始服务后，建档、核心交付与第7天成效沉淀是否按时完成。",
    },
    {
        "key": "participation",
        "label": "用户参与",
        "description": "用户是否持续打卡、持续交流，并愿意反馈身体感受。",
    },
    {
        "key": "outcome",
        "label": "服务结果",
        "description": "同批用户走满28天后，是否留下结果证据、体感改善与睡眠改善。",
    },
    {
        "key": "commercial",
        "label": "商业信号",
        "description": "同批用户前28天内出现的续购、退款与推荐线索。",
    },
)

TREND_METRICS: tuple[TrendMetricSpec, ...] = (
    TrendMetricSpec(
        "profile_completion_rate",
        "建档完成率",
        "efficiency",
        7,
        "higher_is_better",
        "走到第7天的服务单中，前7天内已生成用户画像的比例。",
    ),
    TrendMetricSpec(
        "core_document_generation_rate",
        "核心交付生成率",
        "efficiency",
        7,
        "higher_is_better",
        "走到第7天的服务单中，前7天内两份核心交付均已生成的比例。",
    ),
    TrendMetricSpec(
        "day7_snapshot_rate",
        "7日成效快照覆盖率",
        "efficiency",
        7,
        "higher_is_better",
        "走到第7天的服务单中，已按时生成第7天成效快照的比例。",
    ),
    TrendMetricSpec(
        "day7_checkin_active_rate",
        "7日持续打卡率",
        "participation",
        7,
        "higher_is_better",
        "走到第7天的服务单中，前7天至少4天有打卡记录的比例。",
    ),
    TrendMetricSpec(
        "day7_conversation_active_rate",
        "7日持续交流率",
        "participation",
        7,
        "higher_is_better",
        "走到第7天的服务单中，前7天至少3个不同日期有用户本人发言的比例。",
    ),
    TrendMetricSpec(
        "day28_checkin_completion_rate",
        "28天打卡完成率",
        "participation",
        28,
        "higher_is_better",
        "走到第28天的服务单中，前28天至少20天有打卡记录的比例。",
    ),
    TrendMetricSpec(
        "body_feedback_rate",
        "睡眠/排便/能量反馈率",
        "participation",
        7,
        "higher_is_better",
        "走到第7天的服务单中，前7天留下过睡眠、排便或精神能量反馈证据的比例。",
    ),
    TrendMetricSpec(
        "result_signal_rate",
        "体重/围度/目标结果记录率",
        "outcome",
        28,
        "higher_is_better",
        "走到第28天的服务单中，前28天留下体重、围度或目标结果证据的比例。",
    ),
    TrendMetricSpec(
        "body_improvement_rate",
        "体感改善率",
        "outcome",
        28,
        "higher_is_better",
        "走到第28天的服务单中，前28天出现睡眠、排便或精神能量改善证据的比例。",
    ),
    TrendMetricSpec(
        "sleep_improvement_rate",
        "睡眠质量提升率",
        "outcome",
        28,
        "higher_is_better",
        "走到第28天的服务单中，前28天出现睡眠改善证据的比例。",
    ),
    TrendMetricSpec(
        "renewal_intent_rate",
        "明确复购意向率",
        "commercial",
        28,
        "higher_is_better",
        "走到第28天的服务单中，前28天明确表达续购或继续购买意向的比例。",
    ),
    TrendMetricSpec(
        "refund_intent_rate",
        "退款/退出意向率",
        "commercial",
        28,
        "lower_is_better",
        "走到第28天的服务单中，前28天明确表达退款、退出或不继续意向的比例。",
    ),
    TrendMetricSpec(
        "referral_signal_rate",
        "推荐线索率",
        "commercial",
        28,
        "higher_is_better",
        "走到第28天的服务单中，前28天出现推荐朋友、同事或家人参与线索的比例。",
    ),
)


def _as_local_date(value: datetime, tz: ZoneInfo) -> date:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(tz).date()


def _case_start_date(service_case: ServiceCase, tz: ZoneInfo) -> date | None:
    started = effective_start_at(service_case)
    return _as_local_date(started, tz) if started is not None else None


def _month_start(day: date) -> date:
    return day.replace(day=1)


def _shift_month(day: date, offset: int) -> date:
    index = day.year * 12 + day.month - 1 + offset
    return date(index // 12, index % 12 + 1, 1)


def _in_stage(day: date, start: date, stage_day: int) -> bool:
    return start <= day <= start + timedelta(days=stage_day - 1)


def _by_stage(day: date, start: date, stage_day: int) -> bool:
    return day <= start + timedelta(days=stage_day - 1)


def _message_intent(analysis: dict[str, Any] | None, intent: str) -> bool:
    if not isinstance(analysis, dict):
        return False
    value = analysis.get(f"{intent}_intent")
    return isinstance(value, dict) and bool(value.get("detected"))


async def build_outcome_trend_summary(
    session: AsyncSession,
    *,
    months: int = 6,
    now: datetime | None = None,
) -> dict[str, Any]:
    """返回最近若干服务开始月份的同阶段趋势，查询全部批量执行，无 N+1。"""

    tz = ZoneInfo(get_settings().reminder_timezone)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    today = now.astimezone(tz).date()
    months = min(max(int(months), 2), 12)
    current_month = _month_start(today)
    month_starts = [_shift_month(current_month, offset) for offset in range(1 - months, 1)]
    month_keys = {item.isoformat()[:7] for item in month_starts}

    all_cases = list(
        await session.scalars(
            select(ServiceCase).where(
                active_service_case_condition(),
                or_(
                    ServiceCase.service_plan_start_date.is_not(None),
                    ServiceCase.started_at.is_not(None),
                ),
            )
        )
    )
    start_by_case: dict[int, date] = {}
    cases_by_month: dict[str, list[ServiceCase]] = defaultdict(list)
    for service_case in all_cases:
        start = _case_start_date(service_case, tz)
        if start is None or start > today:
            continue
        month_key = start.isoformat()[:7]
        if month_key not in month_keys:
            continue
        start_by_case[service_case.id] = start
        cases_by_month[month_key].append(service_case)

    case_ids = list(start_by_case)
    profile_dates: dict[int, list[date]] = defaultdict(list)
    document_dates: dict[int, dict[DocumentType, list[date]]] = defaultdict(
        lambda: defaultdict(list)
    )
    snapshot_dates: dict[int, dict[str, list[date]]] = defaultdict(
        lambda: defaultdict(list)
    )
    checkin_dates: dict[int, set[date]] = defaultdict(set)
    message_rows: dict[int, list[tuple[date, dict[str, Any] | None]]] = defaultdict(list)
    outcome_rows: dict[int, list[tuple[date, str, str]]] = defaultdict(list)

    if case_ids:
        for case_id, created_at in (
            await session.execute(
                select(UserProfile.service_case_id, UserProfile.created_at).where(
                    UserProfile.service_case_id.in_(case_ids)
                )
            )
        ).all():
            profile_dates[case_id].append(_as_local_date(created_at, tz))

        for case_id, document_type, created_at in (
            await session.execute(
                select(
                    UserDocument.service_case_id,
                    UserDocument.document_type,
                    UserDocument.created_at,
                ).where(UserDocument.service_case_id.in_(case_ids))
            )
        ).all():
            document_dates[case_id][document_type].append(_as_local_date(created_at, tz))

        for case_id, milestone, created_at in (
            await session.execute(
                select(
                    OutcomeSnapshot.service_case_id,
                    OutcomeSnapshot.milestone,
                    OutcomeSnapshot.created_at,
                ).where(OutcomeSnapshot.service_case_id.in_(case_ids))
            )
        ).all():
            snapshot_dates[case_id][milestone].append(_as_local_date(created_at, tz))

        for case_id, checkin_date in (
            await session.execute(
                select(Checkin.service_case_id, Checkin.checkin_date).where(
                    Checkin.service_case_id.in_(case_ids),
                    Checkin.deleted_at.is_(None),
                )
            )
        ).all():
            checkin_dates[case_id].add(checkin_date)

        for case_id, created_at, analysis_json in (
            await session.execute(
                select(Message.service_case_id, Message.created_at, Message.analysis_json).where(
                    Message.service_case_id.in_(case_ids),
                    Message.direction == MessageDirection.INBOUND,
                    Message.sender_type == MessageSenderType.USER,
                )
            )
        ).all():
            if case_id is not None:
                message_rows[case_id].append(
                    (_as_local_date(created_at, tz), analysis_json)
                )

        for case_id, occurred_at, created_at, field_key, event_type in (
            await session.execute(
                select(
                    OutcomeEvent.service_case_id,
                    OutcomeEvent.occurred_at,
                    OutcomeEvent.created_at,
                    OutcomeEvent.field_key,
                    OutcomeEvent.event_type,
                ).where(OutcomeEvent.service_case_id.in_(case_ids))
            )
        ).all():
            event_at = occurred_at or created_at
            outcome_rows[case_id].append(
                (_as_local_date(event_at, tz), field_key, event_type)
            )

    core_types = {
        DocumentType.ENERGY_RESTART_PRESCRIPTION,
        DocumentType.DAY28_EXECUTION_STANDARD,
    }

    def has_outcome(
        case_id: int,
        start: date,
        stage_day: int,
        fields: set[str],
        *,
        positive_only: bool = False,
    ) -> bool:
        return any(
            field_key in fields
            and _in_stage(event_date, start, stage_day)
            and (not positive_only or event_type in POSITIVE_EVENT_TYPES)
            for event_date, field_key, event_type in outcome_rows.get(case_id, [])
        )

    evaluators: dict[str, Callable[[int, date], bool]] = {
        "profile_completion_rate": lambda cid, start: any(
            _by_stage(day, start, 7) for day in profile_dates.get(cid, [])
        ),
        "core_document_generation_rate": lambda cid, start: all(
            any(_by_stage(day, start, 7) for day in document_dates.get(cid, {}).get(kind, []))
            for kind in core_types
        ),
        "day7_snapshot_rate": lambda cid, start: any(
            _by_stage(day, start, 7)
            for day in snapshot_dates.get(cid, {}).get("day7", [])
        ),
        "day7_checkin_active_rate": lambda cid, start: len(
            {day for day in checkin_dates.get(cid, set()) if _in_stage(day, start, 7)}
        )
        >= 4,
        "day7_conversation_active_rate": lambda cid, start: len(
            {
                day
                for day, _ in message_rows.get(cid, [])
                if _in_stage(day, start, 7)
            }
        )
        >= 3,
        "day28_checkin_completion_rate": lambda cid, start: len(
            {day for day in checkin_dates.get(cid, set()) if _in_stage(day, start, 28)}
        )
        >= 20,
        "body_feedback_rate": lambda cid, start: has_outcome(
            cid, start, 7, BODY_FEEDBACK_FIELDS
        ),
        "result_signal_rate": lambda cid, start: has_outcome(
            cid, start, 28, RESULT_FIELDS
        ),
        "body_improvement_rate": lambda cid, start: has_outcome(
            cid, start, 28, BODY_FEEDBACK_FIELDS, positive_only=True
        ),
        "sleep_improvement_rate": lambda cid, start: has_outcome(
            cid, start, 28, {"sleep_change"}, positive_only=True
        ),
        "renewal_intent_rate": lambda cid, start: any(
            _in_stage(day, start, 28) and _message_intent(analysis, "renewal")
            for day, analysis in message_rows.get(cid, [])
        ),
        "refund_intent_rate": lambda cid, start: any(
            _in_stage(day, start, 28) and _message_intent(analysis, "refund")
            for day, analysis in message_rows.get(cid, [])
        ),
        "referral_signal_rate": lambda cid, start: has_outcome(
            cid, start, 28, {"referral_signal"}
        ),
    }

    cohort_payload = [
        {
            "month": month.isoformat()[:7],
            "label": f"{month.year}年{month.month}月",
            "started_cases": len(cases_by_month.get(month.isoformat()[:7], [])),
            "eligible_day7_cases": sum(
                today >= start_by_case[item.id] + timedelta(days=6)
                for item in cases_by_month.get(month.isoformat()[:7], [])
            ),
            "eligible_day28_cases": sum(
                today >= start_by_case[item.id] + timedelta(days=27)
                for item in cases_by_month.get(month.isoformat()[:7], [])
            ),
        }
        for month in month_starts
    ]

    groups: list[dict[str, Any]] = []
    for group in TREND_GROUPS:
        metrics: list[dict[str, Any]] = []
        for spec in (item for item in TREND_METRICS if item.group == group["key"]):
            points: list[dict[str, Any]] = []
            for month in month_starts:
                month_key = month.isoformat()[:7]
                eligible = [
                    item
                    for item in cases_by_month.get(month_key, [])
                    if today >= start_by_case[item.id] + timedelta(days=spec.stage_day - 1)
                ]
                numerator = sum(
                    bool(evaluators[spec.key](item.id, start_by_case[item.id]))
                    for item in eligible
                )
                denominator = len(eligible)
                status = (
                    "no_sample"
                    if denominator == 0
                    else "small_sample"
                    if denominator < MIN_TREND_SAMPLE
                    else "ok"
                )
                points.append(
                    {
                        "month": month_key,
                        "label": f"{month.month}月",
                        "value": round(numerator * 100 / denominator, 1)
                        if denominator
                        else None,
                        "numerator": numerator,
                        "denominator": denominator,
                        "status": status,
                    }
                )
            metrics.append(
                {
                    "key": spec.key,
                    "label": spec.label,
                    "unit": "%",
                    "stage_day": spec.stage_day,
                    "direction": spec.direction,
                    "description": spec.description,
                    "points": points,
                }
            )
        groups.append({**group, "metrics": metrics})

    return {
        "months": months,
        "cohorts": cohort_payload,
        "groups": groups,
        "history_notes": [
            "每个月代表当月开始服务的一批用户；各指标只比较已经走到同一服务阶段的人。",
            "趋势反映同时期变化，不等于单独证明由 AI 导致；判断调优效果还要结合上线时间与样本结构。",
            "睡眠/排便/能量的负向反馈从 2026-08-08 起才完整收集，早期月份的体感反馈率可能偏低。",
        ],
        "generated_at": now,
    }
