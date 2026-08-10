from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI

from app.core.config import Settings


@dataclass(frozen=True)
class RagasScores:
    context_recall: float
    context_precision: float
    faithfulness: float
    answer_relevancy: float
    raw_context_recall: float


class RagasScorer:
    """Thin adapter around the non-deprecated RAGAS 0.4 collections API."""

    def __init__(self, settings: Settings):
        from ragas.embeddings import embedding_factory
        from ragas.llms import llm_factory
        from ragas.metrics.collections import (
            AnswerRelevancy,
            ContextPrecision,
            ContextRecall,
            Faithfulness,
        )

        judge_client = AsyncOpenAI(
            api_key=settings.ai_api_key,
            base_url=settings.ai_base_url,
            timeout=settings.ai_timeout_seconds,
        )
        embedding_client = AsyncOpenAI(
            api_key=settings.embedding_api_key,
            base_url=settings.embedding_base_url,
            timeout=settings.ai_timeout_seconds,
        )
        llm = llm_factory(settings.ai_model, client=judge_client)
        embeddings = embedding_factory(
            "openai",
            model=settings.embedding_model,
            client=embedding_client,
        )
        self._context_recall = ContextRecall(llm=llm)
        self._context_precision = ContextPrecision(llm=llm)
        self._faithfulness = Faithfulness(llm=llm)
        self._answer_relevancy = AnswerRelevancy(
            llm=llm,
            embeddings=embeddings,
        )

    @staticmethod
    def _value(result: Any) -> float:
        value = getattr(result, "value", result)
        return max(0.0, min(1.0, float(value)))

    async def score(
        self,
        *,
        user_input: str,
        response: str,
        reference: str,
        retrieved_contexts: list[str],
        raw_contexts: list[str],
    ) -> RagasScores:
        recall = (
            self._value(
                await self._context_recall.ascore(
                    user_input=user_input,
                    retrieved_contexts=retrieved_contexts,
                    reference=reference,
                )
            )
            if retrieved_contexts
            else 0.0
        )
        raw_recall = (
            self._value(
                await self._context_recall.ascore(
                    user_input=user_input,
                    retrieved_contexts=raw_contexts,
                    reference=reference,
                )
            )
            if raw_contexts
            else 0.0
        )
        precision = (
            self._value(
                await self._context_precision.ascore(
                    user_input=user_input,
                    retrieved_contexts=retrieved_contexts,
                    reference=reference,
                )
            )
            if retrieved_contexts
            else 0.0
        )
        faithfulness = (
            self._value(
                await self._faithfulness.ascore(
                    user_input=user_input,
                    response=response,
                    retrieved_contexts=retrieved_contexts,
                )
            )
            if retrieved_contexts
            else 0.0
        )
        relevancy = self._value(
            await self._answer_relevancy.ascore(
                user_input=user_input,
                response=response,
            )
        )
        return RagasScores(
            context_recall=recall,
            context_precision=precision,
            faithfulness=faithfulness,
            answer_relevancy=relevancy,
            raw_context_recall=raw_recall,
        )
