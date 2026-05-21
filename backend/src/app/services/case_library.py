from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import json


CASE_LIBRARY_DIR = Path(__file__).resolve().parent / "case_library"


def retrieve_case_context(
    *,
    message_text: str,
    analysis: dict[str, Any] | None = None,
    recent_user_messages: list[str] | None = None,
    limit: int = 1,
) -> str:
    query = "\n".join([message_text, *(recent_user_messages or [])])
    cases = _approved_cases()
    scored = [
        (score, case)
        for case in cases
        if (score := _score_case(case, query, analysis or {})) > 0
    ]
    scored.sort(key=lambda item: item[0], reverse=True)
    selected = [case for _, case in scored[:limit]]
    if not selected:
        return ""
    return "\n\n".join([_skill_text(), *(_render_case(case) for case in selected)])


@lru_cache(maxsize=1)
def _skill_text() -> str:
    path = CASE_LIBRARY_DIR / "skill.md"
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


@lru_cache(maxsize=1)
def _approved_cases() -> list[dict[str, Any]]:
    path = CASE_LIBRARY_DIR / "cases.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        return []
    return [
        item
        for item in data
        if isinstance(item, dict)
        and item.get("status") == "approved"
        and item.get("consent") in {"anonymized", "authorized"}
    ]


def _score_case(case: dict[str, Any], query: str, analysis: dict[str, Any]) -> int:
    haystack = query + "\n" + _analysis_text(analysis)
    score = 0
    for tag in case.get("tags") or []:
        tag_text = str(tag)
        if tag_text and tag_text in haystack:
            score += 4
    for field in ("usage", "before", "process", "after", "quote", "user_profile"):
        for token in _tokens(str(case.get(field) or "")):
            if token in haystack:
                score += 1
    emotion_signal = analysis.get("emotion_signal") if isinstance(analysis, dict) else None
    if isinstance(emotion_signal, dict) and emotion_signal.get("detected"):
        score += 2
    return score


def _analysis_text(analysis: dict[str, Any]) -> str:
    chunks: list[str] = []
    for key in ("emotion_signal", "renewal_intent", "refund_intent"):
        value = analysis.get(key)
        if isinstance(value, dict):
            chunks.extend(str(value.get(field) or "") for field in ("reason", "evidence"))
    for key in ("intervention_reasons", "risk_flags"):
        values = analysis.get(key)
        if isinstance(values, list):
            for item in values:
                if isinstance(item, dict):
                    chunks.extend(str(item.get(field) or "") for field in ("code", "reason", "evidence"))
    return "\n".join(chunks)


def _tokens(text: str) -> set[str]:
    import re

    return set(re.findall(r"[\w\u4e00-\u9fff]{2,}", text))


def _render_case(case: dict[str, Any]) -> str:
    tags = "、".join(str(item) for item in case.get("tags") or [])
    return (
        f"案例ID：{case.get('id')}\n"
        f"标签：{tags}\n"
        f"适用场景：{case.get('usage')}\n"
        f"用户背景：{case.get('user_profile')}\n"
        f"之前：{case.get('before')}\n"
        f"过程：{case.get('process')}\n"
        f"之后：{case.get('after')}\n"
        f"可引用原话：{case.get('quote')}\n"
        f"注意：{case.get('caution')}"
    )
