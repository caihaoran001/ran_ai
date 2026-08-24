from __future__ import annotations

import asyncio
import json
import sys
from types import SimpleNamespace

import pytest

from app.evaluation.rag_cases import load_rag_eval_suite
from eval.deepeval_eval import (
    GATE_CONTEXT_PRECISION,
    GATE_CONTEXT_RECALL,
    GATE_FAITHFULNESS,
)
from eval.intent_eval import resolve_intent
from eval.run_full_eval import (
    _answer_fact_checks,
    _gate_thresholds,
    _load_faithfulness_policy,
    _mock_cases,
)


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
    assert result.scores.raw_context_recall == result.scores.context_recall
    assert [reason.code for reason in result.failure_reasons] == [
        "faithfulness_below_threshold"
    ]


def test_noon_boundary_uses_current_atomic_faq_truth():
    from eval.run_full_eval import _reference, _retrieval_reference

    case = next(
        item
        for item in load_rag_eval_suite().cases
        if item.case_id == "rag-faq-noon-boundary"
    )

    answer_reference = _reference(case)
    retrieval_reference = _retrieval_reference(case)

    assert answer_reference.count("\n") == 0
    assert retrieval_reference.splitlines() == [
        "- 午B超过下午2点当天跳过，不要补喝",
        "- 晚A照常按原计划进行",
    ]
    assert "超过下午2点当天跳过" in answer_reference
    assert case.origin.reference_id == "faq_2b69430ec351e003"
    assert (
        case.authoritative_facts[0].provenance
        == "knowledge_faqs:faq_2b69430ec351e003:v2"
    )


def test_gate_thresholds_are_fixed_by_product_decision():
    gates = _gate_thresholds()
    assert gates == {
        "context_recall": GATE_CONTEXT_RECALL,
        "context_precision": GATE_CONTEXT_PRECISION,
        "faithfulness": GATE_FAITHFULNESS,
    }
    assert "answer_relevancy" not in gates


@pytest.mark.asyncio
async def test_deepeval_judge_bridge_returns_requested_schema_instance():
    from deepeval.metrics import ContextualRecallMetric
    from deepeval.metrics.contextual_recall.schema import Verdicts
    from deepeval.test_case import LLMTestCase
    from eval.deepeval_eval import _DeepEvalJudgeBridge

    class _Judge:
        model_name = "offline-judge"

        async def complete(self, prompt: str) -> str:
            if '"verdicts"' in prompt:
                return '{"verdicts":[{"verdict":"yes","reason":"supported"}]}'
            return '{"reason":"supported"}'

    bridge = _DeepEvalJudgeBridge(_Judge())

    result = await bridge.a_generate('return {"verdicts": []}', schema=Verdicts)

    assert isinstance(result, Verdicts)
    assert result.verdicts[0].verdict == "yes"
    assert result.verdicts[0].reason == "supported"

    metric = ContextualRecallMetric(
        model=bridge,
        include_reason=True,
        async_mode=True,
    )
    await metric.a_measure(
        LLMTestCase(
            input="用户问题",
            actual_output="实际回复",
            expected_output="必须事实",
            retrieval_context=["必须事实"],
        )
    )
    assert metric.score == 1.0
    assert metric.reason == "supported"


@pytest.mark.asyncio
async def test_deepeval_judge_bridge_retries_one_invalid_json_response():
    from deepeval.metrics.contextual_recall.schema import Verdicts
    from eval.deepeval_eval import _DeepEvalJudgeBridge

    class _Judge:
        model_name = "offline-judge"

        def __init__(self):
            self.calls = 0

        async def complete(self, prompt: str) -> str:
            self.calls += 1
            if self.calls == 1:
                return ""
            return '{"verdicts":[{"verdict":"yes","reason":"supported"}]}'

    judge = _Judge()
    bridge = _DeepEvalJudgeBridge(judge)

    result = await bridge.a_generate("judge", schema=Verdicts)

    assert judge.calls == 2
    assert result.verdicts[0].verdict == "yes"


@pytest.mark.asyncio
async def test_deepeval_judge_uses_reasoning_json_only_when_final_is_empty():
    from eval.deepeval_eval import _JudgeModel

    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content="",
                    reasoning_content='analysis\n{"reason":"supported"}',
                )
            )
        ]
    )

    class _Completions:
        async def create(self, **kwargs):
            return response

    judge = object.__new__(_JudgeModel)
    judge._settings = SimpleNamespace()
    judge._semaphore = asyncio.Semaphore(1)
    judge.model_name = "offline-judge"
    judge._client = SimpleNamespace(
        chat=SimpleNamespace(completions=_Completions())
    )

    raw = await judge.complete("judge")

    assert raw == 'analysis\n{"reason":"supported"}'


