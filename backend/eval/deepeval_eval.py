"""DeepEval-backed scorer for the formal RAG evaluation entrypoint.

Drop-in replacement for legacy scorer: same public surface (score plus
per-metric *_with_details variants), same judgement dataclasses, so the
report schema and the historical-report reader stay untouched.

Gate thresholds are fixed by product decision:
  context_recall >= 0.80, context_precision >= 0.70, faithfulness >= 0.8333.
Answer relevancy stays diagnostic-only; answer correctness/completeness is a
custom semantic metric that also stays observational until calibrated.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Any

if __package__:
    from .rag_costs import instrument_openai_client
else:
    from rag_costs import instrument_openai_client


DEEPEVAL_VERSION = "4.1.5"
GATE_CONTEXT_RECALL = 0.80
GATE_CONTEXT_PRECISION = 0.70
GATE_FAITHFULNESS = 0.8333

ANSWER_CORRECTNESS_NAME = "回答正确与完整度"
_ANSWER_CORRECTNESS_CRITERIA = (
    "判断回复是否正确且完整地回答了用户的问题。评价依据："
    "1) 必须包含的关键事实是否都出现且数值、单位、条件方向正确；"
    "2) 是否出现与权威事实冲突的错误说法（数字错误、单位错误、"
    "否定反转、条件遗漏、无依据建议均算错）；"
    "3) 是否遗漏了回答该问题必需的信息；"
    "4) 回复方式是否符合预期（该澄清时澄清、该确认时确认、该给执行方法时给出可执行步骤）。"
    "严禁因为措辞不同而扣分，只看事实与完整性。"
)


def deepeval_judge_max_tokens() -> int:
    raw_value = os.getenv(
        "DEEPEVAL_JUDGE_MAX_TOKENS",
        os.getenv("RAG_EVAL_JUDGE_MAX_TOKENS", "8000"),
    ).strip()
    value = int(raw_value)
    if value <= 0:
        raise RuntimeError("judge max tokens must be a positive integer")
    return value


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
class SemanticJudgement:
    score: float
    reason: str


@dataclass(frozen=True)
class RagScores:
    context_recall: float
    context_precision: float
    faithfulness: float
    answer_relevancy: float
    raw_context_recall: float
    answer_correctness: float | None = None
    answer_correctness_reason: str | None = None
    details: dict[str, list[BinaryJudgement]] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    def judgements(self, name: str) -> list[BinaryJudgement]:
        return self.details.get(name, [])


class _JudgeModel:
    """Ledgered OpenAI-compatible chat client feeding DeepEval prompts."""

    def __init__(self, settings, ledger=None, semaphore=None):
        from openai import AsyncOpenAI

        self._settings = settings
        self._semaphore = semaphore or asyncio.Semaphore(8)
        self.model_name = settings.ai_model
        self._client = AsyncOpenAI(
            api_key=settings.ai_api_key,
            base_url=settings.ai_base_url,
            timeout=settings.ai_timeout_seconds,
        )
        if ledger is not None:
            instrument_openai_client(
                self._client, ledger, chat_component="judge"
            )

    async def complete(self, prompt: str) -> str:
        started = time.monotonic()
        async with self._semaphore:
            response = await self._client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": "你是严格的中文评测判官。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
                max_tokens=deepeval_judge_max_tokens(),
                response_format={"type": "json_object"},
            )
        latency_ms = int((time.monotonic() - started) * 1000)
        del latency_ms
        return response.choices[0].message.content or ""


def _parse_json_dict(raw: str) -> dict[str, Any]:
    text = raw.strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        text = match.group(0)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"judge emitted invalid JSON: {exc}") from exc
    return parsed if isinstance(parsed, dict) else {}


class _DeepEvalJudgeBridge:
    """Adapter exposing generate()/a_generate()/get_model_name() so DeepEval
    metrics can drive our ledgered judge client."""

    def __init__(self, judge: _JudgeModel):
        self._judge = judge
        self.model_name = judge.model_name

    def load_model(self):
        return self._judge

    def get_model_name(self) -> str:
        return self.judge.model_name

    async def a_generate(self, prompt: str, *args, schema=None, **kwargs):
        raw = await self._judge.complete(prompt)
        if schema is None:
            return raw
        from deepeval.metrics.utils import trimAndLoadJson

        return trimAndLoadJson(raw, self)

    def generate(self, prompt: str, *args, schema=None, **kwargs):
        raise RuntimeError("sync generation is not used; metrics run async")


class DeepEvalScorer:
    """Formal RAG scorer on DeepEval native metrics with fixed gates."""

    def __init__(
        self,
        settings,
        *,
        include_answer_relevancy: bool = True,
        include_answer_correctness: bool = True,
        cost_ledger: Any | None = None,
        max_concurrency: int = 8,
    ):
        import asyncio

        from deepeval.metrics import (
            AnswerRelevancyMetric,
            ContextualPrecisionMetric,
            ContextualRecallMetric,
            FaithfulnessMetric,
        )

        self._ledger = cost_ledger
        judge = _JudgeModel(settings, cost_ledger, asyncio.Semaphore(max(1, max_concurrency)))
        bridge = _DeepEvalJudgeBridge(judge)
        common = {"model": bridge, "include_reason": True, "async_mode": True}
        self._context_recall = ContextualRecallMetric(threshold=GATE_CONTEXT_RECALL, **common)
        self._context_precision = ContextualPrecisionMetric(
            threshold=GATE_CONTEXT_PRECISION, **common
        )
        self._faithfulness = FaithfulnessMetric(threshold=GATE_FAITHFULNESS, **common)
        self._answer_relevancy = None
        if include_answer_relevancy:
            self._answer_relevancy = AnswerRelevancyMetric(threshold=0.80, **common)
        self._answer_correctness = None
        if include_answer_correctness:
            from deepeval.metrics import GEval
            from deepeval.test_case import LLMTestCaseParams

            self._answer_correctness = GEval(
                name=ANSWER_CORRECTNESS_NAME,
                criteria=_ANSWER_CORRECTNESS_CRITERIA,
                evaluation_params=[
                    LLMTestCaseParams.INPUT,
                    LLMTestCaseParams.ACTUAL_OUTPUT,
                    LLMTestCaseParams.EXPECTED_OUTPUT,
                ],
                threshold=0.80,
                model=bridge,
                async_mode=True,
            )
        self._bridge = bridge

    def _cost_stage(self, stage_key: str):
        ledger = self._ledger
        return ledger.stage(stage_key) if ledger is not None else nullcontext()

    @staticmethod
    def _value(result: Any) -> float:
        value = getattr(result, "score", result)
        return max(0.0, min(1.0, float(value)))

    @staticmethod
    def _judgement_items(metric: Any, test_case: Any) -> list[BinaryJudgement]:
        """Best-effort extraction of per-statement verdicts from DeepEval."""
        verbose = (getattr(metric, "verbose_logs", "") or "")
        items: list[BinaryJudgement] = []
        for line in verbose.splitlines():
            line = line.strip()
            if not line:
                continue
            verdict = None
            for marker in ("[yes]", "[Yes]", "支持", "支持判定"):
                if marker in line.lower() or marker in line:
                    verdict = 1
                    break
            if verdict is None and any(m in line for m in ("[no]", "[No]", "不支持")):
                verdict = 0
            if verdict is not None and len(line) > 6:
                items.append(BinaryJudgement(statement=line[:400], reason=line[:400], verdict=verdict))
        return items[:50]

    async def _measure(self, metric: Any, test_case: Any) -> tuple[float, str]:
        await metric.a_measure(test_case)
        reason = (getattr(metric, "reason", "") or "").strip()
        return self._value(getattr(metric, "score")), reason

    @staticmethod
    def _case(user_input: str, actual_output: str, retrieval_context=None, expected_output=None):
        from deepeval.test_case import LLMTestCase

        kwargs: dict[str, Any] = {"input": user_input, "actual_output": actual_output}
        if retrieval_context:
            kwargs["retrieval_context"] = retrieval_context
        if expected_output:
            kwargs["expected_output"] = expected_output
        return LLMTestCase(**kwargs)

    async def score_context_recall_with_details(
        self, *, user_input, retrieved_contexts, reference
    ) -> tuple[float, list[BinaryJudgement]]:
        if not retrieved_contexts:
            return 0.0, []
        score, _ = await self._measure(
            self._context_recall,
            self._case(user_input, reference, retrieval_context=retrieved_contexts),
        )
        return score, []

    async def score_context_precision_with_details(
        self, *, user_input, retrieved_contexts, reference
    ) -> tuple[float, list[ContextPrecisionJudgement]]:
        if not retrieved_contexts:
            return 0.0, []
        score, _ = await self._measure(
            self._context_precision,
            self._case(user_input, reference, retrieval_context=retrieved_contexts),
        )
        return score, []

    async def score_faithfulness_with_details(
        self, *, user_input, response, retrieved_contexts
    ) -> tuple[float, list[BinaryJudgement]]:
        if not retrieved_contexts:
            return 0.0, []
        score, _ = await self._measure(
            self._faithfulness,
            self._case(user_input, response, retrieval_context=retrieved_contexts),
        )
        return score, []

    async def score_answer_relevancy_with_details(
        self, *, user_input, response
    ) -> tuple[float, list[Any], bool | None]:
        if self._answer_relevancy is None:
            raise RuntimeError("answer relevancy scorer is not initialized")
        score, _ = await self._measure(
            self._answer_relevancy, self._case(user_input, response)
        )
        return score, [], None

    async def score_answer_correctness_with_details(
        self,
        *,
        user_input: str,
        response: str,
        reference: str,
        must_have_statements: list[str],
        forbidden_statements: list[str],
        response_mode: str,
    ) -> tuple[float, str]:
        if self._answer_correctness is None:
            raise RuntimeError("answer correctness scorer is not initialized")
        extra = (
            f"\n\n【必须覆盖的事实】\n{chr(10).join(must_have_statements) or '（无）'}\n"
            f"【禁止出现的说法】\n{chr(10).join(forbidden_statements) or '（无）'}\n"
            f"【预期回复方式】{response_mode}\n【参考答案】\n{reference}"
        )
        test_case = self._case(user_input, response + extra, expected_output=reference)
        score, reason = await self._measure(self._answer_correctness, test_case)
        return score, reason

    async def score(
        self,
        *,
        user_input: str,
        response: str,
        reference: str,
        retrieved_contexts: list[str],
        raw_contexts: list[str],
        must_have_statements: list[str] | None = None,
        forbidden_statements: list[str] | None = None,
        response_mode: str = "answer",
    ) -> RagScores:
        recall, _ = await self.score_context_recall_with_details(
            user_input=user_input,
            retrieved_contexts=retrieved_contexts,
            reference=reference,
        )
        raw_recall, _ = await self.score_context_recall_with_details(
            user_input=user_input,
            retrieved_contexts=raw_contexts,
            reference=reference,
        )
        precision, _ = await self.score_context_precision_with_details(
            user_input=user_input,
            retrieved_contexts=retrieved_contexts,
            reference=reference,
        )
        faithfulness, _ = await self.score_faithfulness_with_details(
            user_input=user_input,
            response=response,
            retrieved_contexts=retrieved_contexts,
        )
        if self._answer_relevancy is None:
            relevancy, _, all_noncommittal = 0.0, [], None
        else:
            relevancy, _, all_noncommittal = await self.score_answer_relevancy_with_details(
                user_input=user_input,
                response=response,
            )
        correctness_score: float | None = None
        correctness_reason: str | None = None
        if response:
            try:
                correctness_score, correctness_reason = (
                    await self.score_answer_correctness_with_details(
                        user_input=user_input,
                        response=response,
                        reference=reference,
                        must_have_statements=must_have_statements or [],
                        forbidden_statements=forbidden_statements or [],
                        response_mode=response_mode,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - observational metric
                correctness_reason = f"{type(exc).__name__}: {exc}"[:300]
        return RagScores(
            context_recall=recall,
            context_precision=precision,
            faithfulness=faithfulness,
            answer_relevancy=relevancy,
            raw_context_recall=raw_recall,
            answer_correctness=correctness_score,
            answer_correctness_reason=correctness_reason,
        )

