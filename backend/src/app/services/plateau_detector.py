"""平台期检测：7 天体重无动静，或用户主动说体重卡住。

客户口径：平台期 = **一星期体重没动静**，或**用户主动说类似「体重卡住了」**。

实现分成两个信号源：

1. **统计主判据**（确定性计算，不调模型）：最近 7 天窗口内，有效体重记录的
   首尾差值 ≤ ``PLATEAU_MAX_DELTA_KG``（0.5kg，处于正常日波动范围内）→ 平台期。
   复用 ``body_change_stats`` 的入营基线锚定与跳变剔除思路，避免把「这周只称了两次
   而且一次写错单位」的用户误判成平台期。
2. **用户自述旁证**：档案官已经按既有抽取链路把「体重没动/平台期」上报为
   ``outcome_events(field_key='weight_change', event_type='unchanged')`` 的结构化事件，
   这里直接读该事件（最近 7 天内）作为「用户主动说体重卡住」的信号，不做关键词硬编码。

数据不足（窗口内有效体重记录 < 2 条、也没有用户自述）→ 标记 ``insufficient_data``，
不误报平台期——运营看到的列表应该只有「真平台期」，没数据的人需要先解决打卡问题。

结果写入 ``service_cases`` 的三个字段（``plateau_status`` / ``plateau_evidence`` /
``plateau_updated_at``），由调度器天闸周期扫描，纯 DB 派生、不占消息热路径。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.checkin import Checkin
from app.models.outcome import OutcomeEvent
from app.models.service_case import ServiceCase
from app.services.body_change_stats import load_intake_baselines
from app.services.body_metrics import parse_weight_kg
from app.services.service_case_queries import active_service_case_condition

logger = logging.getLogger(__name__)

# 观察窗口：客户说「一星期」，取最近 7 个自然日（含今天）。
PLATEAU_WINDOW_DAYS = 7
# 窗口内首尾体重差 ≤0.5kg 视为「没动静」（正常日波动范围内）。
PLATEAU_MAX_DELTA_KG = 0.5
# 窗口内至少 2 条有效体重记录才能下统计结论。
MIN_VALID_WEIGHT_ROWS = 2
# 首尾两次有效打卡必须隔满一周（至少 6 个自然日），才足以支撑「一星期没动静」的结论；
# 否则（如连着两天打卡）只是称了两次，不能说明体重卡了一周。
PLATEAU_MIN_SPAN_DAYS = 6
# 相邻跳变剔除：复用 body_change_stats 的口径（日内波动 + 可能真实变化的和）。
_MAX_WEIGHT_DELTA_PER_DAY = 0.5
_MIN_WEIGHT_DELTA_ALLOWANCE_KG = 2.5

# 状态值
STATUS_NORMAL = "normal"
STATUS_PLATEAU = "plateau"
STATUS_INSUFFICIENT_DATA = "insufficient_data"


@dataclass(frozen=True)
class PlateauResult:
    status: str
    evidence: dict | None = None


@dataclass(frozen=True)
class ScanStats:
    scanned: int
    plateau: int
    insufficient: int
    declared: int


def _tz() -> ZoneInfo:
    return ZoneInfo(get_settings().reminder_timezone)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


async def scan_plateaus(session: AsyncSession) -> ScanStats:
    """扫一遍所有未软删除服务单，把平台期状态写入 service_cases 的三个字段。

    服务状态只代表生命周期，不作为平台期判定前置条件；只要服务单仍保留且有近期
    体重打卡或用户自述，就可以进入平台期计算。
    """
    cases = list(
        await session.scalars(
            select(ServiceCase).where(active_service_case_condition())
        )
    )
    if not cases:
        return ScanStats(scanned=0, plateau=0, insufficient=0, declared=0)
    case_ids = [case.id for case in cases]

    baselines = await load_intake_baselines(session, case_ids)
    checkins_by_case: dict[int, list[Checkin]] = {}
    for checkin in await session.scalars(
        select(Checkin)
        .where(Checkin.service_case_id.in_(case_ids), Checkin.deleted_at.is_(None))
        .order_by(Checkin.checkin_date.asc(), Checkin.created_at.asc())
    ):
        checkins_by_case.setdefault(checkin.service_case_id, []).append(checkin)

    # 用户自述「体重没动/平台期」：档案官抽取的 unchanged 成效事件（最近 7 天）。
    declared_by_case: dict[int, list[str]] = {}
    window_start = _now_utc() - timedelta(days=PLATEAU_WINDOW_DAYS)
    for event in await session.scalars(
        select(OutcomeEvent).where(
            OutcomeEvent.service_case_id.in_(case_ids),
            OutcomeEvent.field_key == "weight_change",
            OutcomeEvent.event_type == "unchanged",
            OutcomeEvent.occurred_at >= window_start,
        )
    ):
        evidence = (event.evidence_text or "").strip()
        if evidence:
            declared_by_case.setdefault(event.service_case_id, []).append(evidence)

    window_start_date = _window_start()
    window_end_date = _window_end()
    plateau_count = insufficient_count = declared_count = 0
    for case in cases:
        result = _evaluate_plateau(
            checkins_by_case.get(case.id, []),
            baseline_kg=baselines.get(case.id, (None, None))[0],
            declared_evidence=declared_by_case.get(case.id, []),
            window_start=window_start_date,
            window_end=window_end_date,
        )
        case.plateau_status = result.status
        case.plateau_evidence = result.evidence
        case.plateau_updated_at = _now_utc()
        if result.status == STATUS_PLATEAU:
            plateau_count += 1
        elif result.status == STATUS_INSUFFICIENT_DATA:
            insufficient_count += 1
        if result.evidence and result.evidence.get("user_declared"):
            declared_count += 1

    await session.commit()
    return ScanStats(
        scanned=len(cases),
        plateau=plateau_count,
        insufficient=insufficient_count,
        declared=declared_count,
    )


def _evaluate_plateau(
    checkin_rows: list[Checkin],
    *,
    baseline_kg: float | None,
    declared_evidence: list[str],
    window_start: date,
    window_end: date,
) -> PlateauResult:
    """单个服务单的平台期判定（纯函数，便于单测）。"""

    usable: list[tuple[date, float]] = []
    for row in checkin_rows:
        payload = row.parsed_json or {}
        raw = payload.get("weight")
        if raw in (None, ""):
            continue
        parsed = parse_weight_kg(raw, baseline_kg=baseline_kg)
        if parsed.ok and parsed.value is not None:
            usable.append((row.checkin_date, parsed.value))
    usable = _drop_implausible_jumps(usable)

    in_window = [(day, value) for day, value in usable if window_start <= day <= window_end]

    user_declared = bool(declared_evidence)

    span_days = 0
    if len(in_window) >= MIN_VALID_WEIGHT_ROWS:
        first_day = in_window[0][0]
        last_day = in_window[-1][0]
        span_days = (last_day - first_day).days

    if not user_declared and (len(in_window) < MIN_VALID_WEIGHT_ROWS or span_days < PLATEAU_MIN_SPAN_DAYS):
        note = (
            "窗口内有效体重记录不足，无法判定平台期"
            if len(in_window) < MIN_VALID_WEIGHT_ROWS
            else "窗口内两次打卡未隔满一周，不足以说明体重卡了一周"
        )
        return PlateauResult(
            STATUS_INSUFFICIENT_DATA,
            {
                "basis": [],
                "user_declared": False,
                "window_start": window_start.isoformat(),
                "window_end": window_end.isoformat(),
                "valid_weight_rows": len(in_window),
                "note": note,
            },
        )

    delta_kg: float | None = None
    first_weight: float | None = None
    last_weight: float | None = None
    if len(in_window) >= MIN_VALID_WEIGHT_ROWS and span_days >= PLATEAU_MIN_SPAN_DAYS:
        first_day, first_weight = in_window[0]
        last_day, last_weight = in_window[-1]
        delta_kg = round(last_weight - first_weight, 2)

    basis: list[str] = []
    if delta_kg is not None and abs(delta_kg) <= PLATEAU_MAX_DELTA_KG:
        basis.append("statistical")
    if user_declared:
        basis.append("user_declared")

    evidence: dict = {
        "basis": basis,
        "user_declared": user_declared,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "valid_weight_rows": len(in_window),
        "first_weight_kg": first_weight,
        "last_weight_kg": last_weight,
        "delta_kg": delta_kg,
        "declared_evidence": declared_evidence[:3],
    }
    return PlateauResult(STATUS_PLATEAU if basis else STATUS_NORMAL, evidence)


def _window_start() -> date:
    return _now_utc().astimezone(_tz()).date() - timedelta(days=PLATEAU_WINDOW_DAYS - 1)


def _window_end() -> date:
    return _now_utc().astimezone(_tz()).date()


def _drop_implausible_jumps(
    usable: list[tuple[date, float]],
) -> list[tuple[date, float]]:
    """剔除相对上一条有效记录出现生理上不可能跳变的那条（复用成效统计口径）。"""

    cleaned: list[tuple[date, float]] = []
    for day, value in usable:
        if cleaned:
            prev_day, prev_value = cleaned[-1]
            span = max((day - prev_day).days, 1)
            if abs(value - prev_value) > _MIN_WEIGHT_DELTA_ALLOWANCE_KG + _MAX_WEIGHT_DELTA_PER_DAY * span:
                continue
        cleaned.append((day, value))
    return cleaned
