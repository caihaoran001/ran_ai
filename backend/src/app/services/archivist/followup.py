"""追问补齐：日打卡 H5 下线后的完整度兜底（H5 的完整替代）。

H5 永久下线后，聊天抽取 + 本模块的追问是打卡数据的**全部来源**。追问为"外部确定性触发、
每 case 每天最多几条"（不注入系统 prompt 让 agent 自由裁量），由调度器
`reminder_service.check_checkin_followup_v2_once` 周期扫描、调 `compose_scheduled_followups`
组织候选，各类各占各的时间窗与天级去重闸（`ReminderEvent(rule_id="checkin_followup:{kind}")`）、
每天最多 3 条：

- 五项清单（daily_checklist）：早上回顾**昨天**当前生效清单里尚未回答的项；系统清单第1项跟踪早C/午B/晚A，工作人员自定义清单不强加该语义。
- 矛盾核对（contradiction）：表单值≠聊天值时下午发一句自然核对。
- 次晨空腹打卡提醒（milestone）：day7/14/21/28 前一晚提醒用户次日早起如厕后、空腹测量并打卡。

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
from app.services.checkin_checklists import (
    TRACKING_PRODUCT_SCHEDULE,
    get_effective_daily_checklist,
)
from app.services.checkin_followup_preferences import get_followup_preference_states
from app.services.service_case_queries import effective_service_day

# 早C/午B/晚A 三段产品依从：最不会自然出现在聊天里、又是阶段报告核心指标。
# （作矛盾核对/五项清单第1项判缺的字段标签来源，见 _FIELD_LABELS、_first_item_missing_label。）
_PRODUCT_SLOTS = {"morning_c": "早C", "noon_b": "午B", "night_a": "晚A"}
_WEIGHT_LOOKBACK_DAYS = 3  # 里程碑前夕若最近 N 天没报体重，次晨打卡提醒中带上称重。
_WAIST_LOOKBACK_DAYS = 7  # 腰围一周测一次，避免天天问。

# 里程碑前一晚：服务第 N 天 → 即将到来的里程碑天。晚间只发提醒，
# 实际测量/打卡必须等到次日早起如厕后的空腹状态。service_day 用 effective_service_day（计划开始当天=第1天）。
_MILESTONE_EVE = {6: 7, 13: 14, 20: 21, 27: 28}

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


def _field_label(field_key: str) -> str:
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
    session: AsyncSession, *, service_case_id: int, field_key: str, today: date, days: int
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
    return any(_slot_filled((c.parsed_json or {}).get(field_key)) for c in recent)


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
    confirmations = _pending_confirmations(today_checkin.parsed_json if today_checkin else None)

    # 体重不再单独一类主动追问，只在里程碑前夕的“次晨打卡”提醒中带上：
    # 仅当最近 N 天都没有记录才提醒次晨称重，避免刚报过又问。
    ask_weight = not await _has_field_recently(
        session,
        service_case_id=service_case.id,
        field_key="weight",
        today=today,
        days=_WEIGHT_LOOKBACK_DAYS,
    )

    # 里程碑前夕：服务第 6/13/20/27 天 → 晚间提醒用户次晨空腹打卡。
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
) -> str | None:
    """矛盾核对：把待确认项组织成一句自然语言核对；无内容返回 None。零 LLM。

    刻意做成"跟你核对一下最新数值"，不暴露"系统发现你自相矛盾"，体验更好；
    用户答复的新值会覆盖旧记录（见 mark_contradictions_pending_confirmation）。
    """
    parts: list[str] = []
    for c in confirmations or []:
        label = c.get("label") or c.get("field")
        chat_value = c.get("chat_value")
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


def _milestone_fallback_message(
    milestone_day: int, need_waist: bool, need_weight: bool = False
) -> str:
    """里程碑前夜提醒的固定模板兜底（LLM 不可用或语义跑偏时使用）。"""
    actions = [
        action
        for action, needed in (("称好体重", need_weight), ("量好腰围", need_waist))
        if needed
    ]
    checkin_action = f"{'、'.join(actions)}，再发群里打卡" if actions else "完成打卡并发到群里"
    return (
        f"明天是服务第{milestone_day}天，今晚先提醒一下："
        f"明早起床如厕后，空腹状态下{checkin_action}。今晚不用测，早点休息～"
    )


def _is_valid_next_morning_checkin_reminder(text: str) -> bool:
    """确保 LLM 没有把“晚间提醒”写成“晚间测量”。"""
    has_morning = any(marker in text for marker in ("明早", "明天早上", "明天早起"))
    forbidden = (
        "今晚称",
        "今晚量",
        "今晚打卡",
        "晚上称",
        "晚上量",
        "晚上打卡",
        "现在称",
        "现在量",
        "现在测",
        "现在打卡",
        "马上称",
        "马上量",
        "马上测",
        "马上打卡",
    )
    return has_morning and "如厕后" in text and "空腹" in text and not any(
        phrase in text for phrase in forbidden
    )


async def compose_milestone_followup(
    *,
    service_case: ServiceCase,
    milestone_day: int,
    need_waist: bool,
    need_weight: bool = False,
    context_hint: str = "",
    ai_service: object | None = None,
) -> str:
    """里程碑前夜：生成“次晨如厕后空腹打卡”提醒；失败或时间语义跑偏则回退固定模板。

    体重/腰围只在 need_weight/need_waist 时带上，不再要求用户当晚复盘或测量。
    """
    try:
        if ai_service is None:
            from app.services.ai_service.client import AIService

            ai_service = AIService()
        system_prompt = (
            "你是燃人健康管理的贴心助理，正在企业微信群里发一条晚间提醒。"
            "要求：亲切自然、不超过 80 字、不要罗列编号。"
            "必须明确写出『明早』、『如厕后』和『空腹』，让用户到明天早上再测量并打卡。"
            "绝对不能让用户今晚、现在或马上称重、量腰围或打卡。"
            "不要追问阶段变化、卡点或下阶段计划。"
        )
        measures = [label for label, need in (("体重", need_weight), ("腰围", need_waist)) if need]
        measure_hint = (
            f"明早需要用户在如厕后、空腹状态下测量{'、'.join(measures)}，再发群里打卡。"
            if measures
            else "体重、腰围最近已记录，只提醒用户明早如厕后空腹打卡。"
        )
        user_prompt = (
            f"今晚是用户服务第 {milestone_day} 天的前一晚。{measure_hint}"
            f"{('用户近况：' + context_hint) if context_hint else ''}"
            "请生成一句自然、简短、亲切的次晨空腹打卡提醒。"
        )
        text = await ai_service.generate_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            scenario="checkin_followup",
            service_case_id=service_case.id,
        )
        text = (text or "").strip()
        if text and _is_valid_next_morning_checkin_reminder(text):
            return text
    except Exception:  # noqa: BLE001 - 追问失败不能阻塞，回退模板
        pass
    return _milestone_fallback_message(milestone_day, need_waist, need_weight)


@dataclass
class ScheduledFollowup:
    """A separately sendable proactive prompt, with auditable expected answers."""

    kind: str
    text: str
    target_date: date
    expected_items: list[dict[str, str]] = field(default_factory=list)
    confirmation_fields: list[str] = field(default_factory=list)


async def get_active_daily_checklist(
    session: AsyncSession, *, service_case_id: int
) -> tuple[str, list[dict[str, str]]] | None:
    """Return the effective versioned checklist, independent of file delivery."""
    checklist = await get_effective_daily_checklist(
        session, service_case_id=service_case_id
    )
    items = checklist.resolved_items()
    return (checklist.source_ref, items) if len(items) == 5 else None


def _answered_checklist_items(checkin: Checkin | None, items: list[dict[str, str]]) -> set[str]:
    """已答的自定义清单项（custom_checkin）。

    系统清单第1项若标记为早C/午B/晚A三合一，则按子产品逐项判缺；工作人员自定义项正常按 item_id 判定。
    """
    parsed = checkin.parsed_json if checkin and isinstance(checkin.parsed_json, dict) else {}
    answers = parsed.get("custom_checkin") if isinstance(parsed, dict) else None
    return {
        str(item_id)
        for item_id, answer in (answers or {}).items()
        if isinstance(answer, dict) and str(answer.get("answer") or "").strip()
    }


def _first_item_missing_label(checkin: Checkin | None, items: list[dict[str, str]]) -> str | None:
    """第1项(早C/午B/晚A三合一)要追问的标签；三项都已上报则返回 None(不追)。

    按 `_PRODUCT_SLOTS` 逐个子产品判缺：全缺时用清单原始题面，部分缺时只列尚未上报的子产品
    (如"午B、晚A")。这样只报了早C、午B/晚A没报时，已报的早C不会被拖着一起重复追问。
    """
    if not items:
        return None
    parsed = checkin.parsed_json if checkin and isinstance(checkin.parsed_json, dict) else {}
    product_schedule = parsed.get("product_schedule") if isinstance(parsed, dict) else None
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


async def compose_document_checklist_followup(
    session: AsyncSession,
    *,
    service_case: ServiceCase,
    target_date: date,
    ai_service: object | None = None,
) -> ScheduledFollowup | None:
    """Ask only unanswered items from yesterday's effective five-item checklist."""
    checklist = await get_active_daily_checklist(session, service_case_id=service_case.id)
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
    labels = "；".join(item["label"] for item in missing)
    fallback = f"早上好，补一下昨天的打卡：{labels}。回我一句，我记上，后面好帮你调整～"
    try:
        if ai_service is None:
            from app.services.ai_service.client import AIService

            ai_service = AIService()
        text = await ai_service.generate_text(
            system_prompt=(
                "你是燃人微信群健康助教。用一句不超过100字、口语化的中文，请用户回顾昨天尚未回答的打卡项。"
                "可将多个问题自然合并，但不得增加清单外的问题，不要使用编号。"
                "结尾自然说明用户回一句即可，助教会记上并据此帮助后续调整；简短、不施压。"
            ),
            user_prompt=f"昨天尚未回答的打卡项：{labels}",
            scenario="checkin_followup",
            service_case_id=service_case.id,
        )
        text = (text or "").strip() or fallback
    except Exception:  # noqa: BLE001 - proactive reminder must never stop the scheduler
        text = fallback
    return ScheduledFollowup(
        kind="daily_checklist", text=text, target_date=target_date, expected_items=missing
    )


