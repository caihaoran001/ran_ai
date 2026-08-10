from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.evaluation.rag_cases import load_rag_eval_suite
from eval.intent_eval import resolve_intent
from eval.ragas_eval import RagasScorer
from eval.run_full_eval import _mock_cases


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


class _FactJudgeCompletions:
    async def create(self, **kwargs):
        assert kwargs["temperature"] == 0
        assert "数字、单位、大小比较" in kwargs["messages"][0]["content"]
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content='{"consistent": false, "reason": "把一瓶盖和一大刻度的大小关系说反了"}'
                    )
                )
            ]
        )


@pytest.mark.asyncio
async def test_fact_consistency_judge_detects_relation_contradiction():
    scorer = RagasScorer.__new__(RagasScorer)
    scorer._judge_model = "judge-model"
    scorer._judge_client = SimpleNamespace(
        chat=SimpleNamespace(completions=_FactJudgeCompletions())
    )

    result = await scorer.check_fact_consistency(
        reference="一瓶盖约1.5小刻度，一大刻度等于3小刻度。",
        response="一瓶盖比一大刻度更多。",
    )

    assert result.consistent is False
    assert "大小关系说反" in result.reason
