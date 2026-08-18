"""平台期检测：以最后一次称重为锚往前看一周，数据够就让数据说话。

客户口径：平台期 = **一星期体重没动静**，或**用户主动说类似「体重卡住了」**。

判定顺序（先数据后自述，证据强度写进 evidence）：

1. **窗口锚在最后一次有效称重**（``PLATEAU_WINDOW_DAYS`` 天，含当天），不跟着日历滑动。
   判定只在有新体重时才可能产生新结论——时间往前走只能让旧结论失效，不能凭空造出新结论。
2. **数据够不够**：窗口内至少 ``MIN_WEIGHT_DAYS_IN_WINDOW`` 天有有效体重，且任何位置
   不出现连续超过 ``MAX_GAP_DAYS`` 天没有体重。够 → 用最小二乘趋势线算这一周的变化量
   下结论（不是首尾两点相减，单次异常读数不该独自翻转结论）：
   ``PLATEAU_MAX_DELTA_KG`` 以内算没动静、涨过 ``REBOUND_MIN_TREND_KG`` 算体重反弹；
   趋势平但窗口内最高最低差超过 ``MAX_SPREAD_KG`` 的是波动，也不算平台期。
3. **数据不够/没有/已过期** → 才轮到用户自述。档案官已按既有抽取链路把「体重没动/平台期」
   上报为 ``outcome_events(field_key='weight_change', event_type='unchanged')``，这里直接读该
   事件（最近 7 天内）作为自述信号，不做关键词硬编码；更新的改善/达成/变差事件，或自述之后
   超过阈值的新称重变化，会解除更早的自述。
4. **结论存活期**：最后一次有效称重距今超过 ``MAX_GAP_DAYS`` 天（= 连续三天没体重）→
   结论作废回 ``insufficient_data``；这时既不知道现在什么情况，就不该继续挂着旧标签。

数据充足、却显示体重在明显变化，而用户仍自述卡住时：结论以数据为准（``normal``，不进名单），
但 evidence 标记 ``declaration_conflicts_data`` 并保留原话——这类人多半是期望落差
（实际掉秤了自己觉得没动），运营从详情页能看到，值得单独沟通。

结果写入 ``service_cases`` 的三个字段（``plateau_status`` / ``plateau_evidence`` /
``plateau_updated_at``），由调度器每天定点扫描，纯 DB 派生、不占消息热路径；
**结论没有变化就不写库**，避免每次扫描刷新全部服务单的 ``updated_at``（后台多处按它排序、
「生成中卡住」补跑也用它当判据）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.checkin import Checkin
from app.models.outcome import OutcomeEvent
from app.models.service_case import ServiceCase
from app.services.body_change_stats import load_intake_baselines
from app.services.body_metrics import parse_weight_kg
from app.services.service_case_queries import active_service_case_condition

logger = logging.getLogger(__name__)

# 观察窗口：客户说「一星期」，取最后一次称重往前 7 个自然日（含当天）。
PLATEAU_WINDOW_DAYS = 7
# 一周变化 ≤0.5kg 视为「没动静」（正常日波动范围内）。
PLATEAU_MAX_DELTA_KG = 0.5
# 一周涨 ≥1.0kg 判为体重反弹。0.5~1.0 之间留缓冲不惊动运营：晨起空腹的正常波动就有半公斤
# 上下，经期水肿能到一两公斤，门槛压太低会让名单被噪声淹掉。
REBOUND_MIN_TREND_KG = 1.0
# 窗口内最高最低差 >2.0kg 说明是波动不是「卡住」，不判平台期（趋势线对 U 形摆动是平的，
# 只看趋势会把一周掉 4 斤又涨回来的人算成平台期）。
MAX_SPREAD_KG = 2.0
# 窗口内至少 4 天有有效体重，才够代表这一周。
MIN_WEIGHT_DAYS_IN_WINDOW = 4
# 允许连续空缺的天数上限：连续 3 天没有体重就不作数。
# 落在窗口里 → 这次判定不成立；落在判定之后 → 已有结论作废。
MAX_GAP_DAYS = 2
# 用户自述的有效期（天）。
DECLARATION_WINDOW_DAYS = 7
# 打卡回溯上限：判定只用一周，多读的部分是给单位跳变剔除当锚点用的。
CHECKIN_LOOKBACK_DAYS = 40
# 相邻跳变剔除：复用 body_change_stats 的口径（日内波动 + 可能真实变化的和）。
_MAX_WEIGHT_DELTA_PER_DAY = 0.5
_MIN_WEIGHT_DELTA_ALLOWANCE_KG = 2.5

# 状态值
STATUS_NORMAL = "normal"
STATUS_PLATEAU = "plateau"
STATUS_REBOUND = "rebound"
STATUS_INSUFFICIENT_DATA = "insufficient_data"

# 这些成效方向明确说明“体重仍未变化”的旧自述已经不是最新结论。
_DECLARATION_RESOLVING_EVENT_TYPES = frozenset({"improved", "completed", "worsened"})
_WEIGHT_OUTCOME_EVENT_TYPES = _DECLARATION_RESOLVING_EVENT_TYPES | {"unchanged"}


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
    changed: int = 0
    rebound: int = 0


@dataclass(frozen=True)
class WeightOutcomeSignal:
    event_type: str
    evidence_text: str
    occurred_at: datetime


def _tz() -> ZoneInfo:
    return ZoneInfo(get_settings().reminder_timezone)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _today() -> date:
    return _now_utc().astimezone(_tz()).date()


def plateau_followup_pending_condition():
    """审核控制台「体重状态跟进」待办的唯一口径：当前是平台期或反弹，且还没跟进过，
    或者跟进之后结论又变过一次（离开平台期又回来、平台期转成反弹等）。

    计数（侧边栏红点）与列表必须共用这一个条件，否则会出现「列表 3 条、红点不算这 3 条」。
    结论一直没变就保持已处理，红点因此能归零——不然运营很快学会无视整个红点。
    """

    return and_(
        ServiceCase.plateau_status.in_((STATUS_PLATEAU, STATUS_REBOUND)),
        or_(
            ServiceCase.plateau_followed_up_at.is_(None),
            and_(
                ServiceCase.plateau_updated_at.is_not(None),
                ServiceCase.plateau_followed_up_at < ServiceCase.plateau_updated_at,
            ),
        ),
    )


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
        return ScanStats(scanned=0, plateau=0, insufficient=0, declared=0, changed=0)
    case_ids = [case.id for case in cases]

    today = _today()
    baselines = await load_intake_baselines(session, case_ids)
    checkins_by_case: dict[int, list[Checkin]] = {}
    for checkin in await session.scalars(
        select(Checkin)
        .where(
            Checkin.service_case_id.in_(case_ids),
            Checkin.deleted_at.is_(None),
            Checkin.checkin_date >= today - timedelta(days=CHECKIN_LOOKBACK_DAYS),
        )
        .order_by(Checkin.checkin_date.asc(), Checkin.created_at.asc())
    ):
        checkins_by_case.setdefault(checkin.service_case_id, []).append(checkin)

    # 最近 7 天体重成效事件：unchanged 可作为自述，更新的 improved/completed/worsened
    # 会解除更早的 unchanged，避免旧自述长期压过后续真实结果。
    outcome_signals_by_case: dict[int, list[WeightOutcomeSignal]] = {}
    declaration_start = _now_utc() - timedelta(days=DECLARATION_WINDOW_DAYS)
    for event in await session.scalars(
        select(OutcomeEvent)
        .where(
            OutcomeEvent.service_case_id.in_(case_ids),
            OutcomeEvent.field_key == "weight_change",
            OutcomeEvent.event_type.in_(_WEIGHT_OUTCOME_EVENT_TYPES),
            OutcomeEvent.occurred_at >= declaration_start,
        )
        .order_by(OutcomeEvent.occurred_at.asc(), OutcomeEvent.id.asc())
    ):
        evidence = (event.evidence_text or "").strip()
        if evidence and event.occurred_at is not None:
            outcome_signals_by_case.setdefault(event.service_case_id, []).append(
                WeightOutcomeSignal(
                    event_type=event.event_type,
                    evidence_text=evidence,
                    occurred_at=event.occurred_at,
                )
            )

    now = _now_utc()
    plateau_count = insufficient_count = declared_count = changed_count = 0
    rebound_count = 0
    for case in cases:
        result = _evaluate_plateau(
            checkins_by_case.get(case.id, []),
            baseline_kg=baselines.get(case.id, (None, None))[0],
            outcome_signals=outcome_signals_by_case.get(case.id, []),
            today=today,
        )
        # 结论没变就不碰这一行：service_cases.updated_at 是后台列表排序和
        # 「生成中卡住超时补跑」的判据，不该被每天的派生计算刷掉。
        if case.plateau_status != result.status or case.plateau_evidence != result.evidence:
            case.plateau_status = result.status
            case.plateau_evidence = result.evidence
            case.plateau_updated_at = now
            changed_count += 1
        if result.status == STATUS_PLATEAU:
            plateau_count += 1
        elif result.status == STATUS_REBOUND:
            rebound_count += 1
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
        changed=changed_count,
        rebound=rebound_count,
    )


def _evaluate_plateau(
    checkin_rows: list[Checkin],
    *,
    baseline_kg: float | None,
    outcome_signals: list[WeightOutcomeSignal],
    today: date,
) -> PlateauResult:
    """单个服务单的平台期判定（纯函数，便于单测）。"""

    usable = _drop_implausible_jumps(_usable_weights(checkin_rows, baseline_kg))
    anchor_day = usable[-1][0] if usable else None
    days_since_last = (today - anchor_day).days if anchor_day is not None else None

    window_start: date | None = None
    in_window: list[tuple[date, float]] = []
    if anchor_day is not None:
        window_start = anchor_day - timedelta(days=PLATEAU_WINDOW_DAYS - 1)
        in_window = [
            (day, value) for day, value in usable if window_start <= day <= anchor_day
        ]
    weight_days = len({day for day, _ in in_window})
    max_gap_days = _max_gap_days(in_window, window_start)

    # 最后一次称重距今超过容忍天数 → 数据描述的已经不是「现在」，结论作废。
    expired = days_since_last is not None and days_since_last > MAX_GAP_DAYS
    sufficient = (
        not expired
        and weight_days >= MIN_WEIGHT_DAYS_IN_WINDOW
        and max_gap_days is not None
        and max_gap_days <= MAX_GAP_DAYS
    )

    (
        user_declared,
        latest_declaration,
        declared_superseded_by,
        post_declared_delta_kg,
    ) = _resolve_user_declaration(outcome_signals, in_window)

    basis: list[str] = []
    conflicts_data = False
    # 结论是「正常」但仍有话要说的两种情况，供详情页决定要不要露出这块面板：
    # swing=一周大起大落（是波动不是卡住）、slight_gain=略有上浮但未到反弹门槛。
    attention: str | None = None
    delta_kg: float | None = None
    trend_kg: float | None = None
    spread_kg: float | None = None
    first_weight: float | None = None
    last_weight: float | None = None

    if sufficient:
        first_weight = in_window[0][1]
        last_weight = in_window[-1][1]
        delta_kg = round(last_weight - first_weight, 2)
        trend_kg = _trend_delta_kg(in_window)
        values = [value for _, value in in_window]
        spread_kg = round(max(values) - min(values), 2)
        span = f"{window_start.isoformat()}~{anchor_day.isoformat()}"
        basis.append("statistical")
        if trend_kg >= REBOUND_MIN_TREND_KG:
            status = STATUS_REBOUND
            note = f"{span} 共 {weight_days} 天有体重，趋势 +{trend_kg} kg"
        elif abs(trend_kg) <= PLATEAU_MAX_DELTA_KG and spread_kg <= MAX_SPREAD_KG:
            status = STATUS_PLATEAU
            if user_declared:
                basis.append("user_declared")
            note = f"{span} 共 {weight_days} 天有体重，趋势 {trend_kg:+} kg"
        elif abs(trend_kg) <= PLATEAU_MAX_DELTA_KG:
            # 趋势是平的但一周里大起大落：这是波动，不是卡住。
            status = STATUS_NORMAL
            attention = "swing"
            note = f"{span} 趋势 {trend_kg:+} kg，但最高最低相差 {spread_kg} kg，属波动而非平台期"
        else:
            status = STATUS_NORMAL
            note = f"{span} 趋势 {trend_kg:+} kg"
            if trend_kg > PLATEAU_MAX_DELTA_KG:
                attention = "slight_gain"
                note += "，略有上浮但未到反弹门槛"
        # 数据够就以数据为准：自述压不过实测。冲突只记录，不改变结论。
        if status != STATUS_PLATEAU and user_declared:
            conflicts_data = True
            note += "，与用户自述「体重卡住」不符"
    elif user_declared:
        status = STATUS_PLATEAU
        basis.append("user_declared")
        note = "用户口述平台期；" + _data_shortfall_note(
            anchor_day=anchor_day,
            days_since_last=days_since_last,
            weight_days=weight_days,
            max_gap_days=max_gap_days,
            expired=expired,
        )
    else:
        status = STATUS_INSUFFICIENT_DATA
        note = _data_shortfall_note(
            anchor_day=anchor_day,
            days_since_last=days_since_last,
            weight_days=weight_days,
            max_gap_days=max_gap_days,
            expired=expired,
        )

    evidence: dict = {
        "basis": basis,
        "user_declared": user_declared,
        "data_sufficient": sufficient,
        "window_start": window_start.isoformat() if window_start else None,
        "window_end": anchor_day.isoformat() if anchor_day else None,
        "last_weight_date": anchor_day.isoformat() if anchor_day else None,
        "days_since_last_weight": days_since_last,
        "valid_weight_rows": weight_days,
        "max_gap_days": max_gap_days,
        "first_weight_kg": first_weight,
        "last_weight_kg": last_weight,
        "delta_kg": delta_kg,
        "trend_kg": trend_kg,
        "spread_kg": spread_kg,
        "declared_evidence": (
            [latest_declaration.evidence_text] if latest_declaration is not None else []
        ),
        "declared_at": (
            latest_declaration.occurred_at.isoformat()
            if latest_declaration is not None
            else None
        ),
        "declared_superseded_by": declared_superseded_by,
        "post_declared_delta_kg": post_declared_delta_kg,
        "declaration_conflicts_data": conflicts_data,
        "attention": attention,
        "note": note,
    }
    return PlateauResult(status, evidence)


def _data_shortfall_note(
    *,
    anchor_day: date | None,
    days_since_last: int | None,
    weight_days: int,
    max_gap_days: int | None,
    expired: bool,
) -> str:
    """说清楚这次为什么不能用体重数据下结论，供运营在详情页核对。"""

    if anchor_day is None:
        return "无有效体重打卡数据"
    if expired:
        return (
            f"最后一次有效体重打卡 {anchor_day.isoformat()}，距今 {days_since_last} 天，"
            "已连续三天没有体重"
        )
    if weight_days < MIN_WEIGHT_DAYS_IN_WINDOW:
        return (
            f"近 {PLATEAU_WINDOW_DAYS} 天仅 {weight_days} 天有有效体重"
            f"（需至少 {MIN_WEIGHT_DAYS_IN_WINDOW} 天）"
        )
    return (
        f"近 {PLATEAU_WINDOW_DAYS} 天内出现连续 {max_gap_days} 天没有体重"
        f"（超过 {MAX_GAP_DAYS} 天）"
    )


def _trend_delta_kg(in_window: list[tuple[date, float]]) -> float:
    """这一周体重的走向：最小二乘趋势线从首次称重走到末次称重抬升/下降了多少公斤。

    比首尾两点相减稳：全部称重都参与，单次异常读数（穿衣称、前一晚吃咸了）不会独自
    翻转结论；横轴用真实日期，所以隔天称、漏一天都不需要补点。点少时它自然退化成接近
    首尾差——真正扛事的是「窗口内至少 4 天有体重」那道门槛。
    """

    days = [(day - in_window[0][0]).days for day, _ in in_window]
    values = [value for _, value in in_window]
    mean_day = sum(days) / len(days)
    mean_value = sum(values) / len(values)
    covariance = sum(
        (day - mean_day) * (value - mean_value) for day, value in zip(days, values)
    )
    variance = sum((day - mean_day) ** 2 for day in days)
    if variance <= 0:  # 同一天的记录不可能出现（每天最多一条），兜底不除零
        return 0.0
    return round(covariance / variance * (days[-1] - days[0]), 2)


def _usable_weights(
    checkin_rows: list[Checkin],
    baseline_kg: float | None,
) -> list[tuple[date, float]]:
    usable: list[tuple[date, float]] = []
    for row in checkin_rows:
        payload = row.parsed_json or {}
        raw = payload.get("weight")
        if raw in (None, ""):
            continue
        parsed = parse_weight_kg(raw, baseline_kg=baseline_kg)
        if parsed.ok and parsed.value is not None:
            usable.append((row.checkin_date, parsed.value))
    usable.sort(key=lambda item: item[0])
    return usable


def _max_gap_days(
    in_window: list[tuple[date, float]],
    window_start: date | None,
) -> int | None:
    """窗口内最长的「连续几天没有体重」。

    窗口末尾就是最后一次称重当天，所以只需要看开头那段和中间各段。
    """

    if not in_window or window_start is None:
        return None
    days = sorted({day for day, _ in in_window})
    gap = (days[0] - window_start).days
    for previous, current in zip(days, days[1:]):
        gap = max(gap, (current - previous).days - 1)
    return gap


def _resolve_user_declaration(
    outcome_signals: list[WeightOutcomeSignal],
    in_window: list[tuple[date, float]],
) -> tuple[bool, WeightOutcomeSignal | None, str | None, float | None]:
    """判断最近一次“体重未变”自述是否仍有效。

    更新的成效事件直接覆盖旧自述；如果没有更新事件，则看自述之后的新称重是否已经
    发生超过平台阈值的下降或上涨。持平数据不会解除自述。
    """

    declarations = [
        signal for signal in outcome_signals if signal.event_type == "unchanged"
    ]
    if not declarations:
        return False, None, None, None

    latest_declaration = max(
        declarations, key=lambda signal: _aware_utc(signal.occurred_at)
    )
    declared_at_utc = _aware_utc(latest_declaration.occurred_at)
    newer_resolutions = [
        signal
        for signal in outcome_signals
        if signal.event_type in _DECLARATION_RESOLVING_EVENT_TYPES
        and _aware_utc(signal.occurred_at) > declared_at_utc
    ]
    if newer_resolutions:
        latest_resolution = max(
            newer_resolutions,
            key=lambda signal: _aware_utc(signal.occurred_at),
        )
        return (
            False,
            latest_declaration,
            f"outcome_{latest_resolution.event_type}",
            None,
        )

    declared_day = declared_at_utc.astimezone(_tz()).date()
    post_declared_delta_kg = _weight_delta_after_declaration(in_window, declared_day)
    if post_declared_delta_kg is not None:
        if post_declared_delta_kg < -PLATEAU_MAX_DELTA_KG:
            return False, latest_declaration, "weight_loss", post_declared_delta_kg
        if post_declared_delta_kg > PLATEAU_MAX_DELTA_KG:
            return False, latest_declaration, "weight_gain", post_declared_delta_kg

    return True, latest_declaration, None, post_declared_delta_kg


def _weight_delta_after_declaration(
    in_window: list[tuple[date, float]],
    declared_day: date,
) -> float | None:
    """取自述当天或此前最近体重为锚，再和之后最新体重比较。

    如果自述前没有体重，至少需要自述后两次称重，才有可以比较的起点和终点。
    同日只有日期精度，不能证明称重发生在自述之后，因此同日记录只可作为锚点。
    """

    at_or_before = [(day, value) for day, value in in_window if day <= declared_day]
    after = [(day, value) for day, value in in_window if day > declared_day]
    if not after:
        return None

    if at_or_before:
        anchor_day, anchor_weight = at_or_before[-1]
    elif len(after) >= 2:
        anchor_day, anchor_weight = after[0]
    else:
        return None

    last_day, last_weight = after[-1]
    if last_day <= anchor_day:
        return None
    return round(last_weight - anchor_weight, 2)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _drop_implausible_jumps(
    usable: list[tuple[date, float]],
) -> list[tuple[date, float]]:
    """剔除相对上一条有效记录出现生理上不可能跳变的那条（复用成效统计口径）。"""

    cleaned: list[tuple[date, float]] = []
    for day, value in usable:
        if cleaned:
            prev_day, prev_value = cleaned[-1]
            span = max((day - prev_day).days, 1)
            if (
                abs(value - prev_value)
                > _MIN_WEIGHT_DELTA_ALLOWANCE_KG + _MAX_WEIGHT_DELTA_PER_DAY * span
            ):
                continue
        cleaned.append((day, value))
    return cleaned
