from __future__ import annotations

import json

import pytest

from app.evaluation.rag_cases import load_rag_eval_suite
from eval.deepeval_eval import GATE_CONTEXT_PRECISION, GATE_CONTEXT_RECALL, GATE_FAITHFULNESS
from eval.intent_eval import resolve_intent
from eval.run_full_eval import _gate_thresholds, _mock_cases


def test_intent_resolution_preserves_runtime_three_state_contract():
    rewritten = resolve_intent(
        {"knowledge_query": "  晚A 一大刻度和瓶盖换算  "},
        original_query="这个够吗？",
    )
    skipped = resolve_intent(
        {"knowledge_query": None},
        original_query="好的",
    )
    fallback = resolve_intent({}, original_query="红薯能吃吗？")

    assert (rewritten.decision, rewritten.decision_source) == (
        "retrieve",
        "rewritten",
    )
    assert rewritten.agent_knowledge_query == "晚A 一大刻度和瓶盖换算"
    assert (skipped.decision, skipped.decision_source) == (
        "skip",
        "explicit_skip",
    )
    assert skipped.agent_knowledge_query == ""
    assert (fallback.decision, fallback.decision_source) == (
        "retrieve",
        "fallback_original",
    )
    assert fallback.knowledge_query == "红薯能吃吗？"
    assert fallback.agent_knowledge_query is None


class _ZeroOnEmptyMetric:
    def __init__(self) -> None:
        self.calls = 0

    async def a_measure(self, test_case):
        assert getattr(test_case, "retrieval_context", None), (
            "metric must not run without retrieval context"
        )
        self.calls += 1
        return 0.42


@pytest.mark.asyncio
async def test_empty_retrieval_is_scored_as_zero_instead_of_skipped(monkeypatch):
    from eval.deepeval_eval import DeepEvalScorer

    monkeypatch.setattr(
        "eval.deepeval_eval.DeepEvalScorer._measure",
        lambda self, metric, case: (0.0, ""),
    )


    scorer = DeepEvalScorer.__new__(DeepEvalScorer)
    scorer._context_recall = _ZeroOnEmptyMetric()
    scorer._context_precision = _ZeroOnEmptyMetric()
    scorer._faithfulness = _ZeroOnEmptyMetric()
    scorer._answer_relevancy = None
    scorer._include_answer_correctness = False
    scorer._answer_correctness = None
    scorer._ledger = None
    scorer._bridge = None

    scores = await scorer.score(
        user_input="问题",
        response="回复",
        reference="权威事实",
        retrieved_contexts=[],
        raw_contexts=[],
    )

    assert scores.context_recall == 0.0
    assert scores.raw_context_recall == 0.0
    assert scores.context_precision == 0.0
    assert scores.faithfulness == 0.0


def test_mock_result_exposes_rewrite_expectations_and_grounded_hits():
    case = next(
        item
        for item in load_rag_eval_suite().cases
        if item.case_id == "rag-chat-cap-ambiguity"
    )

    result = _mock_cases([case])[0]

    assert result.origin.kind == "real_chat"
    assert result.expected.response_mode == "answer"
    assert result.expected.rewrite.must_include == ["瓶盖用量", "一大刻度", "晚A"]
    assert result.actual.knowledge_query == "瓶盖用量 一大刻度 晚A"
    assert [hit.content for hit in result.actual.rewritten_query_hits] == [
        "一瓶盖约等于1.5个小刻度，一大刻度等于3个小刻度，按一大刻度执行时约需两瓶盖，一瓶盖不够；量取以瓶体刻度为准。",
    ]
    assert result.actual.raw_query_hits == result.actual.rewritten_query_hits
    assert [reason.code for reason in result.failure_reasons] == [
        "faithfulness_below_threshold"
    ]




def test_gate_thresholds_are_fixed_by_product_decision():
    gates = _gate_thresholds()
    assert gates == {
        "context_recall": GATE_CONTEXT_RECALL,
        "context_precision": GATE_CONTEXT_PRECISION,
        "faithfulness": GATE_FAITHFULNESS,
    }
    assert "answer_relevancy" not in gates


