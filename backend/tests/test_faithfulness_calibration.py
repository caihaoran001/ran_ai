from __future__ import annotations

from collections import Counter

import pytest

from app.evaluation.faithfulness_calibration import (
    assess_faithfulness_calibration,
    assess_two_stage_faithfulness_calibration,
    build_faithfulness_calibration_pairs,
    load_faithfulness_calibration_suite,
    resolve_faithfulness_policy,
)
from app.evaluation.rag_cases import load_rag_eval_suite
from app.schemas.rag_evaluation import (
    FaithfulnessCalibrationItem,
    FaithfulnessCalibrationReport,
)
from eval.reanalyze_faithfulness import _verify_source_report


def _metrics() -> dict:
    return {
        "positive_mean": 0.70,
        "negative_mean": 0.20,
        "mean_gap": 0.50,
        "auc": 1.0,
        "paired_accuracy": 1.0,
        "positive_pass_rate": 1.0,
        "negative_rejection_rate": 1.0,
    }


def _complete_items() -> list[FaithfulnessCalibrationItem]:
    suite = load_faithfulness_calibration_suite()
    return [
        FaithfulnessCalibrationItem(
            case_id=pair.case_id,
            source_case_id=pair.source_case_id,
            user_input=pair.user_input,
            retrieved_contexts=pair.retrieved_contexts,
            positive_response=pair.positive_response,
            negative_response=pair.negative_response,
            positive_score=0.75,
            negative_score=0.50,
            phase=pair.phase,
            error_type=pair.error_type,
            repeat_index=repeat_index,
        )
        for repeat_index in (1, 2)
        for pair in build_faithfulness_calibration_pairs(suite)
    ]


def _calibration_report(*, passed: bool) -> FaithfulnessCalibrationReport:
    return FaithfulnessCalibrationReport.model_validate(
        {
            "schema_version": 3,
            "calibration_id": "faithfulness-zh-v4",
            "dataset_version": 4,
            "run_id": "faithfulness-calibration-test",
            "status": "completed",
            "started_at": "2026-08-13T08:00:00Z",
            "updated_at": "2026-08-13T08:20:00Z",
            "finished_at": "2026-08-13T08:20:00Z",
            "git_sha": "1234567",
            "model": "deepseek-v4-flash",
            "ragas_version": "0.4.3",
            "threshold": 0.625,
            "base_pair_count": 30,
            "completed_pair_count": 30,
            "passed": passed,
            "metrics": _metrics(),
            "failed_criteria": [] if passed else ["10组锁定验证题未通过"],
            "items": [item.model_dump(mode="json") for item in _complete_items()],
            "repeat_count": 2,
            "selection_case_count": 20,
            "validation_case_count": 10,
            "selected_threshold": 0.625,
            "threshold_selection": {
                "rule": "perfect_gap_midpoint_v1",
                "minimum_classification_rate": 1.0,
                "lower_exclusive": 0.50,
                "upper_inclusive": 0.75,
                "selected_threshold": 0.625,
                "safety_margin": 0.125,
            },
            "selection_results": [
                {"repeat_index": index, "passed": True, "metrics": _metrics()}
                for index in (1, 2)
            ],
            "validation_results": [
                {"repeat_index": index, "passed": passed, "metrics": _metrics()}
                for index in (1, 2)
            ],
        }
    )


def test_fixed_faithfulness_suite_reuses_authoritative_rag_facts():
    suite = load_faithfulness_calibration_suite()
    pairs = build_faithfulness_calibration_pairs(suite)
    rag_cases = {case.case_id: case for case in load_rag_eval_suite().cases}

    assert suite.calibration_id == "faithfulness-zh-v4"
    assert suite.version == 4
    assert len(pairs) == 30
    assert len({pair.case_id for pair in pairs}) == 30
    assert len({pair.source_case_id for pair in pairs}) == 30
    assert Counter(pair.phase for pair in pairs) == {
        "selection": 20,
        "validation": 10,
    }
    for pair in pairs:
        source_statements = {
            fact.statement for fact in rag_cases[pair.source_case_id].authoritative_facts
        }
        assert set(pair.retrieved_contexts).issubset(source_statements)
        assert pair.positive_response != pair.negative_response
        assert all("近期对话" not in context for context in pair.retrieved_contexts)


def test_two_stage_calibration_selects_midpoint_of_common_data_interval():
    result = assess_two_stage_faithfulness_calibration(
        selection_runs=[([0.75] * 20, [0.50] * 20)] * 2,
        validation_runs=[([0.70] * 10, [0.40] * 10)] * 2,
    )

    assert result.threshold_selection is not None
    assert result.threshold_selection.rule == "perfect_gap_midpoint_v1"
    assert result.threshold_selection.minimum_classification_rate == 1.0
    assert result.threshold_selection.lower_exclusive == 0.50
    assert result.threshold_selection.upper_inclusive == 0.75
    assert result.selected_threshold == 0.625
    assert all(run.passed for run in result.selection_runs)
    assert result.passed is True


