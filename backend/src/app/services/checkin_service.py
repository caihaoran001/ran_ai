from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.checkin import Checkin
from app.models.service_case import ServiceCase


CHECKIN_KEYS = ("product_schedule", "water", "sleep", "mood", "exercise", "weight")


def is_checkin_message(text: str, pattern: str = r"^#打卡") -> bool:
    return re.search(pattern, text or "") is not None


async def create_checkin_from_message(
    session: AsyncSession,
    *,
    service_case: ServiceCase,
    content: str,
    raw_message_id: str | None,
) -> Checkin:
    parsed_json = parse_checkin_content(content)
    checkin = Checkin(
        service_case_id=service_case.id,
        group_id=service_case.group_id,
        user_id=service_case.user_id,
        checkin_date=datetime.now(ZoneInfo("Asia/Shanghai")).date(),
        content=content,
        raw_message_id=raw_message_id,
        parsed_json=parsed_json,
    )
    session.add(checkin)
    return checkin


def parse_checkin_content(content: str) -> dict[str, Any]:
    text = content or ""
    return {
        "product_schedule": _parse_product_schedule(text),
        "water": _match_value(text, ("饮水", "喝水", "水量")),
        "sleep": _match_value(text, ("睡眠", "昨晚睡觉时间", "睡觉时间", "入睡", "起床")),
        "mood": _match_value(text, ("情绪", "心情", "状态")),
        "exercise": _match_value(text, ("运动", "步数", "走路", "训练")),
        "weight": _match_value(text, ("体重", "今日体重", "今天体重")),
    }


def normalize_checkin_json(value: dict[str, Any] | None, content: str) -> dict[str, Any]:
    parsed = parse_checkin_content(content)
    if not value:
        return parsed
    for key in CHECKIN_KEYS:
        if value.get(key) not in (None, ""):
            parsed[key] = value.get(key)
    return parsed


def _parse_product_schedule(text: str) -> dict[str, Any]:
    schedule = {
        "morning_c": _done_status(text, ("早C", "早 C", "早c")),
        "noon_b": _done_status(text, ("午B", "午 B", "午b")),
        "night_a": _done_status(text, ("晚A", "晚 A", "晚a")),
    }
    value = _match_value(text, ("早C午B晚A", "早 C 午 B 晚 A", "三杯", "产品"))
    if value:
        schedule["summary"] = value
    return schedule


def _done_status(text: str, keys: tuple[str, ...]) -> str:
    for key in keys:
        pattern = rf"{re.escape(key)}\s*[:：]?\s*([^\s；;，,。]*)"
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            value = match.group(1).strip()
            if any(word in value for word in ("未", "没", "漏", "无")):
                return "未完成"
            if any(word in value for word in ("已", "完成", "喝", "ok", "OK", "√")):
                return "已完成"
            return value or "已提及"
    return ""


def _match_value(text: str, labels: tuple[str, ...]) -> str:
    for label in labels:
        pattern = rf"{re.escape(label)}\s*[:：]\s*([^\n]+)"
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    for line in text.splitlines():
        stripped = line.strip(" -•\t")
        for label in labels:
            if stripped.startswith(label):
                value = stripped[len(label):].strip(" :：")
                if value:
                    return value
    return ""
