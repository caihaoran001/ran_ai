from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.evaluation.rag_cases import load_rag_eval_suite
from eval.intent_eval import resolve_intent
from eval.ragas_eval import RagasScorer, _create_ragas_llm, ragas_judge_max_tokens
from eval.run_full_eval import _active_ragas_thresholds, _mock_cases


def test_ragas_judge_uses_large_configurable_output_limit(monkeypatch):
    monkeypatch.delenv("RAG_EVAL_JUDGE_MAX_TOKENS", raising=False)
    assert ragas_judge_max_tokens() == 50_000

    monkeypatch.setenv("RAG_EVAL_JUDGE_MAX_TOKENS", "60000")
    assert ragas_judge_max_tokens() == 60_000


def test_ragas_llm_factory_receives_configured_output_limit(monkeypatch):
    monkeypatch.setenv("RAG_EVAL_JUDGE_MAX_TOKENS", "50000")
    captured: dict[str, object] = {}
    judge_client = object()

    def factory(model: str, **kwargs: object) -> object:
        captured["model"] = model
        captured.update(kwargs)
        return object()

    _create_ragas_llm(factory, model="judge-model", client=judge_client)

    assert captured == {
        "model": "judge-model",
        "client": judge_client,
        "max_tokens": 50_000,
    }


@pytest.mark.parametrize("value", ["", "0", "-1", "not-a-number"])
def test_ragas_judge_rejects_invalid_output_limit(monkeypatch, value):
    monkeypatch.setenv("RAG_EVAL_JUDGE_MAX_TOKENS", value)

    with pytest.raises(RuntimeError, match="must be a positive integer"):
        ragas_judge_max_tokens()


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


class _UnexpectedMetric:
    async def ascore(self, **_kwargs):
        raise AssertionError("empty contexts must not be sent to this RAGAS metric")


class _AnswerMetric:
    async def ascore(self, **kwargs):
        assert kwargs == {"user_input": "问题", "response": "回复"}
        return SimpleNamespace(value=0.75)


@pytest.mark.asyncio
async def test_empty_retrieval_is_scored_as_zero_instead_of_skipped():
    scorer = RagasScorer.__new__(RagasScorer)
    scorer._context_recall = _UnexpectedMetric()
    scorer._context_precision = _UnexpectedMetric()
    scorer._faithfulness = _UnexpectedMetric()
    scorer._answer_relevancy = _AnswerMetric()

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
    assert scores.answer_relevancy == 0.75


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


def test_uncalibrated_answer_relevancy_is_scored_but_not_thresholded():
    active = _active_ragas_thresholds(
        answer_relevancy_enforced=False,
        faithfulness_enforced=True,
    )

    assert "answer_relevancy" not in active
    assert active == {
        "context_recall": 0.80,
        "context_precision": 0.70,
        "faithfulness": 0.85,
    }


def test_uncalibrated_faithfulness_is_scored_but_not_thresholded():
    active = _active_ragas_thresholds(
        answer_relevancy_enforced=False,
        faithfulness_enforced=False,
    )

    assert active == {
        "context_recall": 0.80,
        "context_precision": 0.70,
    }


class _FaithfulnessMetric:
    async def ascore(self, **kwargs):
        assert kwargs == {
            "user_input": "问题",
            "response": "忠实回复",
            "retrieved_contexts": ["权威事实"],
        }
        return SimpleNamespace(value=0.92)


@pytest.mark.asyncio
async def test_faithfulness_calibration_uses_same_metric_adapter_as_full_eval():
    scorer = RagasScorer.__new__(RagasScorer)
    scorer._faithfulness = _FaithfulnessMetric()

    score = await scorer.score_faithfulness(
        user_input="问题",
        response="忠实回复",
        retrieved_contexts=["权威事实"],
    )

    assert score == 0.92
