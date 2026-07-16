from __future__ import annotations

import json
import logging
from typing import Any

from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.services.ai_service import AIService

logger = logging.getLogger(__name__)

VALID_RISK_CODES = frozenset({
    "pregnancy", "lactation", "chronic_disease", "depression",
    "long_term_medication", "hormone_medication", "liver_kidney", "stone",
    "severe_test_abnormality", "severe_insomnia",
    "chest_pain", "palpitation", "allergy", "medical_claim", "other_risk",
    "emotional_distress", "risk_classifier_unavailable",
    "medication", "physical_discomfort", "complaint", "robot_stop", "refund_request",
    "elderly_age",
})

VALID_RISK_LEVELS = frozenset({"low", "medium", "high"})

# 服务单上的「高风险」是持续健康背景，不是一次需要班主任接手的事件。
# 高风险必须可被运营从用户原话核验；急性不适、诊疗/疗效承诺边界、投诉退款等
# 仍会转人工，但只作为本次事件留在 intervention_reasons，不升级用户风险等级。
PERSISTENT_HIGH_RISK_CODES = frozenset({
    "pregnancy",
    "lactation",
    "chronic_disease",
    "long_term_medication",
    "hormone_medication",
    "liver_kidney",
    "stone",
    "severe_test_abnormality",
    "depression",
})

VALID_SOURCES = frozenset({"chat_message", "questionnaire"})

VALID_AGENT_MESSAGE_SCENES = frozenset({
    "product_rule",
    "data_query",
    "diet_advice",
    "body_feedback",
    "emotion_support",
    "casual_interest",
    "correction",
    "unknown",
})

VALID_AGENT_MEMORY_SCOPES = frozenset({
    "stable_facts",
    "preferences",
    "habits",
    "current_state",
    "warnings",
})

VALID_AGENT_TOOL_HINTS = frozenset({
    "service_case_data",
    "product_rule",
    "web_search",
    "knowledge_base",
})

# 归档信号 archive_plan.kinds 合法取值：给档案官(后台归档)判断本条含哪类可沉淀信息。
# checkin/profile_fact/outcome 供 P0 起用；correction/knowledge_qa 为后续阶段(P2)预留。
VALID_ARCHIVE_KINDS = frozenset({
    "checkin",
    "profile_fact",
    "outcome",
    "correction",
    "knowledge_qa",
})


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

    if refund_intent["detected"]:
        intervention_reasons.append(
            {
                "code": "refund_request",
                "level": "medium",
                "reason": refund_intent.get("reason") or "用户明确表达退款或退出意向，需要人工处理。",
                "evidence": refund_intent.get("evidence") or content[:120],
                "source": refund_intent.get("source") or source,
            }
        )
    
    has_high_intervention = any(r.get("level") in {"high", "medium"} for r in intervention_reasons)
    requires = bool(insight.get("requires_human_intervention")) or has_high_intervention
    normalized = {
        "requires_human_intervention": requires,
        "intervention_reasons": _dedupe_items(intervention_reasons),
        "risk_flags": _dedupe_items(risk_flags),
        "renewal_intent": renewal_intent,
        "refund_intent": refund_intent,
        "emotion_signal": emotion_signal,
        "confidence": _number(insight.get("confidence"), default=0.0),
        "notes": _string(insight.get("notes")),
    }
    if "requires_immediate_safety_reply" in insight:
        normalized["requires_immediate_safety_reply"] = bool(insight.get("requires_immediate_safety_reply"))
    normalized["health_consultation"] = bool(insight.get("health_consultation"))
    agent_context_plan = _normalize_agent_context_plan(insight.get("agent_context_plan"))
    if agent_context_plan is not None:
        normalized["agent_context_plan"] = agent_context_plan
    archive_plan = _normalize_archive_plan(insight.get("archive_plan"))
    if archive_plan is not None:
        normalized["archive_plan"] = archive_plan
    return normalized


def merge_case_insight(service_case: ServiceCase, insight: dict[str, Any]) -> None:
    risks = _normalize_items(insight.get("risk_flags"), source="")
    if risks:
        has_high_risk = any(_is_persistent_high_risk(r) for r in risks)
        has_medium_risk = any(r.get("level") == "medium" for r in risks)
        if has_high_risk:
            service_case.risk_level = "high"
        elif has_medium_risk and service_case.risk_level != "high":
            service_case.risk_level = "medium"
        service_case.risk_flags = _merge_lists(service_case.risk_flags, risks)

    reasons = _normalize_items(insight.get("intervention_reasons"), source="")
    # 转人工不再是服务单状态；保留历史原因，供画像、运营回溯和 LLM 上下文参考。
    service_case.intervention_reasons = _merge_lists(service_case.intervention_reasons, reasons)

    renewal = insight.get("renewal_intent")
    if isinstance(renewal, dict) and renewal.get("detected"):
        service_case.renewal_intent_flags = _merge_lists(service_case.renewal_intent_flags, [renewal])

    refund = insight.get("refund_intent")
    if isinstance(refund, dict) and refund.get("detected"):
        service_case.refund_intent_flags = _merge_lists(service_case.refund_intent_flags, [refund])
    # 模型识别到"退款已成为事实"→进入后台"已退款待确认"列表(不改状态、不停推)，等运营确认。
    # 已是 REFUNDED 的无需再提示；候选标记一旦置真则保持，由运营确认/驳回时清除。
    if (
        isinstance(refund, dict)
        and refund.get("completed")
        and service_case.status != ServiceCaseStatus.REFUNDED
    ):
        service_case.refund_pending_confirmation = True


