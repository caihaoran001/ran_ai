from __future__ import annotations

from collections import defaultdict

from app.models.checkin import Checkin


def _nonempty(value: object) -> bool:
    return bool(str(value or "").strip())


def is_complete_checkin_payload(parsed_json: object) -> bool:
    """完整打卡：早C/午B/晚A 和同一版清单的第 2—5 项都有回答。

    只判断是否如实回答，不判断是否完成行为；“未完成”和“暂停”
    也是有效回答。第 2—5 项必须来自同一版清单，避免清单更换后拼凑。
    """
    parsed = parsed_json if isinstance(parsed_json, dict) else {}
    schedule = parsed.get("product_schedule")
    if not isinstance(schedule, dict) or not all(
        _nonempty(schedule.get(key))
        for key in ("morning_c", "noon_b", "night_a")
    ):
        return False

    return complete_checkin_item_prefix(parsed) is not None


def complete_checkin_item_prefix(parsed_json: object) -> str | None:
    """返回使当天完整的清单版本；多版都完整时固定取字典序最小者。"""

    parsed = parsed_json if isinstance(parsed_json, dict) else {}
    custom = parsed.get("custom_checkin")
    if not isinstance(custom, dict):
        return None
    answered_by_prefix: dict[str, set[int]] = defaultdict(set)
    for raw_item_id, value in custom.items():
        if not isinstance(value, dict) or not _nonempty(value.get("answer")):
            continue
        prefix, separator, raw_index = str(raw_item_id).rpartition(":")
        if not separator or raw_index not in {"2", "3", "4", "5"}:
            continue
        answered_by_prefix[prefix].add(int(raw_index))
    complete_prefixes = sorted(
        prefix
        for prefix, indexes in answered_by_prefix.items()
        if indexes == {2, 3, 4, 5}
    )
    return complete_prefixes[0] if complete_prefixes else None


def is_complete_checkin(checkin: Checkin) -> bool:
    return is_complete_checkin_payload(checkin.parsed_json)


def complete_checkin_day_count(rows: list[Checkin]) -> int:
    return len({row.checkin_date for row in rows if is_complete_checkin(row)})
