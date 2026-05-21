from __future__ import annotations

from typing import Any

CHECKIN_KEYS = (
    "product_schedule",
    "water",
    "sleep",
    "mood",
    "exercise",
    "weight",
    "waist",
    "diet",
    "bowel",
    "hunger",
    "sensation",
    "discomfort",
    "tomorrow_focus",
)


def normalize_checkin_json(value: dict[str, Any] | None, content: str) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    if not value:
        return parsed
    for key in CHECKIN_KEYS:
        if value.get(key) not in (None, ""):
            parsed[key] = value.get(key)
    return parsed
