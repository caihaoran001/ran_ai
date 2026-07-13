"""追问补齐：日打卡 H5 下线后的完整度兜底（H5 的完整替代）。

H5 永久下线后，聊天抽取 + 本模块的追问是打卡数据的**全部来源**。追问统一为"外部确定性触发、
每 case 每天最多一条"（不再注入系统 prompt 让 agent 自由裁量），由两条链路复用本模块：

- **活跃触发**（`ranren_ai/engine.py`）：用户当天在群里说过话时，agent 回复发送成功后单独补发一条。
- **不活跃兜底**（`reminder_service.check_checkin_followup_once`）：用户当天没说话，到当天一个确定性
  随机时刻主动发一条。

两条链路共用 `ReminderEvent(rule_id="checkin_followup")` 天级去重闸（唯一约束保证一天一条、绝不叠加），
统一调 `compose_daily_followup` 组织内容：按优先级只问一个主题，合并成一句自然话——
里程碑复盘（day7/14/21/28 阶段报告要的腰围+复盘，前一晚追）> 矛盾核对（表单值≠聊天值）>
日常缺口（早C/午B、体重合并；晚A 睡前那顿不主动追）。判定读 checkins 现状 + service_day。

日常缺口追问统一在傍晚（`CHECKIN_FOLLOWUP_WINDOW_*`，默认 19:30~20:30）发一次：时间闸放在调用方
（engine 活跃触发 / reminder 不活跃兜底），本模块只负责组织内容、不做发送时机裁量。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.checkin import Checkin
from app.models.service_case import ServiceCase
from app.services.service_case_queries import effective_service_day

# 早C/午B/晚A 三段产品依从：最不会自然出现在聊天里、又是阶段报告核心指标。
# （仅作矛盾核对等的字段标签来源，见 _FIELD_LABELS；主动缺口追问的覆盖范围见 _DAILY_GAP_SLOTS。）
_PRODUCT_SLOTS = {"morning_c": "早C", "noon_b": "午B", "night_a": "晚A"}
# 主动「日常缺口」追问只覆盖早C/午B：缺口追问统一在傍晚(见 CHECKIN_FOLLOWUP_WINDOW_*)发一次，
# 那时晚A(睡前那顿)还没到点，主动追只会变成"没到点就催晚A"，故不纳入——晚A 靠聊天抽取/次日补齐。
_DAILY_GAP_SLOTS = {"morning_c": "早C", "noon_b": "午B"}
_WEIGHT_LOOKBACK_DAYS = 3
_WAIST_LOOKBACK_DAYS = 7  # 腰围一周测一次，避免天天问。

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
    """当天仍缺、且可主动追问的产品依从槽位：返回 [{"key","label"}]。

    只看 _DAILY_GAP_SLOTS（早C/午B）；晚A 不主动追（见其定义处说明）。
    """
    ps = (parsed_json or {}).get("product_schedule") if isinstance(parsed_json, dict) else None
    ps = ps if isinstance(ps, dict) else {}
    return [
        {"key": key, "label": label}
        for key, label in _DAILY_GAP_SLOTS.items()
        if not _slot_filled(ps.get(key))
    ]


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


def _daily_gap_fallback_message(labels: list[str]) -> str:
    """日常缺口的固定模板兜底（LLM 不可用时使用）。合并所有缺口为一句，不逐项拆问。"""
    return f"今天还没看到你的{('、'.join(labels))}打卡，方便的话跟我说一下哈～"


async def _compose_daily_gap_message(
    *,
    service_case: ServiceCase,
    missing_slots: list[dict],
    ask_weight: bool,
    ai_service: object | None = None,
) -> str | None:
    """日常缺口：把当天缺的产品依从 + 体重合并成一句自然口语追问；无缺口返回 None。

    LLM 生成保证自然（参考 compose_milestone_followup 的 LLM+模板兜底模式），失败回退模板。
    刻意合并成一句、不按字段逐条拆问，避免一天问多次。
    """
    labels = [s["label"] for s in (missing_slots or [])]
    if ask_weight:
        labels.append("体重")
    if not labels:
        return None
    try:
        if ai_service is None:
            from app.services.ai_service.client import AIService

            ai_service = AIService()
        system_prompt = (
            "你是燃人微信群里的健康陪伴助教，用一句口语化中文自然地提醒用户补一下今天还没报的打卡项。"
            "要求：亲切自然、不超过 50 字、只发一句、不要罗列编号、不要用称呼开头、不要用波浪号以外的语气符号堆砌。"
            "把所有缺口揉进同一句里顺口带过，不要逐项拆开分别问。"
        )
        user_prompt = f"今天还没收到的打卡项：{('、'.join(labels))}。请生成一句自然的提醒补打卡的话。"
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
    return _daily_gap_fallback_message(labels)


@dataclass
class DailyFollowup:
    """一条主动追问的组织结果：文本 + 类型 + （矛盾核对时）待标 asked 的字段。

    kind 供调用方在发送成功后做后处理：矛盾核对需调 mark_contradictions_pending_confirmation。
    """

    text: str
    kind: str  # "milestone" | "contradiction" | "daily_gap"
    confirmation_fields: list[str] = field(default_factory=list)


async def compose_daily_followup(
    session: AsyncSession,
    *,
    service_case: ServiceCase,
    now: datetime,
    tz: ZoneInfo,
    ai_service: object | None = None,
) -> DailyFollowup | None:
    """统一「今日主动追问」：按优先级只问一个主题，合并成一句自然话；无可问返回 None。

    优先级：里程碑复盘 > 矛盾核对 > 日常缺口（早C/午B/晚A、体重合并）。
    活跃触发（engine）与不活跃兜底（reminder_service）共用本函数，配合 case 级天闸保证一天一条。
    """
    detect = await detect_followup(session, service_case=service_case, now=now, tz=tz)

    milestone = detect.get("milestone")
    if milestone:
        text = await compose_milestone_followup(
            service_case=service_case,
            milestone_day=milestone["milestone_day"],
            need_waist=milestone["need_waist"],
            ai_service=ai_service,
        )
        return DailyFollowup(text=text, kind="milestone")

    confirmations = detect.get("confirmations") or []
    conf_msg = compose_followup_message(confirmations=confirmations)
    if conf_msg:
        return DailyFollowup(
            text=conf_msg,
            kind="contradiction",
            confirmation_fields=[c["field"] for c in confirmations],
        )

    gap_msg = await _compose_daily_gap_message(
        service_case=service_case,
        missing_slots=detect.get("missing_slots") or [],
        ask_weight=bool(detect.get("ask_weight")),
        ai_service=ai_service,
    )
    if gap_msg:
        return DailyFollowup(text=gap_msg, kind="daily_gap")
    return None
