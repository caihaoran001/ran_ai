from __future__ import annotations

from dataclasses import dataclass

from app.evaluation.rag_cases import RagEvalCase
from app.services.ai_service.client import AIService
from app.services.message_insight_service import analyze_message_insight


@dataclass(frozen=True)
class IntentResult:
    insight: dict
    decision: str
    decision_source: str
    knowledge_query: str | None
    agent_knowledge_query: str | None


def format_history(case: RagEvalCase, *, limit: int) -> list[str]:
    labels = {"user": "用户", "staff": "工作人员", "assistant": "AI助教"}
    return [
        f"{labels[item.role]}：{item.content}"
        for item in case.history[-limit:]
    ]


def resolve_intent(insight: dict, *, original_query: str) -> IntentResult:
    if "knowledge_query" not in insight:
        return IntentResult(
            insight=insight,
            decision="retrieve",
            decision_source="fallback_original",
            knowledge_query=original_query,
            agent_knowledge_query=None,
        )
    value = insight.get("knowledge_query")
    if value is None:
        return IntentResult(
            insight=insight,
            decision="skip",
            decision_source="explicit_skip",
            knowledge_query=None,
            agent_knowledge_query="",
        )
    if isinstance(value, str) and value.strip():
        clean = value.strip()
        return IntentResult(
            insight=insight,
            decision="retrieve",
            decision_source="rewritten",
            knowledge_query=clean,
            agent_knowledge_query=clean,
        )
    return IntentResult(
        insight=insight,
        decision="retrieve",
        decision_source="fallback_original",
        knowledge_query=original_query,
        agent_knowledge_query=None,
    )


def install_in_memory_usage_collector() -> list[dict]:
    """Prevent the intent model's normal observability hook from writing RDS."""

    collected: list[dict] = []

    async def _collect_usage(self, **payload) -> None:
        collected.append(payload)

    AIService._record_usage = _collect_usage  # type: ignore[method-assign]
    return collected


async def evaluate_intent(case: RagEvalCase) -> IntentResult:
    recent_messages = format_history(case, limit=10)
    insight = await analyze_message_insight(
        source="chat_message",
        content=case.user_query,
        context={
            "service_case_id": 0,
            "message_id": 0,
            "group_id": "rag-evaluation",
            "current_message": case.user_query,
            "recent_user_messages": recent_messages,
        },
    )
    return resolve_intent(insight, original_query=case.user_query)
