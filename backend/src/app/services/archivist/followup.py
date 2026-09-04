"""追问补齐：日打卡 H5 下线后的完整度兜底（H5 的完整替代）。

H5 永久下线后，聊天抽取 + 本模块的追问是打卡数据的**全部来源**。追问为"外部确定性触发、
每 case 每天最多几条"（不注入系统 prompt 让 agent 自由裁量），由调度器
`reminder_service.check_checkin_followup_v2_once` 周期扫描、调 `plan_scheduled_followups`
无模型地组织候选；调度器通过时间窗与天级去重闸
（`ReminderEvent(rule_id="checkin_followup:{kind}")`）后，再由 `render_scheduled_followup`
填充固定模板。整条追问链路不调用 LLM，每天最多 3 条：

- 五项清单（daily_checklist）：**服务第 2 天起**每天早上回顾**昨天**当前生效清单里尚未回答的项（第 1 天的「昨天」还在服务期外，不追）；第1项始终跟踪早C/午B/晚A，工作人员只能调整第2—5项。
- 矛盾核对（contradiction）：表单值≠聊天值时下午发一句自然核对。
- 次晨空腹打卡提醒（milestone）：day3/7/14/21/28 前一晚提醒用户次日早起如厕后、空腹测量并打卡。

判定读 checkins 现状 + service_day。体重不再单独一类主动追问，改由里程碑前夜提醒在次晨采集。
（历史上另有一条"活跃触发"链路在 agent 回复后即时补发，已下线；本模块只组织内容、不裁量发送时机。）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.checkin import Checkin
from app.models.checkin_followup_cycle import CheckinFollowupCycle
from app.models.service_case import ServiceCase
from app.services.body_metrics import parse_waist_cm, parse_weight_kg
from app.services.checkin_checklists import (
    TRACKING_PRODUCT_SCHEDULE,
    get_effective_daily_checklist,
)
from app.services.checkin_followup_preferences import get_followup_preference_states
from app.services.service_case_queries import checkin_first_day, effective_service_day

# 早C/午B/晚A 三段产品依从：最不会自然出现在聊天里、又是阶段报告核心指标。
# （作矛盾核对/五项清单第1项判缺的字段标签来源，见 _FIELD_LABELS、_first_item_missing_label。）
_PRODUCT_SLOTS = {"morning_c": "早C", "noon_b": "午B", "night_a": "晚A"}
_WEIGHT_LOOKBACK_DAYS = 3  # 里程碑前夕若最近 N 天没报体重，次晨打卡提醒中带上称重。
_WAIST_LOOKBACK_DAYS = 7  # 腰围一周测一次，避免天天问。

# 里程碑前一晚：服务第 N 天 → 即将到来的里程碑天。晚间只发提醒，
# 实际测量/打卡必须等到次日早起如厕后的空腹状态。service_day 用 effective_service_day（计划开始当天=第1天）。
_MILESTONE_EVE = {2: 3, 6: 7, 13: 14, 20: 21, 27: 28}

# 矛盾追问用的字段中文标签（含 product_schedule 子键）。
_FIELD_LABELS = {
    "weight": "体重",
    "waist": "腰围",
    "water": "喝水量",
    "sleep": "睡眠",
    "diet": "饮食",
    "exercise": "运动",
    "bowel": "排便",
    "mood": "状态",
    "hunger": "饥饿感",
    "product_schedule.morning_c": "早C",
    "product_schedule.noon_b": "午B",
    "product_schedule.night_a": "晚A",
}

_FIELD_LABELS_EN = {
    "weight": "weight",
    "waist": "waist measurement",
    "water": "water intake",
    "sleep": "sleep",
    "diet": "diet",
    "exercise": "exercise",
    "bowel": "digestion/bowel movement",
    "mood": "energy/mood",
    "hunger": "appetite/hunger",
    "product_schedule.morning_c": "Morning C",
    "product_schedule.noon_b": "Noon B",
    "product_schedule.night_a": "Evening A",
}


def _field_label(field_key: str, language: str = "zh") -> str:
    if language == "en":
        return _FIELD_LABELS_EN.get(field_key, field_key)
    return _FIELD_LABELS.get(field_key, field_key)


def _slot_filled(value: object) -> bool:
    return bool(str(value or "").strip())


def _pending_confirmations(parsed_json: dict | None) -> list[dict]:
    """当天待向用户确认的矛盾（表单值≠聊天值、且还没问过 asked）。"""
    meta = (parsed_json or {}).get("_meta") if isinstance(parsed_json, dict) else None
    contradictions = (meta or {}).get("contradictions") or []
    out: list[dict] = []
    for c in contradictions:
        if not isinstance(c, dict) or not c.get("field") or c.get("asked"):
            continue
        out.append(
            {
                "field": c["field"],
                "label": _field_label(str(c["field"])),
                "form_value": c.get("form_value"),
                "chat_value": c.get("chat_value"),
            }
        )
    return out


async def _has_field_recently(
    session: AsyncSession,
    *,
    service_case_id: int,
    field_key: str,
    today: date,
    days: int,
) -> bool:
    """最近 days 天内是否有某字段的记录（用于避免重复追问体重/腰围）。"""
    since = today - timedelta(days=days - 1)
    recent = (
        await session.scalars(
            select(Checkin).where(
                Checkin.service_case_id == service_case_id,
                Checkin.checkin_date >= since,
                Checkin.deleted_at.is_(None),
            )
        )
    ).all()
    parsers = {"weight": parse_weight_kg, "waist": parse_waist_cm}
    try:
        parser = parsers[field_key]
    except KeyError as exc:
        raise ValueError(f"Unsupported body field: {field_key}") from exc
    return any(parser((c.parsed_json or {}).get(field_key)).ok for c in recent)


async def detect_followup(
    session: AsyncSession,
    *,
    service_case: ServiceCase,
    now: datetime,
    tz: ZoneInfo,
) -> dict:
    """判断有哪些数据要确认 / 是否里程碑前夕。

    返回 {
        ask_weight: bool,
        confirmations: [{field,label,form_value,chat_value}],
        milestone: None | {"milestone_day": int, "need_waist": bool},
    }。
    """
    today = now.date()
    today_checkin = await session.scalar(
        select(Checkin).where(
            Checkin.service_case_id == service_case.id,
            Checkin.checkin_date == today,
            Checkin.deleted_at.is_(None),
        )
    )
    confirmations = _pending_confirmations(
        today_checkin.parsed_json if today_checkin else None
    )

    # 体重不再单独一类主动追问，只在里程碑前夕的“次晨打卡”提醒中带上：
    # 仅当最近 N 天都没有记录才提醒次晨称重，避免刚报过又问。
    ask_weight = not await _has_field_recently(
        session,
        service_case_id=service_case.id,
        field_key="weight",
        today=today,
        days=_WEIGHT_LOOKBACK_DAYS,
    )

    # 里程碑前夕：服务第 2/6/13/20/27 天 → 晚间提醒用户次晨空腹打卡。
    milestone = None
    service_day = effective_service_day(service_case, now, tz)
    milestone_day = _MILESTONE_EVE.get(service_day)
    if milestone_day is not None:
        need_waist = not await _has_field_recently(
            session,
            service_case_id=service_case.id,
            field_key="waist",
            today=today,
            days=_WAIST_LOOKBACK_DAYS,
        )
        milestone = {
            "milestone_day": milestone_day,
            "need_waist": need_waist,
            "need_weight": ask_weight,
        }

    return {
        "ask_weight": ask_weight,
        "confirmations": confirmations,
        "milestone": milestone,
    }


def compose_followup_message(
    *,
    confirmations: list[dict] | None = None,
    language: str = "zh",
) -> str | None:
    """矛盾核对：把待确认项组织成一句自然语言核对；无内容返回 None。零 LLM。

    刻意做成"跟你核对一下最新数值"，不暴露"系统发现你自相矛盾"，体验更好；
    用户答复的新值会覆盖旧记录（见 mark_contradictions_pending_confirmation）。
    """
    parts: list[str] = []
    for c in confirmations or []:
        field_key = str(c.get("field") or "")
        label = _field_label(field_key, language=language) or c.get("label") or field_key
        chat_value = c.get("chat_value")
        if language == "en":
            if chat_value:
                parts.append(
                    f"By the way, is your {label} {chat_value}? Just wanted to double-check and keep your records accurate!"
                )
            else:
                parts.append(
                    f"By the way, what is your latest {label}? Just wanted to confirm so I can update your log!"
                )
        else:
            if chat_value:
                parts.append(
                    f"对了{label}是{chat_value}吧？我先前记的跟你后面说的对不上，是的话我就按这个改哈。"
                )
            else:
                parts.append(
                    f"对了你{label}最新是多少呀？我这记的有点对不上，你说个准的我改一下哈。"
                )
    if not parts:
        return None
    return " ".join(parts)


def compose_milestone_followup(
    milestone_day: int, need_waist: bool, need_weight: bool = False, language: str = "zh"
) -> str:
    """里程碑前夜的固定模板：提醒用户次晨如厕后空腹打卡。"""
    if language == "en":
        actions = [
            action
            for action, needed in (("record your weight", need_weight), ("measure your waist", need_waist))
            if needed
        ]
        checkin_action = (
            f"{' and '.join(actions)}, then share it in the group" if actions else "complete your check-in and post it in the group"
        )
        return (
            f"Tomorrow is Day {milestone_day} of your program! Quick reminder for tonight: "
            f"tomorrow morning after using the restroom, please {checkin_action} on an empty stomach. No need to measure tonight, get some good rest~"
        )

    actions = [
        action
        for action, needed in (("称好体重", need_weight), ("量好腰围", need_waist))
        if needed
    ]
    checkin_action = (
        f"{'、'.join(actions)}，再发群里打卡" if actions else "完成打卡并发到群里"
    )
    return (
        f"明天是服务第{milestone_day}天，今晚先提醒一下："
        f"明早起床如厕后，空腹状态下{checkin_action}。今晚不用测，早点休息～"
    )


@dataclass
class ScheduledFollowup:
    """A separately sendable proactive prompt, with auditable expected answers."""

    kind: str
    text: str
    target_date: date
    expected_items: list[dict[str, str]] = field(default_factory=list)
    confirmation_fields: list[str] = field(default_factory=list)


@dataclass
class ScheduledFollowupPlan:
    """A model-free candidate; rendering is deferred until the send gate is won."""

    kind: str
    target_date: date
    expected_items: list[dict[str, str]] = field(default_factory=list)
    confirmation_fields: list[str] = field(default_factory=list)
    fixed_text: str | None = None
    milestone_day: int | None = None
    need_waist: bool = False
    need_weight: bool = False
    language: str = "zh"


async def get_active_daily_checklist(
    session: AsyncSession, *, service_case_id: int
) -> tuple[str, list[dict[str, str]]] | None:
    """Return the effective versioned checklist, independent of file delivery."""
    checklist = await get_effective_daily_checklist(
        session, service_case_id=service_case_id
    )
    items = checklist.resolved_items()
    return (checklist.source_ref, items) if len(items) == 5 else None


def _answered_checklist_items(
    checkin: Checkin | None, items: list[dict[str, str]]
) -> set[str]:
    """已答的自定义清单项（custom_checkin）。

    第1项若标记为早C/午B/晚A三合一，则按子产品逐项判缺；第2—5项按 item_id 判定。
    """
    parsed = (
        checkin.parsed_json if checkin and isinstance(checkin.parsed_json, dict) else {}
    )
    answers = parsed.get("custom_checkin") if isinstance(parsed, dict) else None
    return {
        str(item_id)
        for item_id, answer in (answers or {}).items()
        if isinstance(answer, dict) and str(answer.get("answer") or "").strip()
    }


def _first_item_missing_label(
    checkin: Checkin | None, items: list[dict[str, str]]
) -> str | None:
    """第1项(早C/午B/晚A三合一)要追问的标签；三项都已上报则返回 None(不追)。

    按 `_PRODUCT_SLOTS` 逐个子产品判缺：全缺时用清单原始题面，部分缺时只列尚未上报的子产品
    (如"午B、晚A")。这样只报了早C、午B/晚A没报时，已报的早C不会被拖着一起重复追问。
    """
    if not items:
        return None
    parsed = (
        checkin.parsed_json if checkin and isinstance(checkin.parsed_json, dict) else {}
    )
    product_schedule = (
        parsed.get("product_schedule") if isinstance(parsed, dict) else None
    )
    product_schedule = product_schedule if isinstance(product_schedule, dict) else {}
    missing_slots = [
        label
        for key, label in _PRODUCT_SLOTS.items()
        if not _slot_filled(product_schedule.get(key))
    ]
    if not missing_slots:
        return None
    if len(missing_slots) == len(_PRODUCT_SLOTS):
        return items[0]["label"]
    return "、".join(missing_slots)


async def plan_document_checklist_followup(
    session: AsyncSession,
    *,
    service_case: ServiceCase,
    target_date: date,
) -> ScheduledFollowupPlan | None:
    """Resolve yesterday's unanswered checklist items without calling a model."""
    checklist = await get_active_daily_checklist(
        session, service_case_id=service_case.id
    )
    if checklist is None:
        return None
    _, items = checklist
    checkin = await session.scalar(
        select(Checkin).where(
            Checkin.service_case_id == service_case.id,
            Checkin.checkin_date == target_date,
            Checkin.deleted_at.is_(None),
        )
    )
    answered = _answered_checklist_items(checkin, items)
    missing: list[dict[str, str]] = []
    for index, item in enumerate(items):
        if index == 0 and item.get("tracking_kind") == TRACKING_PRODUCT_SCHEDULE:
            # 第1项三合一：只追尚未上报的子产品（早C/午B/晚A），已报的不重复问。
            first_label = _first_item_missing_label(checkin, items)
            if first_label is not None:
                missing.append({"id": item["id"], "label": first_label})
        elif item["id"] not in answered:
            missing.append(item)
    if not missing:
        return None
    case_lang = getattr(service_case, "language", "zh") or "zh"
    return ScheduledFollowupPlan(
        kind="daily_checklist",
        target_date=target_date,
        expected_items=missing,
        language=case_lang,
    )


