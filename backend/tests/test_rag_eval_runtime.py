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


class _TraceInput(SimpleNamespace):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)


class _TracePrompt:
    input_model = _TraceInput
    output_model = SimpleNamespace

    @staticmethod
    def to_string(input_data):
        return input_data


class _RecallLlm:
    async def agenerate(self, prompt, _output_model):
        fully_covered = "干扰内容" not in prompt.context
        return SimpleNamespace(
            classifications=[
                SimpleNamespace(
                    statement="事实一",
                    reason="召回内容包含事实一",
                    attributed=1,
                ),
                SimpleNamespace(
                    statement="事实二",
                    reason="召回内容包含事实二" if fully_covered else "未包含事实二",
                    attributed=1 if fully_covered else 0,
                ),
            ]
        )


class _RecallMetric:
    prompt = _TracePrompt()
    llm = _RecallLlm()


class _PrecisionLlm:
    async def agenerate(self, prompt, _output_model):
        useful = prompt.context == "有效内容"
        return SimpleNamespace(
            reason="可用于回答" if useful else "与问题无关",
            verdict=1 if useful else 0,
        )


class _PrecisionMetric:
    prompt = _TracePrompt()
    llm = _PrecisionLlm()

    @staticmethod
    def _calculate_average_precision(verdicts):
        denominator = sum(verdicts) + 1e-10
        numerator = sum(
            (sum(verdicts[: index + 1]) / (index + 1)) * verdict
            for index, verdict in enumerate(verdicts)
        )
        return numerator / denominator


class _DetailedFaithfulnessMetric:
    async def _create_statements(self, question, response):
        assert (question, response) == ("问题", "回复")
        return ["有依据的陈述", "没有依据的陈述"]

    async def _create_verdicts(self, statements, context):
        assert context == "有效内容\n干扰内容"
        return SimpleNamespace(
            statements=[
                SimpleNamespace(
                    statement=statements[0], reason="召回内容支持", verdict=1
                ),
                SimpleNamespace(
                    statement=statements[1], reason="召回内容未支持", verdict=0
                ),
            ]
        )

    @staticmethod
    def _compute_score(verdicts):
        return sum(item.verdict for item in verdicts.statements) / len(
            verdicts.statements
        )


class _RelevancyLlm:
    def __init__(self):
        self.calls = 0

    async def agenerate(self, _prompt, _output_model):
        self.calls += 1
        return SimpleNamespace(
            question=f"反向问题{self.calls}",
            noncommittal=0,
        )


class _TraceEmbeddings:
    async def aembed_text(self, text):
        assert text == "问题"
        return [1.0, 0.0]

    async def aembed_texts(self, texts):
        assert texts == ["反向问题1", "反向问题2", "反向问题3"]
        return [[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]]


class _DetailedAnswerMetric:
    prompt = _TracePrompt()
    llm = _RelevancyLlm()
    embeddings = _TraceEmbeddings()
    strictness = 3


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
    assert scores.context_recall_details == ()
    assert scores.faithfulness_details == ()


@pytest.mark.asyncio
async def test_ragas_score_preserves_all_intermediate_judgements_without_extra_calls():
    scorer = RagasScorer.__new__(RagasScorer)
    scorer._context_recall = _RecallMetric()
    scorer._context_precision = _PrecisionMetric()
    scorer._faithfulness = _DetailedFaithfulnessMetric()
    scorer._answer_relevancy = _DetailedAnswerMetric()

    scores = await scorer.score(
        user_input="问题",
        response="回复",
        reference="事实一；事实二",
        retrieved_contexts=["有效内容", "干扰内容"],
        raw_contexts=["有效内容"],
    )

    assert scores.context_recall == 0.5
    assert [item.verdict for item in scores.context_recall_details] == [1, 0]
    assert scores.raw_context_recall == 1.0
    assert [item.verdict for item in scores.raw_context_recall_details] == [1, 1]
    assert scores.context_precision == pytest.approx(1.0)
    assert [item.verdict for item in scores.context_precision_details] == [1, 0]
    assert scores.faithfulness == 0.5
    assert [item.verdict for item in scores.faithfulness_details] == [1, 0]
    assert scores.answer_relevancy == pytest.approx((1 + 2**-0.5) / 3)
    assert [item.generated_question for item in scores.answer_relevancy_details] == [
        "反向问题1",
        "反向问题2",
        "反向问题3",
    ]
    assert scores.answer_relevancy_all_noncommittal is False
    assert scorer._answer_relevancy.llm.calls == 3


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
        faithfulness_threshold=0.55,
    )

    assert "answer_relevancy" not in active
    assert active == {
        "context_recall": 0.80,
        "context_precision": 0.70,
        "faithfulness": 0.55,
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


def test_faithfulness_uses_threshold_selected_by_matching_calibration():
    active = _active_ragas_thresholds(
        answer_relevancy_enforced=False,
        faithfulness_enforced=True,
        faithfulness_threshold=0.60,
    )

    assert active["faithfulness"] == 0.60


def test_answer_relevancy_uses_threshold_selected_by_matching_calibration():
    active = _active_ragas_thresholds(
        answer_relevancy_enforced=True,
        answer_relevancy_threshold=0.45,
        faithfulness_enforced=False,
    )

    assert active["answer_relevancy"] == 0.45


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
