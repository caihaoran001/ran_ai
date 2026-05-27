from __future__ import annotations

import json
import logging
from typing import Any

from app.models.service_case import ServiceCase
from app.services.ai_service import AIService

logger = logging.getLogger(__name__)


async def analyze_intervention(
    *,
    source: str,
    content: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ai = AIService()
    try:
        insight = await ai.extract_intervention_insight(
            source=source,
            content=content,
            context=context,
        )
    except Exception:
        logger.exception("Failed to analyze intervention insight for source=%s", source)
        insight = _classifier_unavailable_insight(source)
    return normalize_insight(insight, source=source, content=_content_with_context(content, context))


def _classifier_unavailable_insight(source: str) -> dict[str, Any]:
    return {
        "requires_human_intervention": True,
        "risk_flags": [
            {
                "code": "risk_classifier_unavailable",
                "level": "medium",
                "reason": "风控/意图识别服务调用失败，当前内容需要人工兜底复核。",
                "evidence": "LLM 风控识别失败",
                "source": source,
            }
        ],
        "intervention_reasons": [
            {
                "code": "risk_classifier_unavailable",
                "level": "medium",
                "reason": "需要人工复核后再继续自动生成或发送内容。",
                "evidence": "LLM 风控识别失败",
                "source": source,
            }
        ],
        "confidence": 0.0,
        "notes": "风控识别失败，已按中风险人工复核兜底处理。",
    }


def normalize_insight(insight: dict[str, Any], *, source: str, content: str) -> dict[str, Any]:
    risk_flags = _normalize_items(insight.get("risk_flags"), source=source)
    intervention_reasons = _normalize_items(insight.get("intervention_reasons"), source=source)
    renewal_intent = _normalize_intent(insight.get("renewal_intent"), source=source)
    refund_intent = _normalize_intent(insight.get("refund_intent"), source=source)
    emotion_signal = _normalize_emotion_signal(insight.get("emotion_signal"), source=source)

    if risk_flags:
        intervention_reasons.extend(
            {
                "code": item.get("code", "risk"),
                "level": item.get("level", "high"),
                "reason": item.get("reason") or "",
                "evidence": item.get("evidence") or "",
                "source": item.get("source") or source,
            }
            for item in risk_flags
        )

    if emotion_signal["detected"] and emotion_signal.get("level") == "high":
        intervention_reasons.append(
            {
                "code": "emotional_distress",
                "level": "high",
                "reason": emotion_signal.get("reason") or "",
                "evidence": emotion_signal.get("evidence") or "",
                "source": emotion_signal.get("source") or source,
            }
        )
    
    has_high_intervention = any(r.get("level") in {"high", "medium"} for r in intervention_reasons)
    requires = bool(insight.get("requires_human_intervention")) or has_high_intervention
    return {
        "requires_human_intervention": requires,
        "intervention_reasons": _dedupe_items(intervention_reasons),
        "risk_flags": _dedupe_items(risk_flags),
        "renewal_intent": renewal_intent,
        "refund_intent": refund_intent,
        "emotion_signal": emotion_signal,
        "confidence": _number(insight.get("confidence"), default=0.0),
        "notes": _string(insight.get("notes")),
    }


def merge_case_insight(service_case: ServiceCase, insight: dict[str, Any]) -> None:
    risks = _normalize_items(insight.get("risk_flags"), source="")
    if risks:
        has_high_risk = any(r.get("level") == "high" for r in risks)
        has_medium_risk = any(r.get("level") == "medium" for r in risks)
        if has_high_risk:
            service_case.risk_level = "high"
        elif has_medium_risk and service_case.risk_level != "high":
            service_case.risk_level = "medium"
        service_case.risk_flags = _merge_lists(service_case.risk_flags, risks)

    reasons = _normalize_items(insight.get("intervention_reasons"), source="")
    has_high_reasons = any(r.get("level") in {"high", "medium"} for r in reasons)
    if insight.get("requires_human_intervention") or has_high_reasons:
        service_case.needs_human_intervention = True
    service_case.intervention_reasons = _merge_lists(service_case.intervention_reasons, reasons)

    renewal = insight.get("renewal_intent")
    if isinstance(renewal, dict) and renewal.get("detected"):
        service_case.renewal_intent_flags = _merge_lists(service_case.renewal_intent_flags, [renewal])

    refund = insight.get("refund_intent")
    if isinstance(refund, dict) and refund.get("detected"):
        service_case.refund_intent_flags = _merge_lists(service_case.refund_intent_flags, [refund])


def questionnaire_content(fields: list[dict], answers: dict[str, Any]) -> str:
    labels = {str(field.get("key") or field.get("id")): str(field.get("title") or field.get("label") or field.get("key") or "") for field in fields}
    lines = []
    for key, value in answers.items():
        label = labels.get(str(key), str(key))
        lines.append(f"{label}: {_answer_text(value)}")
    return "\n".join(lines)


def _normalize_items(value: Any, *, source: str) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    items = []
    for item in value:
        if not isinstance(item, dict):
            continue
        code = _string(item.get("code")) or "intervention"
        items.append(
            {
                "code": code,
                "level": _string(item.get("level")) or "medium",
                "reason": _string(item.get("reason")),
                "evidence": _string(item.get("evidence") or item.get("keyword")),
                "source": _string(item.get("source")) or source,
            }
        )
    return items


def _normalize_intent(value: Any, *, source: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        value = {}
    return {
        "detected": bool(value.get("detected")),
        "reason": _string(value.get("reason")),
        "evidence": _string(value.get("evidence")),
        "source": _string(value.get("source")) or source,
    }


def _normalize_emotion_signal(value: Any, *, source: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        value = {}
    level = _string(value.get("level")) or "medium"
    if level not in {"low", "medium", "high"}:
        level = "medium"
    return {
        "detected": bool(value.get("detected")),
        "level": level,
        "reason": _string(value.get("reason")),
        "evidence": _string(value.get("evidence")),
        "source": _string(value.get("source")) or source,
    }


def _merge_lists(existing: list[dict] | None, incoming: list[dict]) -> list[dict]:
    return _dedupe_items([*(existing or []), *incoming])


def _dedupe_items(items: list[dict]) -> list[dict]:
    deduped = []
    seen = set()
    for item in items:
        key = (
            item.get("code"),
            item.get("evidence"),
            item.get("source"),
            item.get("reason"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _answer_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _string(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _number(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _content_with_context(content: str, context: dict[str, Any] | None) -> str:
    if not context:
        return content
    recent = context.get("recent_user_messages")
    if not isinstance(recent, list):
        return content
    recent_text = "\n".join(str(item) for item in recent if item)
    return "\n".join(part for part in (recent_text, content) if part)
