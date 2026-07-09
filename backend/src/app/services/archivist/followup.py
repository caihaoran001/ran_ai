"""追问补齐：日打卡 H5 下线后的完整度兜底（H5 的完整替代）。

H5 永久下线后，聊天抽取 + 本模块的晚间追问是打卡数据的**全部来源**，故追问分两档：

- **日常档**（写死模板变体，零 LLM）：对当天仍缺的产品依从（早C/午B/晚A）、体重、
  以及需向用户核对的矛盾，用一句自然语言追问；话术按服务单 id 轮换变体，避免千人一面。
- **里程碑档**（LLM 个性化，带模板兜底）：day7/14/21/28 阶段报告要用的腰围 + 复盘信息
  （这阶段最大变化 / 最大卡点 / 下阶段重点），在里程碑**前一晚**（服务第 6/13/20/27 天）
  追问，给用户约一天答复，数据赶在里程碑当晚 21:00 阶段报告/成效快照生成前落库。
  用户答复经档案官聊天抽取落 checkin.waist（喂快照）+ 进聊天历史（喂阶段报告 LLM）。

判定读 checkins 现状 + service_day；时机/去重/发送复用 reminder_service（见 check_checkin_followup_once）。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.checkin import Checkin
from app.models.service_case import ServiceCase
from app.services.service_case_queries import effective_service_day

# 早C/午B/晚A 三段产品依从：最不会自然出现在聊天里、又是阶段报告核心指标。
_PRODUCT_SLOTS = {"morning_c": "早C", "noon_b": "午B", "night_a": "晚A"}
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

# 日常追问话术变体：按服务单 id 轮换，避免千人一面。花括号留字段占位。
_SLOT_VARIANTS = [
    "今天的{slots}喝了吗？喝了的话跟我说一声就行～",
    "对了，今天{slots}安排上了没？记得的话回我一下哈。",
    "今天{slots}别忘了哦，喝了跟我报一声我好记录～",
]
_WEIGHT_VARIANTS = [
    "这几天还没看到你的体重，方便的话早上空腹量一下发我记录一下哈。",
    "好几天没收到体重啦，明早空腹称一下发我呗，我帮你记上。",
    "顺便问下体重，这两天还没记录，早上空腹量一量告诉我哈～",
]


def _field_label(field_key: str) -> str:
    return _FIELD_LABELS.get(field_key, field_key)


def _slot_filled(value: object) -> bool:
    return bool(str(value or "").strip())


def _missing_product_slots(parsed_json: dict | None) -> list[str]:
    ps = (parsed_json or {}).get("product_schedule") if isinstance(parsed_json, dict) else None
    ps = ps if isinstance(ps, dict) else {}
    return [label for key, label in _PRODUCT_SLOTS.items() if not _slot_filled(ps.get(key))]


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
        missing_slots: [中文标签],
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
    missing_slots: list[str],
    ask_weight: bool,
    confirmations: list[dict] | None = None,
    case_id: int = 0,
) -> str | None:
    """日常档：把缺失项/待确认项组织成一句自然语言追问；无内容返回 None。零 LLM。

    话术按 case_id 轮换变体，避免所有用户收到一模一样的话。措辞刻意口语、无链接。
    矛盾确认刻意做成"跟你核对一下最新数值"，不暴露"系统发现你自相矛盾"，体验更好；
    用户答复的新值会覆盖旧记录（见 mark_contradictions_pending_confirmation）。
    """
    parts: list[str] = []
    if missing_slots:
        variant = _SLOT_VARIANTS[case_id % len(_SLOT_VARIANTS)]
        parts.append(variant.format(slots="/".join(missing_slots)))
    if ask_weight:
        parts.append(_WEIGHT_VARIANTS[case_id % len(_WEIGHT_VARIANTS)])
    for c in confirmations or []:
        label = c.get("label") or c.get("field")
        chat_value = c.get("chat_value")
        parts.append(
            f"另外跟你核对下{label}，我这边记的和你最近说的对不太上，"
            f"现在准确是{('多少' if not chat_value else f'{chat_value} 吗')}？我按你说的更新哈。"
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
            "要求：亲切自然、不超过 80 字、不发任何链接、不要罗列编号。"
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
