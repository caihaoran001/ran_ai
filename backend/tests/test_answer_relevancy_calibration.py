from __future__ import annotations

import pytest

from app.evaluation.answer_relevancy_calibration import (
    MIN_STAGE_CLASSIFICATION_RATE,
    assess_answer_relevancy_calibration,
    assess_two_stage_answer_relevancy_calibration,
    build_answer_relevancy_calibration_pairs,
    load_answer_relevancy_calibration_suite,
    resolve_answer_relevancy_policy,
)
from app.schemas.rag_evaluation import AnswerRelevancyCalibrationReport


def _metrics() -> dict:
    return {
        "positive_mean": 0.60,
        "negative_mean": 0.30,
        "mean_gap": 0.30,
        "auc": 1.0,
        "paired_accuracy": 1.0,
        "positive_pass_rate": 1.0,
        "negative_rejection_rate": 1.0,
    }


def _calibration_report(*, passed: bool) -> AnswerRelevancyCalibrationReport:
    return AnswerRelevancyCalibrationReport.model_validate(
        {
            "schema_version": 2,
            "calibration_id": "answer-relevancy-zh-v3",
            "dataset_version": 3,
            "run_id": "relevancy-calibration-test",
            "status": "completed",
            "started_at": "2026-08-14T07:00:00Z",
            "updated_at": "2026-08-14T07:10:00Z",
            "finished_at": "2026-08-14T07:10:00Z",
            "git_sha": "1234567",
            "model": "deepseek-v4-flash",
            "embedding_model": "text-embedding-v4",
            "ragas_version": "0.4.3",
            "threshold": 0.45,
            "base_pair_count": 30,
            "completed_pair_count": 30,
            "passed": passed,
            "metrics": _metrics(),
            "failed_criteria": [] if passed else ["10组锁定验证题未通过"],
            "items": [],
            "repeat_count": 2,
            "selection_case_count": 20,
            "validation_case_count": 10,
            "selected_threshold": 0.45,
            "threshold_selection": {
                "rule": "class_rate_interval_midpoint_v1",
                "minimum_classification_rate": MIN_STAGE_CLASSIFICATION_RATE,
                "lower_exclusive": 0.40,
                "upper_inclusive": 0.50,
                "selected_threshold": 0.45,
                "safety_margin": 0.05,
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


def test_fixed_chinese_calibration_suite_uses_thirty_static_answer_swaps():
    suite = load_answer_relevancy_calibration_suite()
    pairs = build_answer_relevancy_calibration_pairs(suite)

    assert suite.calibration_id == "answer-relevancy-zh-v3"
    assert suite.version == 3
    assert len(pairs) == 30
    assert sum(pair.phase == "selection" for pair in pairs) == 20
    assert sum(pair.phase == "validation" for pair in pairs) == 10
    assert len({pair.source_case_id for pair in pairs}) == 30
    assert len({pair.negative_source_case_id for pair in pairs}) == 30
    assert all(pair.source_case_id != pair.negative_source_case_id for pair in pairs)
    assert all(pair.positive_response != pair.negative_response for pair in pairs)


def test_two_stage_calibration_selects_midpoint_then_validates_it():
    result = assess_two_stage_answer_relevancy_calibration(
        selection_runs=[([0.60] * 20, [0.30] * 20)] * 2,
        validation_runs=[([0.55] * 10, [0.25] * 10)] * 2,
    )

    assert result.selected_threshold == 0.45
    assert result.threshold_selection is not None
    assert result.threshold_selection.rule == "perfect_gap_midpoint_v1"
    assert result.threshold_selection.minimum_classification_rate == 1.0
    assert result.threshold_selection.lower_exclusive == 0.30
    assert result.threshold_selection.upper_inclusive == 0.60
    assert result.passed is True


def test_two_stage_calibration_rejects_missing_common_interval():
    result = assess_two_stage_answer_relevancy_calibration(
        selection_runs=[([0.45] * 20, [0.44] * 20), ([0.65] * 20, [0.64] * 20)],
        validation_runs=[([0.8] * 10, [0.2] * 10)] * 2,
    )

    assert result.selected_threshold is None
    assert result.validation_runs == ()
    assert result.passed is False


def test_calibration_score_validation_remains_strict():
    assessment = assess_answer_relevancy_calibration(
        [0.61, 0.64, 0.66], [0.11, 0.14, 0.16], threshold=0.45
    )

    assert assessment.auc == 1.0
    assert assessment.positive_pass_rate == 1.0
    assert assessment.negative_rejection_rate == 1.0


@pytest.mark.parametrize(
    ("positive", "negative"),
    [([], []), ([0.9], []), ([1.1], [0.1])],
)
def test_calibration_rejects_invalid_score_inputs(positive, negative):
    with pytest.raises(ValueError, match="calibration"):
        assess_answer_relevancy_calibration(positive, negative, threshold=0.45)


def test_failed_calibration_makes_answer_relevancy_observational_only():
    policy = resolve_answer_relevancy_policy(
        _calibration_report(passed=False),
        model="deepseek-v4-flash",
        embedding_model="text-embedding-v4",
        ragas_version="0.4.3",
    )

    assert policy.status == "calibration_failed"
    assert policy.enforced is False
    assert policy.threshold is None


def test_calibration_enables_answer_relevancy_with_selected_threshold():
    policy = resolve_answer_relevancy_policy(
        _calibration_report(passed=True),
        model="deepseek-v4-flash",
        embedding_model="text-embedding-v4",
        ragas_version="0.4.3",
    )

    assert policy.status == "enforced"
    assert policy.threshold == 0.45


def test_previous_fixed_threshold_report_cannot_enable_answer_relevancy():
    report = _calibration_report(passed=True).model_copy(
        update={"schema_version": 1, "calibration_id": "answer-relevancy-zh-v2"}
    )

    policy = resolve_answer_relevancy_policy(
        report,
        model="deepseek-v4-flash",
        embedding_model="text-embedding-v4",
        ragas_version="0.4.3",
    )

    assert policy.status == "not_calibrated"
    assert policy.enforced is False
