from __future__ import annotations

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
