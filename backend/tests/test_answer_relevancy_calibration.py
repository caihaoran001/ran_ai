from __future__ import annotations

import pytest

from app.evaluation.answer_relevancy_calibration import (
    assess_answer_relevancy_calibration,
    build_answer_relevancy_calibration_pairs,
    load_answer_relevancy_calibration_suite,
)


def test_fixed_chinese_calibration_suite_uses_one_to_one_answer_swaps():
    suite = load_answer_relevancy_calibration_suite()
    pairs = build_answer_relevancy_calibration_pairs(suite)

    assert suite.calibration_id == "answer-relevancy-zh-v1"
    assert len(pairs) == 10
    assert len({pair.case_id for pair in pairs}) == 10
    assert len({pair.negative_source_case_id for pair in pairs}) == 10
    assert all(pair.case_id != pair.negative_source_case_id for pair in pairs)
    assert all(pair.positive_response != pair.negative_response for pair in pairs)


def test_calibration_passes_when_current_threshold_separates_both_classes():
    assessment = assess_answer_relevancy_calibration(
        [0.91, 0.93, 0.88, 0.86, 0.90, 0.92, 0.84, 0.89, 0.95, 0.87],
        [0.12, 0.21, 0.25, 0.30, 0.18, 0.33, 0.27, 0.15, 0.24, 0.29],
    )

    assert assessment.passed is True
    assert assessment.auc == 1.0
    assert assessment.paired_accuracy == 1.0
    assert assessment.positive_pass_rate == 1.0
    assert assessment.negative_rejection_rate == 1.0
    assert assessment.failed_criteria == ()


def test_calibration_fails_when_metric_ranks_well_but_point_eight_is_invalid():
    assessment = assess_answer_relevancy_calibration(
        [0.61, 0.64, 0.66, 0.63, 0.68, 0.62, 0.65, 0.67, 0.69, 0.60],
        [0.11, 0.14, 0.16, 0.13, 0.18, 0.12, 0.15, 0.17, 0.19, 0.10],
    )

    assert assessment.auc == 1.0
    assert assessment.paired_accuracy == 1.0
    assert assessment.positive_pass_rate == 0.0
    assert assessment.negative_rejection_rate == 1.0
    assert assessment.passed is False
    assert assessment.failed_criteria == (
        "正例在0.80门槛下通过率低于0.80",
    )


@pytest.mark.parametrize(
    ("positive", "negative"),
    [([], []), ([0.9], []), ([1.1], [0.1])],
)
def test_calibration_rejects_invalid_score_inputs(positive, negative):
    with pytest.raises(ValueError, match="calibration"):
        assess_answer_relevancy_calibration(positive, negative)
