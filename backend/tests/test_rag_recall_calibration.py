"""Zero-cost checks for the context-recall calibration suite and runner.

No network, no model calls: the suite contract, the shared atomic-clause
rendering, the two-stage selection maths and the recall-shaped test case are
all verified offline.
"""

from __future__ import annotations

import json

import pytest

from app.evaluation.context_recall_calibration import (
    MAX_USER_INPUT_QUESTIONS,
    MIN_REFERENCE_CLAUSES,
    PROHIBITION_MARKERS,
    SELECTION_CASE_COUNT,
    VALIDATION_CASE_COUNT,
    ContextRecallCalibrationSuite,
    assess_two_stage_context_recall_calibration,
    build_context_recall_calibration_pairs,
    default_context_recall_calibration_path,
    load_context_recall_calibration_suite,
)
from app.evaluation.rag_metric_calibration import (
    render_atomic_retrieval_reference,
    split_atomic_clauses,
)


def test_suite_loads_with_fixed_phase_counts_and_miss_type_coverage():
    suite = load_context_recall_calibration_suite()
    assert suite.calibration_id == "context-recall-zh-v2"
    assert len(suite.cases) == SELECTION_CASE_COUNT + VALIDATION_CASE_COUNT
    phases = [case.phase for case in suite.cases]
    assert phases.count("selection") == SELECTION_CASE_COUNT
    assert phases.count("validation") == VALIDATION_CASE_COUNT
    assert len({case.miss_type for case in suite.cases}) >= 3


def test_every_pair_uses_distinct_real_contexts_on_both_sides():
    suite = load_context_recall_calibration_suite()
    for case in suite.cases:
        assert case.positive_contexts != case.negative_contexts, case.case_id
        assert all(text.strip() for text in case.positive_contexts), case.case_id
        assert all(text.strip() for text in case.negative_contexts), case.case_id
        # A negative built by copying the positive chunk would make the suite
        # trivially separable and the selected threshold meaningless.
        assert not set(case.positive_contexts) & set(case.negative_contexts), case.case_id


def test_every_reference_states_retrievable_facts_not_prohibitions():
    """A prohibition ("不应解释为代谢启动") has no retrievable truth: no chunk
    asserts the absence of a wrong framing, so the v1 judge returned opposite
    verdicts for the same pair across repeats. Recall truth stays positive."""
    suite = load_context_recall_calibration_suite()
    for case in suite.cases:
        offenders = [m for m in PROHIBITION_MARKERS if m in case.reference]
        assert not offenders, (case.case_id, offenders)
        assert len(split_atomic_clauses(case.reference)) >= MIN_REFERENCE_CLAUSES, case.case_id


def test_user_questions_are_single_real_utterances_and_distinct():
    """v2's first cut pasted the FAQ's alternative-phrasing list in as the user
    question, which bundled three questions into one and left two cases sharing
    the same text. Recall ignores the question; the precision calibration reuses
    this suite and does not."""
    suite = load_context_recall_calibration_suite()
    questions = [case.user_input for case in suite.cases]
    assert len(set(questions)) == len(questions)
    for case in suite.cases:
        marks = case.user_input.count("？") + case.user_input.count("?")
        assert marks <= MAX_USER_INPUT_QUESTIONS, (case.case_id, case.user_input)


def test_suite_rejects_bundled_user_question():
    payload = json.loads(
        default_context_recall_calibration_path().read_text(encoding="utf-8")
    )
    payload["cases"][0]["user_input"] = "一瓶盖等于几个刻度？一大刻度又是多少？够不够足量？"
    with pytest.raises(ValueError, match="one real utterance"):
        ContextRecallCalibrationSuite.model_validate(payload)


def test_suite_rejects_shared_user_question():
    payload = json.loads(
        default_context_recall_calibration_path().read_text(encoding="utf-8")
    )
    payload["cases"][1]["user_input"] = payload["cases"][0]["user_input"]
    with pytest.raises(ValueError, match="reuses one user question"):
        ContextRecallCalibrationSuite.model_validate(payload)


def test_suite_rejects_prohibition_reference():
    payload = json.loads(
        default_context_recall_calibration_path().read_text(encoding="utf-8")
    )
    case = payload["cases"][0]
    case["reference"] = case["reference"].rstrip("。") + "；不要自行加量。"
    with pytest.raises(ValueError, match="prohibitions"):
        ContextRecallCalibrationSuite.model_validate(payload)


def test_suite_rejects_bundled_single_clause_reference():
    """v1 shipped references that never split, so a half-covered negative was
    attributed from its main clause alone and scored full marks."""
    payload = json.loads(
        default_context_recall_calibration_path().read_text(encoding="utf-8")
    )
    payload["cases"][0]["reference"] = "午B超过下午两点当天直接跳过，当天的晚A照常执行。"
    with pytest.raises(ValueError, match="atomic clauses"):
        ContextRecallCalibrationSuite.model_validate(payload)


def test_suite_rejects_wrong_phase_counts():
    payload = json.loads(
        default_context_recall_calibration_path().read_text(encoding="utf-8")
    )
    payload["cases"][0]["phase"] = "validation"
    with pytest.raises(ValueError):
        ContextRecallCalibrationSuite.model_validate(payload)