def test_selection_keeps_a_90_percent_interval_when_boundary_scores_overlap():
    result = assess_two_stage_faithfulness_calibration(
        selection_runs=[([0.40] * 2 + [0.70] * 18, [0.30] * 18 + [0.60] * 2)] * 2,
        validation_runs=[([0.60] * 10, [0.30] * 10)] * 2,
    )

    assert result.threshold_selection is not None
    assert result.threshold_selection.rule == "class_rate_interval_midpoint_v1"
    assert result.threshold_selection.minimum_classification_rate == 0.9
    assert result.selected_threshold == 0.50
    assert all(run.assessment.positive_pass_rate == 0.9 for run in result.selection_runs)
    assert all(run.assessment.negative_rejection_rate == 0.9 for run in result.selection_runs)


def test_two_stage_calibration_rejects_when_no_common_interval_exists():
    result = assess_two_stage_faithfulness_calibration(
        selection_runs=[([0.54] * 20, [0.53] * 20), ([0.61] * 20, [0.60] * 20)],
        validation_runs=[([0.9] * 10, [0.1] * 10)] * 2,
    )

    assert result.selected_threshold is None
    assert result.selection_runs == ()
    assert result.validation_runs == ()
    assert result.passed is False


def test_two_stage_calibration_rejects_when_validation_fails():
    result = assess_two_stage_faithfulness_calibration(
        selection_runs=[([0.70] * 20, [0.30] * 20)] * 2,
        validation_runs=[([0.70] * 10, [0.30] * 10), ([0.70] * 8 + [0.40] * 2, [0.30] * 10)],
    )

    assert result.selected_threshold == 0.50
    assert result.passed is False
    assert result.validation_runs[1].assessment.positive_pass_rate == 0.8


def test_faithfulness_calibration_score_validation_remains_strict():
    assessment = assess_faithfulness_calibration(
        [0.71, 0.74, 0.76],
        [0.11, 0.14, 0.16],
        threshold=0.55,
    )

    assert assessment.auc == 1.0
    assert assessment.positive_pass_rate == 1.0
    assert assessment.negative_rejection_rate == 1.0


def test_failed_faithfulness_calibration_makes_metric_observational_only():
    policy = resolve_faithfulness_policy(
        _calibration_report(passed=False), model="deepseek-v4-flash", ragas_version="0.4.3"
    )

    assert policy.status == "calibration_failed"
    assert policy.enforced is False
    assert policy.threshold is None


def test_passed_faithfulness_calibration_exposes_selected_threshold():
    policy = resolve_faithfulness_policy(
        _calibration_report(passed=True), model="deepseek-v4-flash", ragas_version="0.4.3"
    )

    assert policy.status == "enforced"
    assert policy.threshold == 0.625


def test_previously_default_selected_schema_cannot_enable_faithfulness():
    report = _calibration_report(passed=True).model_copy(update={"schema_version": 2})

    policy = resolve_faithfulness_policy(
        report, model="deepseek-v4-flash", ragas_version="0.4.3"
    )

    assert policy.status == "calibration_failed"
    assert policy.enforced is False
    assert "已废弃" in policy.reason


def test_report_cannot_enable_a_threshold_that_ignores_a_perfect_gap():
    original = _calibration_report(passed=True)
    report = original.model_copy(
        update={
            "threshold": 0.666667,
            "selected_threshold": 0.666667,
            "threshold_selection": original.threshold_selection.model_copy(
                update={
                    "rule": "class_rate_interval_midpoint_v1",
                    "minimum_classification_rate": 0.9,
                    "lower_exclusive": 0.333333,
                    "upper_inclusive": 1.0,
                    "selected_threshold": 0.666667,
                    "safety_margin": 0.333333,
                }
            ),
        }
    )

    policy = resolve_faithfulness_policy(
        report, model="deepseek-v4-flash", ragas_version="0.4.3"
    )

    assert policy.status == "calibration_failed"
    assert policy.enforced is False
    assert "不符合当前选线规则" in policy.reason


def test_existing_complete_scores_can_be_reanalysed_without_a_model_call():
    suite = load_faithfulness_calibration_suite()
    source = _calibration_report(passed=True).model_copy(
        update={"schema_version": 2}
    )

    _verify_source_report(source, suite=suite)


@pytest.mark.parametrize(
    ("positive", "negative"),
    [([], []), ([0.9], []), ([1.1], [0.1])],
)
def test_faithfulness_calibration_rejects_invalid_score_inputs(positive, negative):
    with pytest.raises(ValueError, match="calibration"):
        assess_faithfulness_calibration(positive, negative, threshold=0.55)