async def compose_scheduled_followups(
    session: AsyncSession,
    *,
    service_case: ServiceCase,
    now: datetime,
    tz: ZoneInfo,
    ai_service: object | None = None,
) -> list[ScheduledFollowup]:
    """Build independent candidates; the scheduler applies time windows and the three-message cap."""
    detect = await detect_followup(session, service_case=service_case, now=now, tz=tz)
    preferences = await get_followup_preference_states(session, service_case_id=service_case.id)
    candidates: list[ScheduledFollowup] = []
    confirmations = detect.get("confirmations") or []
    confirmation_text = compose_followup_message(confirmations=confirmations)
    if confirmation_text and preferences["contradiction"]:
        candidates.append(
            ScheduledFollowup(
                kind="contradiction",
                text=confirmation_text,
                target_date=now.date(),
                expected_items=[{"id": f"contradiction:{item['field']}", "label": item["label"]} for item in confirmations],
                confirmation_fields=[item["field"] for item in confirmations],
            )
        )
    milestone = detect.get("milestone")
    if milestone and preferences["milestone_review"]:
        candidates.append(
            ScheduledFollowup(
                kind="milestone",
                text=await compose_milestone_followup(
                    service_case=service_case,
                    milestone_day=milestone["milestone_day"],
                    need_waist=bool(milestone["need_waist"] and preferences["waist"]),
                    need_weight=bool(milestone["need_weight"] and preferences["weight"]),
                    ai_service=ai_service,
                ),
                target_date=now.date() + timedelta(days=1),
                expected_items=[
                    {"id": "milestone_morning_checkin", "label": "次晨空腹打卡"}
                ],
            )
        )
    if preferences["daily_checklist"]:
        daily = await compose_document_checklist_followup(
            session,
            service_case=service_case,
            target_date=now.date() - timedelta(days=1),
            ai_service=ai_service,
        )
        if daily:
            candidates.append(daily)
    # 体重不再单独一类主动追问，已并入里程碑前夜的次晨空腹打卡提醒。
    return candidates


async def finalize_checkin_followup_cycles(session: AsyncSession, *, today: date) -> None:
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


async def is_followup_paused(
    session: AsyncSession, *, service_case_id: int
) -> bool:
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