def _compose_document_checklist_followup_text(*, missing: list[dict[str, str]], language: str = "zh") -> str:
    """Fill the fixed daily-checklist template with yesterday's missing items."""
    if language == "en":
        labels = "; ".join(item["label"] for item in missing)
        return f"Good morning! Just a quick follow-up on yesterday's check-in: {labels}. Reply to me with an update so I can log it and help adjust your plan~"
    labels = "；".join(item["label"] for item in missing)
    return f"早上好，补一下昨天的打卡：{labels}。回我一句，我记上，后面好帮你调整～"


async def compose_document_checklist_followup(
    session: AsyncSession,
    *,
    service_case: ServiceCase,
    target_date: date,
) -> ScheduledFollowup | None:
    """Compatibility helper for direct composition outside the scheduler."""
    plan = await plan_document_checklist_followup(
        session,
        service_case=service_case,
        target_date=target_date,
    )
    if plan is None:
        return None
    return render_scheduled_followup(plan)


async def plan_scheduled_followups(
    session: AsyncSession,
    *,
    service_case: ServiceCase,
    now: datetime,
    tz: ZoneInfo,
) -> list[ScheduledFollowupPlan]:
    """Build independent candidates without generating any LLM copy."""
    case_lang = getattr(service_case, "language", "zh") or "zh"
    detect = await detect_followup(session, service_case=service_case, now=now, tz=tz)
    preferences = await get_followup_preference_states(
        session, service_case_id=service_case.id
    )
    if not preferences["proactive_contact"]:
        # 客户说过"暂停主动联系"：连数据核对都不发。风险相关提醒不走这条链路，不受影响。
        return []
    candidates: list[ScheduledFollowupPlan] = []
    confirmations = detect.get("confirmations") or []
    confirmation_text = compose_followup_message(
        confirmations=confirmations,
        language=case_lang,
    )
    if confirmation_text and preferences["contradiction"]:
        candidates.append(
            ScheduledFollowupPlan(
                kind="contradiction",
                target_date=now.date(),
                expected_items=[
                    {"id": f"contradiction:{item['field']}", "label": item["label"]}
                    for item in confirmations
                ],
                confirmation_fields=[item["field"] for item in confirmations],
                fixed_text=confirmation_text,
                language=case_lang,
            )
        )
    milestone = detect.get("milestone")
    if milestone and preferences["milestone_review"]:
        candidates.append(
            ScheduledFollowupPlan(
                kind="milestone",
                target_date=now.date() + timedelta(days=1),
                expected_items=[
                    {"id": "milestone_morning_checkin", "label": "Morning fasting check-in" if case_lang == "en" else "次晨空腹打卡"}
                ],
                milestone_day=milestone["milestone_day"],
                need_waist=bool(milestone["need_waist"] and preferences["waist"]),
                need_weight=bool(milestone["need_weight"] and preferences["weight"]),
                language=case_lang,
            )
        )
    if preferences["daily_checklist"]:
        # 追问的是「昨天」：服务第 1 天没有可追的昨天(那天还在服务期外、客户也没有打卡
        # 任务)，这条候选整条不生成——不查清单、不查打卡、不占当天的追问名额。
        target_date = now.date() - timedelta(days=1)
        first_day = checkin_first_day(service_case, tz)
        if first_day is not None and target_date >= first_day:
            daily = await plan_document_checklist_followup(
                session,
                service_case=service_case,
                target_date=target_date,
            )
            if daily:
                candidates.append(daily)
    # 体重不再单独一类主动追问，已并入里程碑前夜的次晨空腹打卡提醒。
    return candidates