def test_real_faithfulness_policy_uses_valid_enforced_status(tmp_path):
    policy = _load_faithfulness_policy(
        mock=False,
        reports_root=tmp_path,
        settings=SimpleNamespace(),
    )

    assert policy.status == "enforced"
    assert policy.enforced is True
    assert policy.threshold == GATE_FAITHFULNESS


@pytest.mark.asyncio
async def test_real_runner_retrieve_and_skip_use_one_case_specific_correctness_metric(
    monkeypatch,
    tmp_path,
):
    """Run the real case path and report writer with an offline judge."""
    from app.core.config import get_settings
    from app.schemas.rag_evaluation import (
        AnswerRelevancyPolicy,
        FaithfulnessPolicy,
        RagEvaluationRunDetail,
    )
    from deepeval.models import DeepEvalBaseLLM
    from eval import run_full_eval as runner
    from eval.deepeval_eval import ANSWER_CORRECTNESS_NAME, DeepEvalScorer
    from eval.intent_eval import IntentResult
    from eval.rag_costs import RagEvaluationCostLedger
    from eval.reporting import aggregate_summary, write_report

    class _Metric:
        def __init__(self, name: str):
            self.name = name
            self.verbose_logs = ""

    class _StubBridge(DeepEvalBaseLLM):
        def __init__(self):
            super().__init__(model="stub-judge")

        def load_model(self):
            return None

        def get_model_name(self):
            return "stub-judge"

        def generate(self, prompt, *args, **kwargs):
            return "{}"

        async def a_generate(self, prompt, *args, **kwargs):
            return "{}"

    correctness_metric_ids: list[int] = []
    correctness_metrics: list[object] = []
    correctness_criteria: list[str] = []
    measured_stages: list[str | None] = []
    measured_references: list[tuple[str | None, str | None]] = []

    async def fake_measure(self, metric, test_case):
        name = str(getattr(metric, "name", "") or type(metric).__name__)
        measured_stages.append(self._ledger._stage_key)
        measured_references.append(
            (self._ledger._stage_key, getattr(test_case, "expected_output", None))
        )
        metric.verbose_logs = f"verbose-for-{name}"
        if name == ANSWER_CORRECTNESS_NAME:
            correctness_metrics.append(metric)
            correctness_metric_ids.append(id(metric))
            correctness_criteria.append(str(metric.criteria))
        return 0.91, f"reason-for-{name}"

    monkeypatch.setattr(DeepEvalScorer, "_measure", fake_measure)

    suite = load_rag_eval_suite()
    cases = [
        next(case for case in suite.cases if case.rewrite.decision == decision)
        for decision in ("retrieve", "skip")
    ]
    forbidden_fact_id = cases[1].answer.required_fact_ids[0]
    forbidden_case = cases[1].model_copy(
        update={
            "case_id": "rag-offline-forbidden",
            "answer": cases[1].answer.model_copy(
                update={"forbidden_fact_ids": [forbidden_fact_id]}
            ),
        }
    )
    cases.append(forbidden_case)

    async def fake_intent(case):
        decision = case.rewrite.decision
        query = case.user_query if decision == "retrieve" else None
        return IntentResult(
            insight={"knowledge_query": query},
            decision=decision,
            decision_source="rewritten" if query else "explicit_skip",
            knowledge_query=query,
            agent_knowledge_query=query or "",
        )

    async def fake_retrieve(session, query, **kwargs):
        case = next(
            (item for item in cases if item.user_query == query),
            cases[0],
        )
        facts = {fact.fact_id: fact for fact in case.authoritative_facts}
        hits = [
            SimpleNamespace(
                rank=rank,
                chunk_uid=f"hit-{fact_id}",
                title="离线命中",
                source_type="faq",
                content=facts[fact_id].statement,
            )
            for rank, fact_id in enumerate(
                case.retrieval.required_fact_ids,
                start=1,
            )
        ]
        return SimpleNamespace(hits=hits)

    async def fake_agent_reply(case, intent, settings):
        return runner._reference(case) or "收到"

    class _Session:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    monkeypatch.setattr(runner, "evaluate_intent", fake_intent)
    monkeypatch.setattr(runner, "retrieve_indexed_knowledge_result", fake_retrieve)
    monkeypatch.setattr(runner, "_agent_reply", fake_agent_reply)
    monkeypatch.setattr(runner, "SessionLocal", _Session)

    settings = get_settings()
    ledger = RagEvaluationCostLedger(settings)
    scorer = DeepEvalScorer.__new__(DeepEvalScorer)
    scorer._bridge = _StubBridge()
    scorer._context_recall = _Metric("context_recall")
    scorer._context_precision = _Metric("context_precision")
    scorer._faithfulness = _Metric("faithfulness")
    scorer._answer_relevancy = _Metric("answer_relevancy")
    scorer._include_answer_correctness = True
    scorer._ledger = ledger

    results = []
    for case in cases:
        ledger.begin_case(case.case_id)
        result = await runner._evaluate_real_case(
            case,
            scorer=scorer,
            settings=settings,
            answer_relevancy_enforced=False,
            answer_relevancy_threshold=None,
            faithfulness_enforced=True,
            faithfulness_threshold=GATE_FAITHFULNESS,
            cost_ledger=ledger,
        )
        result.usage = ledger.case_usage(case.case_id)
        ledger.end_case(case.case_id)
        results.append(result)

    assert [result.status for result in results] == ["passed", "passed", "failed"]
    assert [result.scores.answer_correctness for result in results] == [
        0.91,
        0.91,
        0.91,
    ]
    assert results[0].scores.raw_context_recall == 0.91
    assert [reason.code for reason in results[2].failure_reasons] == [
        "forbidden_fact_present"
    ]
    assert len(correctness_metric_ids) == 3
    assert len(set(correctness_metric_ids)) == 3
    assert measured_stages == [
        "context_recall",
        "raw_context_recall",
        "context_precision",
        "faithfulness",
        "answer_relevancy",
        "answer_correctness",
        "answer_correctness",
        "answer_correctness",
    ]
    first_case_references = measured_references[:6]
    atomic_reference = runner._retrieval_reference(cases[0])
    answer_reference = runner._reference(cases[0])
    assert [reference for _, reference in first_case_references[:3]] == [
        atomic_reference,
        atomic_reference,
        atomic_reference,
    ]
    assert first_case_references[-1] == ("answer_correctness", None)
    assert answer_reference in correctness_criteria[0]
    assert cases[0].user_query in correctness_criteria[0]
    assert cases[1].user_query in correctness_criteria[1]
    assert results[0].judge_reasons["faithfulness_verbose"].startswith("verbose-")
    assert results[1].judge_reasons["answer_correctness_verbose"].startswith("verbose-")

    base = runner._base_summary(
        run_id="offline-full-path",
        dataset_id=suite.suite_id,
        dataset_version=suite.version,
        total=len(results),
        mock=False,
        answer_relevancy_policy=AnswerRelevancyPolicy(
            status="diagnostic_only",
            enforced=False,
            reason="离线测试",
        ),
        faithfulness_policy=FaithfulnessPolicy(
            status="enforced",
            enforced=True,
            threshold=GATE_FAITHFULNESS,
            reason="离线测试",
        ),
    )
    report = RagEvaluationRunDetail(
        summary=aggregate_summary(base=base, cases=results),
        cases=results,
    )
    write_report(tmp_path, report)
    payload = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert [item["expected"]["retrieval_decision"] for item in payload["cases"]] == [
        "retrieve",
        "skip",
        "skip",
    ]
    assert all(
        item["scores"]["answer_correctness"] == 0.91 for item in payload["cases"]
    )
    assert payload["cases"][0]["judge_reasons"]["context_recall_verbose"]


def test_forbidden_fact_check_uses_declared_fact_without_runtime_error():
    case = load_rag_eval_suite().cases[0]
    forbidden_fact_id = case.authoritative_facts[0].fact_id
    case = case.model_copy(
        update={
            "answer": case.answer.model_copy(
                update={"forbidden_fact_ids": [forbidden_fact_id]}
            )
        }
    )

    reasons = _answer_fact_checks(case, case.authoritative_facts[0].statement)

    assert [reason.code for reason in reasons] == ["forbidden_fact_present"]


def test_cli_default_dataset_is_v3(monkeypatch):
    from eval.run_full_eval import parse_args

    monkeypatch.delenv("RAG_EVALUATION_DATASET_PATH", raising=False)
    monkeypatch.setattr(sys, "argv", ["run_full_eval.py"])

    assert parse_args().dataset.name == "rag_eval_cases.v3.json"
