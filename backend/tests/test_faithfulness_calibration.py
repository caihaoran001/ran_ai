from __future__ import annotations

import pytest

from app.evaluation.faithfulness_calibration import (
    assess_faithfulness_calibration,
    build_faithfulness_calibration_pairs,
    load_faithfulness_calibration_suite,
    resolve_faithfulness_policy,
)
from app.evaluation.rag_cases import load_rag_eval_suite
from app.schemas.rag_evaluation import FaithfulnessCalibrationReport


def _calibration_report(*, passed: bool) -> FaithfulnessCalibrationReport:
    return FaithfulnessCalibrationReport.model_validate(
        {
            "calibration_id": "faithfulness-zh-v3",
            "dataset_version": 3,
            "run_id": "faithfulness-calibration-test",
            "status": "completed",
            "started_at": "2026-08-11T08:00:00Z",
            "updated_at": "2026-08-11T08:10:00Z",
            "finished_at": "2026-08-11T08:10:00Z",
            "git_sha": "1234567",
            "model": "deepseek-chat",
            "ragas_version": "0.4.3",
            "threshold": 0.85,
            "base_pair_count": 10,
            "completed_pair_count": 10,
            "passed": passed,
            "metrics": {
                "positive_mean": 0.92,
                "negative_mean": 0.18,
                "mean_gap": 0.74,
                "auc": 1.0,
                "paired_accuracy": 1.0,
                "positive_pass_rate": 0.9,
                "negative_rejection_rate": 1.0,
            },
            "failed_criteria": [] if passed else ["正例在0.85门槛下通过率低于0.80"],
            "items": [],
        }
    )


def test_fixed_faithfulness_suite_reuses_authoritative_rag_facts():
    suite = load_faithfulness_calibration_suite()
    pairs = build_faithfulness_calibration_pairs(suite)
    rag_cases = {case.case_id: case for case in load_rag_eval_suite().cases}

    assert suite.calibration_id == "faithfulness-zh-v3"
    assert suite.version == 3
    assert len(pairs) == 10
    assert len({pair.case_id for pair in pairs}) == 10
    assert len({pair.source_case_id for pair in pairs}) == 10
    for pair in pairs:
        source_statements = {
            fact.statement
            for fact in rag_cases[pair.source_case_id].authoritative_facts
        }
        assert set(pair.retrieved_contexts).issubset(source_statements)
        assert pair.positive_response != pair.negative_response
        assert all("近期对话" not in context for context in pair.retrieved_contexts)


def test_faithfulness_calibration_passes_when_threshold_separates_both_classes():
    assessment = assess_faithfulness_calibration(
        [0.91, 0.93, 0.88, 0.86, 0.90, 0.92, 0.89, 0.94, 0.95, 0.87],
        [0.12, 0.21, 0.25, 0.30, 0.18, 0.33, 0.27, 0.15, 0.24, 0.29],
    )

    assert assessment.passed is True
    assert assessment.auc == 1.0
    assert assessment.positive_pass_rate == 1.0
    assert assessment.negative_rejection_rate == 1.0


def test_faithfulness_calibration_rejects_invalid_point_eight_five_threshold():
    assessment = assess_faithfulness_calibration(
        [0.71, 0.74, 0.76, 0.73, 0.78, 0.72, 0.75, 0.77, 0.79, 0.70],
        [0.11, 0.14, 0.16, 0.13, 0.18, 0.12, 0.15, 0.17, 0.19, 0.10],
    )

    assert assessment.auc == 1.0
    assert assessment.positive_pass_rate == 0.0
    assert assessment.passed is False
    assert assessment.failed_criteria == ("正例在0.85门槛下通过率低于0.80",)


def test_failed_faithfulness_calibration_makes_metric_observational_only():
    policy = resolve_faithfulness_policy(
        _calibration_report(passed=False),
        model="deepseek-chat",
        ragas_version="0.4.3",
    )

    assert policy.status == "calibration_failed"
    assert policy.enforced is False
    assert policy.calibration_run_id == "faithfulness-calibration-test"
    assert "仅展示" in policy.reason


def test_faithfulness_calibration_only_applies_to_matching_judge():
    policy = resolve_faithfulness_policy(
        _calibration_report(passed=True),
        model="different-model",
        ragas_version="0.4.3",
    )

    assert policy.status == "not_calibrated"
    assert policy.enforced is False
    assert "判官模型" in policy.reason


def test_faithfulness_calibration_only_applies_to_current_dataset_version():
    report = _calibration_report(passed=True).model_copy(
        update={"calibration_id": "faithfulness-zh-v2", "dataset_version": 2}
    )

    policy = resolve_faithfulness_policy(
        report,
        model="deepseek-chat",
        ragas_version="0.4.3",
    )

    assert policy.status == "not_calibrated"
    assert policy.enforced is False
    assert "校准题版本" in policy.reason


@pytest.mark.parametrize(
    ("positive", "negative"),
    [([], []), ([0.9], []), ([1.1], [0.1])],
)
def test_faithfulness_calibration_rejects_invalid_score_inputs(positive, negative):
    with pytest.raises(ValueError, match="calibration"):
        assess_faithfulness_calibration(positive, negative)