def mark_intake_age_risk(service_case: ServiceCase, fields: list[dict], answers: dict[str, Any]) -> None:
    """入营问卷高龄客户标记高风险：age>=70 需个案评估/线下把关。

    标记后 is_high_risk() 为真 → 文档不自动发、强制人工审核（与慢病一致）。
    阈值 70 覆盖前端 70-85 二次确认区间；>85 前端已拦截，此处兜底防绕过。
    年龄取值口径与 build_intake_fact_summary 一致（按字段 title 含「年龄」）。
    """
    from app.services.intake_facts import _first_number, build_intake_fact_summary

    age_raw = build_intake_fact_summary(fields, answers).get("age") or ""
    age = _first_number(str(age_raw))
    if age is None or age < 70:
        return
    flag = {
        "code": "elderly_age",
        "level": "high",
        "reason": f"高龄用户（{int(age)}岁），需个案评估/线下把关",
        "evidence": f"问卷年龄：{age_raw}",
        "source": "questionnaire",
    }
    service_case.risk_flags = _merge_lists(service_case.risk_flags, [flag])
    service_case.risk_level = "high"


def _normalize_agent_context_plan(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    message_scene = str(value.get("message_scene") or "unknown").strip()
    if message_scene not in VALID_AGENT_MESSAGE_SCENES:
        message_scene = "unknown"
    memory_scopes = [
        item
        for item in _string_list(value.get("memory_scopes"))
        if item in VALID_AGENT_MEMORY_SCOPES
    ]
    tool_hints = [
        item
        for item in _string_list(value.get("tool_hints"))
        if item in VALID_AGENT_TOOL_HINTS
    ]
    return {
        "message_scene": message_scene,
        "memory_scopes": _dedupe_strings(memory_scopes),
        "tool_hints": _dedupe_strings(tool_hints),
        "reason": _string(value.get("reason"))[:160],
    }


def _normalize_archive_plan(value: Any) -> dict[str, Any] | None:
    """规范化归档信号。搭车意图识别 LLM，给档案官判断本条是否含可长期沉淀的信息。

    与风险/转人工完全独立，只做轻量打标(worthy+kinds)，不抽具体值。
    偏向与风控相反：可能含数据/事实即 worthy=true(误报只白跑一次抽取，漏报=打卡继续空)。
    """
    if not isinstance(value, dict):
        return None
    kinds = [item for item in _string_list(value.get("kinds")) if item in VALID_ARCHIVE_KINDS]
    kinds = _dedupe_strings(kinds)
    worthy = bool(value.get("worthy")) or bool(kinds)
    return {
        "worthy": worthy,
        "kinds": kinds,
        "reason": _string(value.get("reason"))[:160],
    }


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
        code = _string(item.get("code")) or "other_risk"
        if code not in VALID_RISK_CODES:
            code = "other_risk"
        level = _string(item.get("level")) or "medium"
        if level not in VALID_RISK_LEVELS:
            level = "medium"
        reason = _string(item.get("reason"))
        evidence = _string(item.get("evidence") or item.get("keyword"))
        # “high”只能用于有明确分类、有理由且可回查用户原话的持续健康风险。
        # 不满足时保守降为中风险：照样转人工，不把该用户永久挂成高风险。
        if level == "high" and not _is_persistent_high_risk({
            "code": code,
            "reason": reason,
            "evidence": evidence,
        }):
            level = "medium"
        raw_source = _string(item.get("source"))
        items.append(
            {
                "code": code,
                "level": level,
                "reason": reason,
                "evidence": evidence,
                "source": raw_source if raw_source in VALID_SOURCES else source,
            }
        )
    return items


def _is_persistent_high_risk(item: dict[str, str]) -> bool:
    return bool(
        item.get("code") in PERSISTENT_HIGH_RISK_CODES
        and (item.get("reason") or "").strip()
        and (item.get("evidence") or "").strip()
    )


def _normalize_intent(value: Any, *, source: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        value = {}
    return {
        "detected": bool(value.get("detected")),
        "completed": bool(value.get("completed")),  # 仅 refund_intent 使用：退款是否已成为事实
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


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_string(item) for item in value if _string(item)]


def _dedupe_strings(items: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
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
