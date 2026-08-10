from __future__ import annotations

import json
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


@dataclass(frozen=True)
class FactConsistencyResult:
    consistent: bool
    reason: str


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
        self._judge_client = judge_client
        self._judge_model = settings.ai_model

    @staticmethod
    def _value(result: Any) -> float:
        value = getattr(result, "value", result)
        return max(0.0, min(1.0, float(value)))

    async def check_fact_consistency(
        self,
        *,
        response: str,
        reference: str,
    ) -> FactConsistencyResult:
        """Detect direct relation contradictions without enumerating bad phrases."""

        completion = await self._judge_client.chat.completions.create(
            model=self._judge_model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是离线评测判官。只判断回复是否直接反转或冲突于权威事实中的"
                        "数字、单位、大小比较、等量关系、时间或否定关系。遗漏事实、措辞不同、"
                        "额外但不冲突的建议，都不算冲突。输出JSON："
                        '{"consistent":true或false,"reason":"一句具体原因"}。'
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"权威事实：\n{reference[:12000]}\n\n"
                        f"待检查回复：\n{response[:12000]}"
                    ),
                },
            ],
        )
        content = completion.choices[0].message.content or ""
        payload = json.loads(content)
        consistent = payload.get("consistent")
        reason = payload.get("reason")
        if not isinstance(consistent, bool) or not isinstance(reason, str):
            raise ValueError("关键事实一致性判官返回格式无效")
        return FactConsistencyResult(
            consistent=consistent,
            reason=reason.strip()[:1000] or "判官未提供原因",
        )

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