def render_scheduled_followup(plan: ScheduledFollowupPlan) -> ScheduledFollowup:
    """Fill a fixed template after the scheduler's time/dedupe reservation."""
    if plan.fixed_text is not None:
        text = plan.fixed_text
    elif plan.kind == "daily_checklist":
        text = _compose_document_checklist_followup_text(missing=plan.expected_items, language=plan.language)
    elif plan.kind == "milestone" and plan.milestone_day is not None:
        text = compose_milestone_followup(
            milestone_day=plan.milestone_day,
            need_waist=plan.need_waist,
            need_weight=plan.need_weight,
            language=plan.language,
        )
    else:
        raise ValueError(f"Unsupported followup plan: {plan.kind}")
    return ScheduledFollowup(
        kind=plan.kind,
        text=text,
        target_date=plan.target_date,
        expected_items=plan.expected_items,
        confirmation_fields=plan.confirmation_fields,
    )


async def compose_scheduled_followups(
    session: AsyncSession,
    *,
    service_case: ServiceCase,
    now: datetime,
    tz: ZoneInfo,
) -> list[ScheduledFollowup]:
    """Compatibility helper that plans and renders all candidates immediately."""
    plans = await plan_scheduled_followups(
        session,
        service_case=service_case,
        now=now,
        tz=tz,
    )
    return [render_scheduled_followup(plan) for plan in plans]


