"""追问补齐：日打卡 H5 下线后的完整度兜底。

档案官被动抽取拿不到"用户不会主动播报"的字段（尤其 早C/午B/晚A 依从、体重），
故对当天仍缺的关键字段，用一句自然语言在群里追问（不发链接）。判定读 checkins 现状，
时机/去重/发送复用 reminder_service（见 check_checkin_followup_once）。
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.checkin import Checkin

# 早C/午B/晚A 三段产品依从：最不会自然出现在聊天里、又是阶段报告核心指标。
_PRODUCT_SLOTS = {"morning_c": "早C", "noon_b": "午B", "night_a": "晚A"}
_WEIGHT_LOOKBACK_DAYS = 3


def _slot_filled(value: object) -> bool:
    return bool(str(value or "").strip())


def _missing_product_slots(parsed_json: dict | None) -> list[str]:
    ps = (parsed_json or {}).get("product_schedule") if isinstance(parsed_json, dict) else None
    ps = ps if isinstance(ps, dict) else {}
    return [label for key, label in _PRODUCT_SLOTS.items() if not _slot_filled(ps.get(key))]


async def detect_followup(
    session: AsyncSession, *, service_case_id: int, today: date
) -> dict:
    """判断今天还缺哪些关键字段。返回 {missing_slots: [中文标签], ask_weight: bool}。"""
    today_checkin = await session.scalar(
        select(Checkin).where(
            Checkin.service_case_id == service_case_id,
            Checkin.checkin_date == today,
            Checkin.deleted_at.is_(None),
        )
    )
    missing_slots = _missing_product_slots(today_checkin.parsed_json if today_checkin else None)

    # 体重不是每天必报：仅当最近 N 天都没有记录才追问，避免天天问体重。
    since = today - timedelta(days=_WEIGHT_LOOKBACK_DAYS - 1)
    recent = (
        await session.scalars(
            select(Checkin).where(
                Checkin.service_case_id == service_case_id,
                Checkin.checkin_date >= since,
                Checkin.deleted_at.is_(None),
            )
        )
    ).all()
    ask_weight = not any(
        _slot_filled((c.parsed_json or {}).get("weight")) for c in recent
    )
    return {"missing_slots": missing_slots, "ask_weight": ask_weight}


def compose_followup_message(*, missing_slots: list[str], ask_weight: bool) -> str | None:
    """把缺失项组织成一句自然语言追问；无缺失返回 None。措辞刻意口语、无链接。"""
    parts: list[str] = []
    if missing_slots:
        parts.append(f"今天的{'/'.join(missing_slots)}喝了吗？喝了的话跟我说一声就行～")
    if ask_weight:
        parts.append("这几天还没看到你的体重，方便的话早上空腹量一下发我记录一下哈。")
    if not parts:
        return None
    return " ".join(parts)
