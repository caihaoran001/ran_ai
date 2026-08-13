from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI

from app.core.config import Settings


DEFAULT_RAG_EVAL_JUDGE_MAX_TOKENS = 50_000


def ragas_judge_max_tokens() -> int:
    raw_value = os.getenv(
        "RAG_EVAL_JUDGE_MAX_TOKENS",
        str(DEFAULT_RAG_EVAL_JUDGE_MAX_TOKENS),
    ).strip()
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise RuntimeError(
            "RAG_EVAL_JUDGE_MAX_TOKENS must be a positive integer"
        ) from exc
    if value <= 0:
        raise RuntimeError("RAG_EVAL_JUDGE_MAX_TOKENS must be a positive integer")
    return value


def _create_ragas_llm(factory: Any, *, model: str, client: Any) -> Any:
    return factory(
        model,
        client=client,
        max_tokens=ragas_judge_max_tokens(),
    )


@dataclass(frozen=True)
class BinaryJudgement:
    statement: str
    reason: str
    verdict: int


@dataclass(frozen=True)
class ContextPrecisionJudgement:
    rank: int
    reason: str
    verdict: int


@dataclass(frozen=True)
class AnswerRelevancyJudgement:
    generated_question: str
    noncommittal: int
    similarity: float


@dataclass(frozen=True)
class RagasScores:
    context_recall: float
    context_precision: float
    faithfulness: float
    answer_relevancy: float
    raw_context_recall: float
    context_recall_details: tuple[BinaryJudgement, ...] = ()
    raw_context_recall_details: tuple[BinaryJudgement, ...] = ()
    context_precision_details: tuple[ContextPrecisionJudgement, ...] = ()
    faithfulness_details: tuple[BinaryJudgement, ...] = ()
    answer_relevancy_details: tuple[AnswerRelevancyJudgement, ...] = ()
    answer_relevancy_all_noncommittal: bool | None = None


