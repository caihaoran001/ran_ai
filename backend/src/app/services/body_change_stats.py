"""体重/腰围变化的分窗口统计：把打卡记录变成可对外讲的成效数字。

为什么不能沿用原来那一个「28天平均变化」：

1. **起点不可比**。原算法拿"这个人第一次称的体重"当起点——第 10 天才第一次上秤的人，
   他的"28天变化"实际只覆盖 18 天，和别人放在一个平均数里没有意义。这里改用入营问卷
   填的起始体重/腰围当第 0 天，问卷缺失才退到服务前三天的打卡值，再缺失就不纳入。
2. **一个平均数看不出分化**。前 7 天几乎所有人都能坚持，第二周开始分层，
   所以 3/7/14/28 四个窗口分别出数。
3. **正负对冲 + 脏值主导**。均值会把减重的人和增重的人抵消掉，小样本下还会被
   个别脏值整体带偏（线上 8 个腰围样本里两个脏值就把均值推成 +9.1cm）。
   所以对外主口径用中位数，均值放详情页，并且每个数字都要能说清样本量和剔除原因。

本模块只产出"每个服务单在每个窗口的变化明细"和纯函数聚合工具，
人群分层（年龄段/减重带/客户类型）由调用方套标签后再聚合，避免统计层依赖画像口径。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from statistics import mean, median
from typing import Any, Iterable, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.checkin import Checkin
from app.models.questionnaire import QuestionnaireSubmission
from app.models.service_case import ServiceCase
from app.services.body_metrics import BodyValue, parse_waist_cm, parse_weight_kg
from app.services.intake_facts import build_intake_fact_summary
from app.services.questionnaire_template import get_active_questionnaire_fields
from app.services.service_case_queries import (
    active_service_case_condition,
    effective_service_day,
    effective_start_at,
)

# 客户要的四个窗口：前 7 天大家坚持度都还行，之后开始分化。
WINDOW_DAYS: tuple[int, ...] = (3, 7, 14, 28)

# 窗口末取值的容差：不是所有人都恰好在第 N 天称重。
_ENDPOINT_TOLERANCE = 2
# 问卷基线缺失时，允许把服务前三天的打卡值当起点——再往后就不算"起点"了。
_FALLBACK_BASELINE_MAX_DAY = 3

# 变化幅度上限：超过就不是真实变化，而是没被前面拦住的录入问题。
# 每天 0.5kg / 0.4cm 已经远高于健康减重速度，再加一个绝对上限兜住长窗口。
_MAX_WEIGHT_DELTA_PER_DAY = 0.5
_MAX_WEIGHT_DELTA_KG = 12.0
_MAX_WAIST_DELTA_PER_DAY = 0.4
_MAX_WAIST_DELTA_CM = 15.0

# 未纳入原因（case 级）
EXCLUDE_NO_BASELINE = "no_baseline"  # 既没问卷起始值，前三天也没有效打卡
EXCLUDE_NO_ENDPOINT = "no_endpoint"  # 窗口末附近没有可用记录
EXCLUDE_IMPLAUSIBLE = "implausible_delta"  # 变化幅度不可能，判定为录入问题
EXCLUDE_BELOW_RATE = "below_checkin_rate"  # 没达到打卡率门槛
INCLUDED = "ok"


@dataclass(frozen=True)
class MetricOutcome:
    """某人某窗口某指标的结论。``delta is None`` 时 ``status`` 说明为什么没纳入。"""

    delta: float | None
    status: str
    baseline: float | None = None
    endpoint: float | None = None
    baseline_source: str | None = None  # questionnaire / checkin
    unit_verified: bool = False


@dataclass(frozen=True)
class CaseWindowChange:
    service_case_id: int
    window_day: int
    service_day: int
    checkin_days: int
    checkin_rate: float
    weight: MetricOutcome
    waist: MetricOutcome


@dataclass
class RecordQuality:
    """记录级解析质量：打卡里填了多少条、其中多少条用不了、为什么。"""

    weight_reasons: dict[str, int]
    waist_reasons: dict[str, int]


def _tz() -> ZoneInfo:
    return ZoneInfo(get_settings().reminder_timezone)


def _service_day_of(service_case: ServiceCase, day: date, tz: ZoneInfo) -> int:
    """某个日历日是这个服务单的第几天（与成效引擎同一锚点：计划开始日优先）。"""

    anchor = effective_start_at(service_case) or service_case.created_at
    if anchor is None:
        return 1
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=ZoneInfo("UTC"))
    return (day - anchor.astimezone(tz).date()).days + 1


async def load_intake_baselines(
    session: AsyncSession,
    case_ids: Sequence[int],
) -> dict[int, tuple[float | None, float | None]]:
    """每个服务单入营问卷填的起始体重(kg)与腰围(cm)。

    这是判断打卡裸数字单位的唯一锚：``151.6`` 对基线 ``75.8`` 是两倍关系就是斤。
    """

    if not case_ids:
        return {}
    fields = await get_active_questionnaire_fields(session, "intake")
    latest: dict[int, QuestionnaireSubmission] = {}
    for submission in await session.scalars(
        select(QuestionnaireSubmission)
        .where(
            QuestionnaireSubmission.service_case_id.in_(list(case_ids)),
            QuestionnaireSubmission.template_key == "intake",
        )
        .order_by(
            QuestionnaireSubmission.service_case_id,
            QuestionnaireSubmission.created_at.desc(),
        )
        .distinct(QuestionnaireSubmission.service_case_id)
    ):
        latest[submission.service_case_id] = submission

    baselines: dict[int, tuple[float | None, float | None]] = {}
    for case_id, submission in latest.items():
        answers = submission.answers_json if isinstance(submission.answers_json, dict) else {}
        facts = build_intake_fact_summary(fields, answers)
        baselines[case_id] = (_float_or_none(facts.get("weight_kg")), _float_or_none(facts.get("waist")))
    return baselines


def _float_or_none(value: Any) -> float | None:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


async def compute_case_window_changes(
    session: AsyncSession,
    *,
    now: datetime,
) -> tuple[list[CaseWindowChange], RecordQuality]:
    """算出每个有效服务单在每个窗口的体重/腰围变化明细（不做门槛过滤）。

    只包含服务天数已经走到该窗口的人：第 10 天的用户不会出现在 28 天窗口的分母里，
    否则"还没到时间"会被当成"没效果"。
    """

    tz = _tz()
    cases = list(
        await session.scalars(select(ServiceCase).where(active_service_case_condition()))
    )
    case_ids = [case.id for case in cases]
    if not case_ids:
        return [], RecordQuality({}, {})

    baselines = await load_intake_baselines(session, case_ids)
    checkins_by_case: dict[int, list[Checkin]] = {}
    for checkin in await session.scalars(
        select(Checkin)
        .where(Checkin.service_case_id.in_(case_ids), Checkin.deleted_at.is_(None))
        .order_by(Checkin.checkin_date.asc(), Checkin.created_at.asc())
    ):
        checkins_by_case.setdefault(checkin.service_case_id, []).append(checkin)

    weight_reasons: dict[str, int] = {}
    waist_reasons: dict[str, int] = {}
    changes: list[CaseWindowChange] = []

    local_now = now.astimezone(tz)
    for case in cases:
        # 服务天数按北京日期算：传 UTC 的 now 会在每天 UTC 16:00 之后少算一天，
        # 让刚满 28 天的人被挡在窗口外。
        service_day = effective_service_day(case, local_now, tz)
        if service_day <= 0:
            continue
        rows = checkins_by_case.get(case.id, [])
        baseline_weight, baseline_waist = baselines.get(case.id, (None, None))

        # 每条打卡只解析一次，记录级质量统计在这里累计（同一批记录会被四个窗口复用）。
        parsed: list[tuple[int, BodyValue | None, BodyValue | None]] = []
        for row in rows:
            payload = row.parsed_json or {}
            day = _service_day_of(case, row.checkin_date, tz)
            weight_raw = payload.get("weight")
            waist_raw = payload.get("waist")
            weight_value = (
                parse_weight_kg(weight_raw, baseline_kg=baseline_weight)
                if weight_raw not in (None, "")
                else None
            )
            waist_value = (
                parse_waist_cm(waist_raw, baseline_cm=baseline_waist)
                if waist_raw not in (None, "")
                else None
            )
            if weight_value is not None:
                weight_reasons[weight_value.reason] = weight_reasons.get(weight_value.reason, 0) + 1
            if waist_value is not None:
                waist_reasons[waist_value.reason] = waist_reasons.get(waist_value.reason, 0) + 1
            parsed.append((day, weight_value, waist_value))

        all_checkin_days = {day for day, _, _ in parsed if day >= 1}

        for window in WINDOW_DAYS:
            if service_day < window:
                continue
            days_in_window = len({day for day in all_checkin_days if 1 <= day <= window})
            changes.append(
                CaseWindowChange(
                    service_case_id=case.id,
                    window_day=window,
                    service_day=service_day,
                    checkin_days=days_in_window,
                    checkin_rate=round(days_in_window / window, 4),
                    weight=_metric_outcome(
                        [(day, value) for day, value, _ in parsed],
                        window=window,
                        questionnaire_baseline=baseline_weight,
                        max_delta=min(_MAX_WEIGHT_DELTA_PER_DAY * window, _MAX_WEIGHT_DELTA_KG),
                    ),
                    waist=_metric_outcome(
                        [(day, value) for day, _, value in parsed],
                        window=window,
                        questionnaire_baseline=baseline_waist,
                        max_delta=min(_MAX_WAIST_DELTA_PER_DAY * window, _MAX_WAIST_DELTA_CM),
                    ),
                )
            )

    return changes, RecordQuality(weight_reasons, waist_reasons)


def _metric_outcome(
    series: list[tuple[int, BodyValue | None]],
    *,
    window: int,
    questionnaire_baseline: float | None,
    max_delta: float,
) -> MetricOutcome:
    usable = [(day, value) for day, value in series if value is not None and value.ok]

    baseline = questionnaire_baseline
    baseline_source = "questionnaire" if baseline else None
    unit_verified = True if baseline else False
    if baseline is None:
        early = [
            (day, value)
            for day, value in usable
            if 1 <= day <= _FALLBACK_BASELINE_MAX_DAY
        ]
        if early:
            day, value = min(early, key=lambda item: item[0])
            baseline = value.value
            baseline_source = "checkin"
            unit_verified = value.unit_verified
    if baseline is None:
        return MetricOutcome(None, EXCLUDE_NO_BASELINE)

    lower = max(window - _ENDPOINT_TOLERANCE, 2)
    upper = window + _ENDPOINT_TOLERANCE
    candidates = [(day, value) for day, value in usable if lower <= day <= upper]
    if not candidates:
        return MetricOutcome(None, EXCLUDE_NO_ENDPOINT, baseline=baseline, baseline_source=baseline_source)
    # 最接近窗口末的那次记录；同样接近时取更晚的一次。
    day, endpoint_value = min(candidates, key=lambda item: (abs(item[0] - window), -item[0]))
    endpoint = endpoint_value.value
    delta = round(endpoint - baseline, 2)
    if abs(delta) > max_delta:
        return MetricOutcome(
            None,
            EXCLUDE_IMPLAUSIBLE,
            baseline=baseline,
            endpoint=endpoint,
            baseline_source=baseline_source,
        )
    return MetricOutcome(
        delta,
        INCLUDED,
        baseline=baseline,
        endpoint=endpoint,
        baseline_source=baseline_source,
        unit_verified=unit_verified and endpoint_value.unit_verified,
    )


def summarize_deltas(values: Iterable[float]) -> dict[str, Any]:
    """一组变化值的对外口径：中位数为主，均值并列给出。"""

    data = [float(item) for item in values]
    if not data:
        return {"sample_count": 0, "median": None, "mean": None, "best": None, "worst": None}
    return {
        "sample_count": len(data),
        "median": round(median(data), 2),
        "mean": round(mean(data), 2),
        "best": round(min(data), 2),  # 减得最多
        "worst": round(max(data), 2),
    }
