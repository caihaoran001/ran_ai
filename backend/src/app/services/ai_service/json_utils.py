from __future__ import annotations

import json
from typing import Any


def parse_json_object(text: str) -> dict[str, Any]:
    normalized_text = strip_code_fence(text)
    parsed = _loads_json_object(normalized_text)
    return _normalize_json_value(parsed, normalized_text)


def strip_code_fence(text: str) -> str:
    normalized = (text or "").strip()
    if not normalized.startswith("```"):
        return normalized
    lines = normalized.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _loads_json_object(text: str) -> Any | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return None
    return None


def _normalize_json_value(value: Any, raw_text: str) -> dict[str, Any]:
    if isinstance(value, dict):
        for key in ("summary", "body", "content"):
            nested = _nested_json_dict(value.get(key))
            if nested is not None:
                return nested
        return value
    if isinstance(value, str):
        nested = _nested_json_dict(value)
        if nested is not None:
            return nested
        return {"summary": value.strip()}
    return {"summary": raw_text}


def _nested_json_dict(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, str):
        return None
    parsed = _loads_json_object(strip_code_fence(value))
    return parsed if isinstance(parsed, dict) else None