class RagasScorer:
    """Thin adapter around the non-deprecated RAGAS 0.4 collections API."""

    def __init__(
        self,
        settings: Settings,
        *,
        include_answer_relevancy: bool = True,
    ):
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
        llm = _create_ragas_llm(
            llm_factory,
            model=settings.ai_model,
            client=judge_client,
        )
        self._context_recall = ContextRecall(llm=llm)
        self._context_precision = ContextPrecision(llm=llm)
        self._faithfulness = Faithfulness(llm=llm)
        self._answer_relevancy = None
        if include_answer_relevancy:
            from ragas.embeddings import embedding_factory

            embedding_client = AsyncOpenAI(
                api_key=settings.embedding_api_key,
                base_url=settings.embedding_base_url,
                timeout=settings.ai_timeout_seconds,
            )
            embeddings = embedding_factory(
                "openai",
                model=settings.embedding_model,
                client=embedding_client,
            )
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
        recall, recall_details = await self.score_context_recall_with_details(
            user_input=user_input,
            retrieved_contexts=retrieved_contexts,
            reference=reference,
        )
        raw_recall, raw_recall_details = await self.score_context_recall_with_details(
            user_input=user_input,
            retrieved_contexts=raw_contexts,
            reference=reference,
        )
        precision, precision_details = await self.score_context_precision_with_details(
            user_input=user_input,
            retrieved_contexts=retrieved_contexts,
            reference=reference,
        )
        faithfulness, faithfulness_details = await self.score_faithfulness_with_details(
            user_input=user_input,
            response=response,
            retrieved_contexts=retrieved_contexts,
        )
        (
            relevancy,
            relevancy_details,
            all_noncommittal,
        ) = await self.score_answer_relevancy_with_details(
            user_input=user_input,
            response=response,
        )
        return RagasScores(
            context_recall=recall,
            context_precision=precision,
            faithfulness=faithfulness,
            answer_relevancy=relevancy,
            raw_context_recall=raw_recall,
            context_recall_details=tuple(recall_details),
            raw_context_recall_details=tuple(raw_recall_details),
            context_precision_details=tuple(precision_details),
            faithfulness_details=tuple(faithfulness_details),
            answer_relevancy_details=tuple(relevancy_details),
            answer_relevancy_all_noncommittal=all_noncommittal,
        )

    async def score_context_recall_with_details(
        self,
        *,
        user_input: str,
        retrieved_contexts: list[str],
        reference: str,
    ) -> tuple[float, list[BinaryJudgement]]:
        if not retrieved_contexts:
            return 0.0, []
        metric = self._context_recall
        if not all(hasattr(metric, name) for name in ("llm", "prompt")):
            result = await metric.ascore(
                user_input=user_input,
                retrieved_contexts=retrieved_contexts,
                reference=reference,
            )
            return self._value(result), []

        prompt = metric.prompt.to_string(
            metric.prompt.input_model(
                question=user_input,
                context="\n".join(retrieved_contexts),
                answer=reference,
            )
        )
        result = await metric.llm.agenerate(prompt, metric.prompt.output_model)
        details = [
            BinaryJudgement(
                statement=item.statement,
                reason=item.reason,
                verdict=int(item.attributed),
            )
            for item in result.classifications
        ]
        score = sum(item.verdict for item in details) / len(details) if details else 0.0
        return self._value(score), details

    async def score_context_precision_with_details(
        self,
        *,
        user_input: str,
        retrieved_contexts: list[str],
        reference: str,
    ) -> tuple[float, list[ContextPrecisionJudgement]]:
        if not retrieved_contexts:
            return 0.0, []
        metric = self._context_precision
        if not all(
            hasattr(metric, name)
            for name in ("llm", "prompt", "_calculate_average_precision")
        ):
            result = await metric.ascore(
                user_input=user_input,
                retrieved_contexts=retrieved_contexts,
                reference=reference,
            )
            return self._value(result), []

        details = []
        for rank, context in enumerate(retrieved_contexts, start=1):
            prompt = metric.prompt.to_string(
                metric.prompt.input_model(
                    question=user_input,
                    context=context,
                    answer=reference,
                )
            )
            result = await metric.llm.agenerate(prompt, metric.prompt.output_model)
            details.append(
                ContextPrecisionJudgement(
                    rank=rank,
                    reason=result.reason,
                    verdict=int(result.verdict),
                )
            )
        score = metric._calculate_average_precision([item.verdict for item in details])
        return self._value(score), details

    async def score_faithfulness_with_details(
        self,
        *,
        user_input: str,
        response: str,
        retrieved_contexts: list[str],
    ) -> tuple[float, list[BinaryJudgement]]:
        if not retrieved_contexts:
            return 0.0, []
        metric = self._faithfulness
        if not all(
            hasattr(metric, name)
            for name in ("_create_statements", "_create_verdicts", "_compute_score")
        ):
            result = await metric.ascore(
                user_input=user_input,
                response=response,
                retrieved_contexts=retrieved_contexts,
            )
            return self._value(result), []

        statements = await metric._create_statements(user_input, response)
        if not statements:
            return 0.0, []
        verdicts = await metric._create_verdicts(
            statements,
            "\n".join(retrieved_contexts),
        )
        details = [
            BinaryJudgement(
                statement=item.statement,
                reason=item.reason,
                verdict=int(item.verdict),
            )
            for item in verdicts.statements
        ]
        return self._value(metric._compute_score(verdicts)), details

    async def score_answer_relevancy_with_details(
        self,
        *,
        user_input: str,
        response: str,
    ) -> tuple[float, list[AnswerRelevancyJudgement], bool | None]:
        if self._answer_relevancy is None:
            raise RuntimeError("answer relevancy scorer is not initialized")
        metric = self._answer_relevancy
        if not all(
            hasattr(metric, name)
            for name in ("llm", "prompt", "embeddings", "strictness")
        ):
            result = await metric.ascore(user_input=user_input, response=response)
            return self._value(result), [], None

        import numpy as np

        generated_questions = []
        noncommittal_flags = []
        for _ in range(metric.strictness):
            prompt = metric.prompt.to_string(
                metric.prompt.input_model(response=response)
            )
            result = await metric.llm.agenerate(prompt, metric.prompt.output_model)
            if result.question:
                generated_questions.append(result.question)
                noncommittal_flags.append(int(result.noncommittal))
        if not generated_questions:
            return 0.0, [], None

        question_vector = np.asarray(
            await metric.embeddings.aembed_text(user_input)
        ).reshape(1, -1)
        generated_vectors = np.asarray(
            await metric.embeddings.aembed_texts(generated_questions)
        ).reshape(len(generated_questions), -1)
        norm = np.linalg.norm(generated_vectors, axis=1) * np.linalg.norm(
            question_vector,
            axis=1,
        )
        similarities = np.dot(generated_vectors, question_vector.T).reshape(-1) / norm
        all_noncommittal = bool(np.all(noncommittal_flags))
        details = [
            AnswerRelevancyJudgement(
                generated_question=question,
                noncommittal=flag,
                similarity=float(similarity),
            )
            for question, flag, similarity in zip(
                generated_questions,
                noncommittal_flags,
                similarities,
                strict=True,
            )
        ]
        score = float(similarities.mean()) * int(not all_noncommittal)
        return self._value(score), details, all_noncommittal

    async def score_faithfulness(
        self,
        *,
        user_input: str,
        response: str,
        retrieved_contexts: list[str],
    ) -> float:
        score, _ = await self.score_faithfulness_with_details(
            user_input=user_input,
            response=response,
            retrieved_contexts=retrieved_contexts,
        )
        return score

    async def score_answer_relevancy(
        self,
        *,
        user_input: str,
        response: str,
    ) -> float:
        score, _, _ = await self.score_answer_relevancy_with_details(
            user_input=user_input,
            response=response,
        )
        return score
