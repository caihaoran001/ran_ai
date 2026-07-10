"""追问补齐：日打卡 H5 下线后的完整度兜底（H5 的完整替代），混合架构分三条路：

H5 永久下线后，聊天抽取 + 本模块的追问是打卡数据的**全部来源**。固定时间冷推里程碑/矛盾
之外的日常缺口既惹企微风控、又像"系统到点了"，故日常缺口改"搭便车"、只把里程碑与矛盾留在单发线：

- **日常缺口搭便车**（`compute_gap_hint`，零 LLM 罐头句）：算出当天仍缺的产品依从（早C/午B/晚A）
  与体重，返回一句极简事实串（如"用户今天还没报：早C"）喂给群内聊天机器人当背景；机器人正常
  回复用户时，在话题自然处顺口关心一句，话术由 LLM 现场生成。退避 backoff_days=1/max_asks=3
  （每天最多提一次、连提 3 天不报即放弃该字段），提示计数落 ReminderEvent rule_id=checkin_followup:{key}。
- **里程碑档**（`compose_milestone_followup`，LLM 个性化 + 模板兜底）：day7/14/21/28 阶段报告要用的
  腰围 + 复盘信息（这阶段最大变化 / 最大卡点），在里程碑**前一晚**（服务第 6/13/20/27 天）经单发线
  追问，给用户约一天答复，赶在里程碑当晚 21:00 阶段报告/成效快照生成前落库。用户答复经档案官
  聊天抽取落 checkin.waist（喂快照）+ 进聊天历史（喂阶段报告 LLM）。
- **矛盾核对**（`compose_followup_message`，零 LLM）：表单值与聊天值对不上时，经单发线向用户核对最新值。

判定读 checkins 现状 + service_day。搭便车由聊天链路即时调用（见 agent/graph.py）；里程碑与矛盾的
时机/去重/发送复用 reminder_service（见 check_checkin_followup_once）。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.checkin import Checkin
from app.models.reminder import ReminderEvent, ReminderEventType
from app.models.service_case import ServiceCase
from app.services.service_case_queries import effective_service_day

# 早C/午B/晚A 三段产品依从：最不会自然出现在聊天里、又是阶段报告核心指标。
_PRODUCT_SLOTS = {"morning_c": "早C", "noon_b": "午B", "night_a": "晚A"}
_WEIGHT_LOOKBACK_DAYS = 3
_WAIST_LOOKBACK_DAYS = 7  # 腰围一周测一次，避免天天问。

# 追问退避：同一缺失字段追过一次就冷却 _FOLLOWUP_BACKOFF_DAYS 天不再追；
# 累计追满 _FOLLOWUP_MAX_ASKS 次仍不补 → 认定"这人就是不爱报"，彻底停追，避免天天骚扰。
_FOLLOWUP_BACKOFF_DAYS = 3
_FOLLOWUP_MAX_ASKS = 3

# 搭便车「今日待补」退避：每天最多向聊天机器人提示一次某字段(backoff_days=1)，
# 连续提示 _RIDE_ALONG_MAX_ASKS 天用户仍不报 → 放弃该字段，不再提示，避免天天顺口追。
_RIDE_ALONG_BACKOFF_DAYS = 1
_RIDE_ALONG_MAX_ASKS = 3

# 里程碑前一晚：服务第 N 天 → 即将到来的里程碑天。阶段报告在里程碑当晚 21:00 生成，
# 故提前一天追问，给用户答复时间。service_day 用 effective_service_day（计划开始当天=第1天）。
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


def _missing_product_slots(parsed_json: dict | None) -> list[dict]:
    """当天仍缺的产品依从槽位：返回 [{"key","label"}]，key 用于退避、label 用于话术。"""
    ps = (parsed_json or {}).get("product_schedule") if isinstance(parsed_json, dict) else None
    ps = ps if isinstance(ps, dict) else {}
    return [
        {"key": key, "label": label}
        for key, label in _PRODUCT_SLOTS.items()
        if not _slot_filled(ps.get(key))
    ]


def allowed_after_backoff(
    candidate_keys: list[str],
    asked_events: list[tuple[str, date]],
    today: date,
    *,
    backoff_days: int = _FOLLOWUP_BACKOFF_DAYS,
    max_asks: int = _FOLLOWUP_MAX_ASKS,
) -> list[str]:
    """退避过滤：从候选缺失字段里筛掉"最近追过还在冷却"或"已追满上限"的，返回本次仍可追的 key。

    asked_events: 该服务单历史"字段级追问"事件 [(key, event_date)]（来自 ReminderEvent
    rule_id=checkin_followup:{key}）。规则：
    - 累计追问 >= max_asks 次 → 永久停追（这人就是不爱报该字段）。
    - 距上次追问 < backoff_days 天 → 冷却中，本次跳过。
    """
    history: dict[str, list[date]] = {}
    for key, event_date in asked_events:
        history.setdefault(key, []).append(event_date)
    allowed: list[str] = []
    for key in candidate_keys:
        dates = history.get(key, [])
        if len(dates) >= max_asks:
            continue
        last = max(dates) if dates else None
        if last is not None and (today - last).days < backoff_days:
            continue
        allowed.append(key)
    return allowed


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
    """判断今天还缺哪些关键字段 / 有哪些数据要确认 / 是否里程碑前夕。

    返回 {
        missing_slots: [{"key","label"}],   # 缺失产品槽位，key 供退避、label 供话术
        ask_weight: bool,
        confirmations: [{field,label,form_value,chat_value}],
        milestone: None | {"milestone_day": int, "need_waist": bool},
    }。
    退避（同一字段冷却/停追）由调用方用 allowed_after_backoff 处理，detect 只报"当前缺什么"。
    """
    today = now.date()
    today_checkin = await session.scalar(
        select(Checkin).where(
            Checkin.service_case_id == service_case.id,
            Checkin.checkin_date == today,
            Checkin.deleted_at.is_(None),
        )
    )
    missing_slots = _missing_product_slots(today_checkin.parsed_json if today_checkin else None)
    confirmations = _pending_confirmations(today_checkin.parsed_json if today_checkin else None)

    # 体重不是每天必报：仅当最近 N 天都没有记录才追问，避免天天问体重。
    ask_weight = not await _has_field_recently(
        session,
        service_case_id=service_case.id,
        field_key="weight",
        today=today,
        days=_WEIGHT_LOOKBACK_DAYS,
    )

    # 里程碑前夕：服务第 6/13/20/27 天 → 为次日的阶段报告采集腰围 + 复盘。
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
        milestone = {"milestone_day": milestone_day, "need_waist": need_waist}

    return {
        "missing_slots": missing_slots,
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
    日常缺口（早C/午B/晚A、体重）已改由聊天机器人「今日待补」搭便车，不再走本函数发罐头句。
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


def _milestone_fallback_message(milestone_day: int, need_waist: bool) -> str:
    """里程碑复盘追问的固定模板兜底（LLM 不可用时使用）。

    只问用户才知道的：腰围（客观数据）+ 这阶段最大变化 + 最大卡点（主观感受）。
    「下阶段重点」是专业判断，交给阶段报告基于这些 + action_focus 生成，不反问用户。
    """
    week = {7: "一周", 14: "两周", 21: "三周", 28: "四周"}.get(milestone_day, f"第{milestone_day}天")
    waist_clause = "腰围量一下发我，" if need_waist else ""
    return (
        f"咱们到{week}啦，一起复盘一下～{waist_clause}"
        "你觉得这阶段最大的变化是什么？还有哪个地方最卡、最难坚持？跟我说说哈。"
    )


async def compose_milestone_followup(
    *,
    service_case: ServiceCase,
    milestone_day: int,
    need_waist: bool,
    context_hint: str = "",
    ai_service: object | None = None,
) -> str:
    """里程碑档：LLM 按用户情况生成一句自然的阶段复盘追问；失败回退固定模板。

    覆盖阶段报告要用的：腰围（need_waist 时）、这阶段最大变化、最大卡点、下阶段重点。
    防御性：LLM 不可用/抛错一律回退模板，绝不 raise、绝不阻塞发送。
    """
    try:
        if ai_service is None:
            from app.services.ai_service.client import AIService

            ai_service = AIService()
        system_prompt = (
            "你是燃人健康管理的贴心助理，在企业微信群里用一句口语化中文向用户发起阶段复盘追问。"
            "要求：亲切自然、不超过 80 字、不要罗列编号。"
            "只收集用户自己才知道的信息：这阶段最大的变化、最大的卡点/最难坚持的地方。"
            "不要反问用户『下阶段重点/下阶段想解决什么』——那是助教基于数据给用户的专业建议，不该甩回给用户。"
        )
        waist_hint = "本次还需要提醒用户测量并报一下腰围。" if need_waist else "腰围最近已记录，无需再问。"
        user_prompt = (
            f"用户处于服务第 {milestone_day} 天里程碑前夕。{waist_hint}"
            f"{('用户近况：' + context_hint) if context_hint else ''}"
            "请生成一句自然、简短、亲切的复盘追问。"
        )
        text = await ai_service.generate_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            scenario="checkin_followup",
            service_case_id=service_case.id,
        )
        text = (text or "").strip()
        if text:
            return text
    except Exception:  # noqa: BLE001 - 追问失败不能阻塞，回退模板
        pass
    return _milestone_fallback_message(milestone_day, need_waist)


async def _reserve_surface_event(
    session: AsyncSession, *, service_case_id: int, field_key: str, today: date
) -> bool:
    """原子记一条「今日已向聊天机器人提示该字段」事件；已存在(当天重复)返回 False。

    与 reminder_service._reserve_reminder_event 同构，但本模块不反向依赖 reminder_service
    (后者已 import 本模块，避免循环)。唯一约束(case,rule_id,date,type)保证幂等。
    """
    event = ReminderEvent(
        service_case_id=service_case_id,
        rule_id=f"checkin_followup:{field_key}",
        event_date=today,
        event_type=ReminderEventType.REMINDED,
    )
    try:
        async with session.begin_nested():
            session.add(event)
            await session.flush()
    except IntegrityError:
        return False
    return True


async def compute_gap_hint(
    session: AsyncSession,
    *,
    service_case_id: int,
    today: date,
) -> str:
    """搭便车「今日待补」：算出今天还没收到、且未被退避挡下的打卡缺口，返回极简事实串。

    供聊天机器人回复时作背景提示，让它在话题自然处顺口关心一句(话术由 LLM 现场生成，
    非罐头句)。会为纳入本次提示的每个字段落一条 surface 事件(退避计数)，故有副作用、
    调用方需 commit。无缺口 / 全被退避 → 返回 ""。

    退避：backoff_days=1 每天最多提一次；max_asks=3 连提 3 天不报即放弃该字段。
    记的是"已提示"而非"agent 确实问了"(不回读出站消息)：提示了但话题没接上就算当天用掉。
    """
    today_checkin = await session.scalar(
        select(Checkin).where(
            Checkin.service_case_id == service_case_id,
            Checkin.checkin_date == today,
            Checkin.deleted_at.is_(None),
        )
    )
    parsed = today_checkin.parsed_json if today_checkin else None

    candidates: list[dict] = list(_missing_product_slots(parsed))  # [{key,label}]
    weight_missing = not await _has_field_recently(
        session,
        service_case_id=service_case_id,
        field_key="weight",
        today=today,
        days=_WEIGHT_LOOKBACK_DAYS,
    )
    if weight_missing:
        candidates.append({"key": "weight", "label": _field_label("weight")})
    if not candidates:
        return ""

    asked_events = await _ride_along_asked_events(session, service_case_id=service_case_id)
    allowed_keys = set(
        allowed_after_backoff(
            [c["key"] for c in candidates],
            asked_events,
            today,
            backoff_days=_RIDE_ALONG_BACKOFF_DAYS,
            max_asks=_RIDE_ALONG_MAX_ASKS,
        )
    )
    labels: list[str] = []
    for c in candidates:
        if c["key"] not in allowed_keys:
            continue
        if await _reserve_surface_event(
            session, service_case_id=service_case_id, field_key=c["key"], today=today
        ):
            labels.append(c["label"])
    if not labels:
        return ""
    return "用户今天还没报：" + "、".join(labels)


async def _ride_along_asked_events(
    session: AsyncSession, *, service_case_id: int
) -> list[tuple[str, date]]:
    """取该单历史「字段级追问/提示」事件 [(field_key, event_date)]，供退避判定。"""
    rows = (
        await session.execute(
            select(ReminderEvent.rule_id, ReminderEvent.event_date).where(
                ReminderEvent.service_case_id == service_case_id,
                ReminderEvent.rule_id.like("checkin_followup:%"),
                ReminderEvent.event_type == ReminderEventType.REMINDED,
            )
        )
    ).all()
    out: list[tuple[str, date]] = []
    for rule_id, event_date in rows:
        _, _, key = str(rule_id).partition(":")
        if key:
            out.append((key, event_date))
    return out