async def finalize_checkin_followup_cycles(
    session: AsyncSession, *, today: date
) -> None:
    """Close older open cycles; only a completed calendar cycle counts as one silence."""
    rows = list(
        await session.scalars(
            select(CheckinFollowupCycle).where(
                CheckinFollowupCycle.cycle_date < today,
                CheckinFollowupCycle.status == "open",
            )
        )
    )
    now = datetime.now(tz=ZoneInfo("UTC"))
    for row in rows:
        row.status = "answered" if row.answered_item_ids else "unanswered"
        row.finalized_at = now


async def is_followup_paused(session: AsyncSession, *, service_case_id: int) -> bool:
    """Pause after two consecutive finished cycles without a valid expected answer."""
    rows = list(
        await session.scalars(
            select(CheckinFollowupCycle)
            .where(
                CheckinFollowupCycle.service_case_id == service_case_id,
                CheckinFollowupCycle.status.in_(["answered", "unanswered"]),
            )
            .order_by(CheckinFollowupCycle.cycle_date.desc())
            .limit(2)
        )
    )
    return len(rows) == 2 and all(row.status == "unanswered" for row in rows)


async def mark_followup_answers(
    session: AsyncSession,
    *,
    service_case_id: int,
    cycle_dates_and_items: list[tuple[date, str]],
) -> None:
    """Late answers may reopen a paused case by changing the original cycle to answered."""
    for cycle_date, item_id in cycle_dates_and_items:
        row = await session.scalar(
            select(CheckinFollowupCycle).where(
                CheckinFollowupCycle.service_case_id == service_case_id,
                CheckinFollowupCycle.cycle_date == cycle_date,
            )
        )
        if row is None:
            continue
        answered = list(row.answered_item_ids or [])
        if item_id not in answered:
            answered.append(item_id)
            row.answered_item_ids = answered
        row.status = "answered"
        row.finalized_at = datetime.now(tz=ZoneInfo("UTC"))


async def mark_latest_followup_cycle_answered(
    session: AsyncSession,
    *,
    service_case_id: int,
    item_ids: list[str],
) -> None:
    """任一正常打卡回答都恢复追问；没有历史追问周期时无需写入。"""
    row = await session.scalar(
        select(CheckinFollowupCycle)
        .where(CheckinFollowupCycle.service_case_id == service_case_id)
        .order_by(CheckinFollowupCycle.cycle_date.desc())
        .limit(1)
    )
    if row is None:
        return
    answered = list(row.answered_item_ids or [])
    for item_id in item_ids:
        if item_id and item_id not in answered:
            answered.append(item_id)
    row.answered_item_ids = answered
    row.status = "answered"
    row.finalized_at = datetime.now(tz=ZoneInfo("UTC"))