def test_suite_rejects_identical_sides():
    payload = json.loads(
        default_context_recall_calibration_path().read_text(encoding="utf-8")
    )
    case = payload["cases"][0]
    case["negative_contexts"] = list(case["positive_contexts"])
    case["negative_sources"] = list(case["positive_sources"])
    with pytest.raises(ValueError):
        ContextRecallCalibrationSuite.model_validate(payload)


def test_atomic_reference_rendering_splits_clauses_like_production():
    rendered = render_atomic_retrieval_reference(
        ["午B随午餐前后饮用，尽量下午两点前完成；超过时间当天跳过。晚A照常。"]
    )
    assert rendered.splitlines() == [
        "- 午B随午餐前后饮用，尽量下午两点前完成",
        "- 超过时间当天跳过",
        "- 晚A照常",
    ]


def test_two_stage_selection_picks_midpoint_and_locks_validation():
    selection = [([1.0] * 20, [0.5] * 20), ([1.0] * 20, [0.5] * 20)]
    validation = [([1.0] * 10, [0.5] * 10), ([1.0] * 10, [0.5] * 10)]
    result = assess_two_stage_context_recall_calibration(
        selection_runs=selection, validation_runs=validation
    )
    assert result.passed is True
    assert result.selected_threshold == pytest.approx(0.75)


def test_two_stage_selection_reports_failure_when_classes_overlap():
    overlapping = ([0.6] * 20, [0.9] * 20)
    validation = [([1.0] * 10, [0.0] * 10), ([1.0] * 10, [0.0] * 10)]
    result = assess_two_stage_context_recall_calibration(
        selection_runs=[overlapping, overlapping], validation_runs=validation
    )
    assert result.passed is False
    assert result.selected_threshold is None
    assert result.failed_criteria


def test_recall_test_case_varies_contexts_not_reply():
    from eval.deepeval_rag_comparison import _recall_test_case

    pair = build_context_recall_calibration_pairs(
        load_context_recall_calibration_suite()
    )[0]
    positive = _recall_test_case(pair, "positive")
    negative = _recall_test_case(pair, "negative")

    assert positive.expected_output == negative.expected_output
    assert positive.actual_output == positive.expected_output
    assert positive.retrieval_context != negative.retrieval_context
    assert positive.retrieval_context == pair.positive_contexts
    assert negative.retrieval_context == pair.negative_contexts
    assert positive.expected_output.startswith("- ")
    assert positive.input == pair.user_input


@pytest.mark.asyncio
async def test_judge_real_call_path_records_latency_without_network():
    """20c815e added latency instrumentation to the judge but no `import time`,
    so every real judgement raised NameError before reaching the API. Mock runs
    never touch this path, which is why it stayed broken and invisible."""
    import asyncio
    from types import SimpleNamespace

    from eval import deepeval_rag_comparison as runner

    settings = SimpleNamespace(
        ai_base_url="http://localhost", ai_api_key="test", ai_model="test-model",
        ai_timeout_seconds=5, ai_prompt_cost_per_1m_tokens=0.0,
        ai_completion_cost_per_1m_tokens=0.0,
        ai_prompt_cache_hit_cost_per_1m_tokens=0.0,
        ai_premium_models="", ai_peak_price_multiplier=1,
    )
    judge = runner.DeepSeekJudge(
        settings, runner.ComparisonLedger(settings, 1.0), asyncio.Semaphore(1)
    )

    class _Completions:
        async def create(self, **_kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))],
                usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
            )

    judge._async_client = SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))

    assert await judge._a_call("ping") == "{}"
    assert judge._ledger.calls == 1
    assert judge._ledger.call_log[0]["latency_ms"] >= 0


@pytest.mark.asyncio
async def test_judgement_reasons_are_kept_alongside_the_score():
    """The first real recall calibration only persisted floats, so an unstable
    pair could only be diagnosed by grepping the finished run's call log."""
    from types import SimpleNamespace

    from eval import deepeval_rag_comparison as runner

    class _Metric:
        async def a_measure(self, _test_case):
            self.reason = "两条子句中只有一条能在检索结果中找到依据"
            self.verdicts = [
                SimpleNamespace(verdict="yes", reason="第1个节点提到了午B超过两点不补喝"),
                SimpleNamespace(verdict="no", reason="检索结果没有提到晚A照常执行"),
            ]
            return 0.5

    score, error, detail = await runner._ascore_detail(_Metric(), object())
    assert (score, error) == (0.5, None)
    assert detail["reason"].startswith("两条子句")
    assert [v["verdict"] for v in detail["verdicts"]] == ["yes", "no"]
    assert "晚A照常" in detail["verdicts"][1]["reason"]


@pytest.mark.asyncio
async def test_mock_calibration_items_cover_every_pair_and_repeat():
    from eval import deepeval_rag_comparison as runner

    pairs = build_context_recall_calibration_pairs(
        load_context_recall_calibration_suite()
    )
    items = await runner._run_calibration_items(
        judge=None,
        metric_kind="context_recall",
        pairs=pairs,
        needs_retrieval_context=True,
        mock=True,
        on_progress=lambda done, total: None,
        build_test_case=runner._recall_test_case,
    )
    assert len(items) == len(pairs) * 2
    assert {item["repeat_index"] for item in items} == {1, 2}
    assert all(not item["errors"] for item in items)
    assert all(item["positive_score"] > item["negative_score"] for item in items)