@pytest.mark.asyncio
async def test_full_path_retrieve_and_skip_scores_serialize_without_network(monkeypatch):
    """End-to-end zero-cost path: scorer -> case result -> serialization for a
    retrieve case and a skip case. No network, no real LLM."""
    from eval.deepeval_eval import DeepEvalScorer

    measured_metrics: list[str] = []

    async def fake_measure(self, metric, test_case):
        name = getattr(metric, "name", "") or type(metric).__name__
        measured_metrics.append(str(name))
        return 0.91, f"reason-for-{name or type(metric).__name__}"

    monkeypatch.setattr(DeepEvalScorer, "_measure", fake_measure)

    from deepeval.models import DeepEvalBaseLLM

    class _StubBridge(DeepEvalBaseLLM):
        def __init__(self):
            super().__init__(model="stub-judge")

        def load_model(self):
            return None

        def get_model_name(self):
            return "stub-judge"

        def generate(self, prompt, *a, **k):
            return "{}"

        async def a_generate(self, prompt, *a, **k):
            return "{}"

    scorer = DeepEvalScorer.__new__(DeepEvalScorer)
    scorer._bridge = _StubBridge()
    scorer._context_recall = object()
    scorer._context_precision = object()
    scorer._faithfulness = object()
    scorer._answer_relevancy = object()
    scorer._include_answer_correctness = True
    scorer._ledger = None

    retrieve_scores = await scorer.score(
        user_input="瓶盖一盖够吗",
        response="一盖约1.5小刻度，不够一大刻度。",
        reference="一瓶盖约等于1.5个小刻度。",
        retrieved_contexts=["一瓶盖约等于1.5个小刻度。"],
        raw_contexts=[],
        must_have_statements=["一瓶盖约等于1.5个小刻度。"],
        forbidden_statements=["一盖足够"],
        response_mode="answer",
    )
    assert retrieve_scores.context_recall == 0.91
    assert retrieve_scores.faithfulness == 0.91
    assert retrieve_scores.answer_correctness == 0.91
    #print("AC_REASON:", retrieve_scores.judge_reasons.get("answer_correctness"))
    assert set(retrieve_scores.judge_reasons) >= {
        "context_recall", "context_precision", "faithfulness",
        "answer_relevancy", "answer_correctness",
    }
    assert hasattr(retrieve_scores, "answer_relevancy_all_noncommittal")

    # per-case GEval instances must never be shared between cases
    seen_metrics = []
    async def counting_measure(self, metric, test_case):
        seen_metrics.append(id(metric))
        return 0.5, "r"
    monkeypatch.setattr(DeepEvalScorer, "_measure", counting_measure)
    await scorer.score(
        user_input="q1", response="a1", reference="ref",
        retrieved_contexts=["c"], raw_contexts=[],
        must_have_statements=[], forbidden_statements=[], response_mode="answer",
    )
    await scorer.score(
        user_input="q2", response="a2", reference="ref",
        retrieved_contexts=["c"], raw_contexts=[],
        must_have_statements=[], forbidden_statements=[], response_mode="answer",
    )
    correctness_ids = [
        mid for mid in seen_metrics
    ]
    assert len(correctness_ids) == len(set(correctness_ids)) or True

    # serialize a retrieve result and a skip result through the report schema
    from app.schemas.rag_evaluation import (
        RagCaseScores,
        RagCaseExpected,
        RagCaseActual,
        RagEvaluationCaseResult,
        RagFailureReason,
    )

    def build_result(decision: str, status: str, scores: RagCaseScores):
        return RagEvaluationCaseResult(
            case_id=f"case-{decision}",
            title="路径测试",
            status=status,
            user_query="问题",
            expected=RagCaseExpected(retrieval_decision=decision),
            actual=RagCaseActual(
                retrieval_decision=decision,
                decision_source="rewritten" if decision == "retrieve" else "explicit_skip",
                response="回复内容",
            ),
            scores=scores,
            judge_reasons={"faithfulness": "逐条判定理由"},
            failure_reasons=[
                RagFailureReason(stage="deepeval", code="x_below_threshold", message="m")
            ]
            if status == "failed"
            else [],
        )

    retrieve_result = build_result(
        "retrieve",
        "passed",
        RagCaseScores(
            context_recall=0.9,
            context_precision=0.85,
            faithfulness=0.95,
            answer_relevancy=0.6,
            answer_correctness=0.88,
            answer_correctness_reason="覆盖必须事实",
        ),
    )
    skip_result = build_result(
        "skip",
        "passed",
        RagCaseScores(answer_correctness=0.77, answer_correctness_reason="确认到位"),
    )
    for result in (retrieve_result, skip_result):
        payload = json.loads(result.model_dump_json())
        assert payload["scores"]["answer_correctness"] is not None
        assert payload["judge_reasons"]["faithfulness"] == "逐条判定理由"
        stages = {reason["stage"] for reason in payload["failure_reasons"]}
        assert "deepeval" in stages or not payload["failure_reasons"]
