from __future__ import annotations

from collections import Counter

import pytest

from app.evaluation.faithfulness_calibration import (
    CANDIDATE_THRESHOLDS,
    assess_faithfulness_calibration,
    assess_two_stage_faithfulness_calibration,
    build_faithfulness_calibration_pairs,
    load_faithfulness_calibration_suite,
    resolve_faithfulness_policy,
)
from app.evaluation.rag_cases import load_rag_eval_suite
from app.schemas.rag_evaluation import FaithfulnessCalibrationReport


def _metrics(
    *,
    positive_pass_rate: float = 1.0,
    negative_rejection_rate: float = 1.0,
) -> dict:
    return {
        "positive_mean": 0.70,
        "negative_mean": 0.20,
        "mean_gap": 0.50,
        "auc": 1.0,
        "paired_accuracy": 1.0,
        "positive_pass_rate": positive_pass_rate,
        "negative_rejection_rate": negative_rejection_rate,
    }


def _calibration_report(*, passed: bool) -> FaithfulnessCalibrationReport:
    return FaithfulnessCalibrationReport.model_validate(
        {
            "schema_version": 2,
            "calibration_id": "faithfulness-zh-v4",
            "dataset_version": 4,
            "run_id": "faithfulness-calibration-test",
            "status": "completed",
            "started_at": "2026-08-13T08:00:00Z",
            "updated_at": "2026-08-13T08:20:00Z",
            "finished_at": "2026-08-13T08:20:00Z",
            "git_sha": "1234567",
            "model": "deepseek-chat",
            "ragas_version": "0.4.3",
            "threshold": 0.55,
            "base_pair_count": 30,
            "completed_pair_count": 30,
            "passed": passed,
            "metrics": _metrics(),
            "failed_criteria": (
                []
                if passed
                else ["10组锁定验证题未在两轮中稳定通过0.55门槛"]
            ),
            "items": [],
            "repeat_count": 2,
            "selection_case_count": 20,
            "validation_case_count": 10,
            "selected_threshold": 0.55,
            "candidate_results": [
                {
                    "threshold": threshold,
                    "eligible": threshold == 0.55,
                    "runs": [
                        {
                            "repeat_index": repeat_index,
                            "passed": threshold == 0.55,
                            "metrics": _metrics(),
                        }
                        for repeat_index in (1, 2)
                    ],
                }
                for threshold in CANDIDATE_THRESHOLDS
            ],
            "validation_results": [
                {
                    "repeat_index": repeat_index,
                    "passed": passed,
                    "metrics": _metrics(
                        positive_pass_rate=1.0 if passed else 0.8
                    ),
                }
                for repeat_index in (1, 2)
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
    assert set(pair.error_type for pair in pairs) == {
        "number_or_unit",
        "negation",
        "condition_omission",
        "fact_reversal",
        "unsupported_advice",
    }
    for pair in pairs:
        source_statements = {
            fact.statement
            for fact in rag_cases[pair.source_case_id].authoritative_facts
        }
        assert set(pair.retrieved_contexts).issubset(source_statements)
        assert pair.positive_response != pair.negative_response
        assert all("近期对话" not in context for context in pair.retrieved_contexts)


def test_two_stage_calibration_selects_middle_threshold_then_validates_it():
    selection = [
        ([0.58] * 20, [0.52] * 20),
        ([0.57] * 20, [0.53] * 20),
    ]
    validation = [
        ([0.59] * 10, [0.51] * 10),
        ([0.58] * 10, [0.52] * 10),
    ]

    result = assess_two_stage_faithfulness_calibration(
        selection_runs=selection,
        validation_runs=validation,
    )

    assert result.selected_threshold == 0.55
    assert result.passed is True
    assert [candidate.eligible for candidate in result.candidates] == [
        False,
        True,
        False,
    ]
    assert all(run.passed for run in result.validation_runs)


def test_two_stage_calibration_rejects_threshold_when_one_validation_repeat_fails():
    selection = [
        ([0.58] * 20, [0.52] * 20),
        ([0.57] * 20, [0.53] * 20),
    ]
    validation = [
        ([0.59] * 10, [0.51] * 10),
        ([0.58] * 8 + [0.54] * 2, [0.52] * 10),
    ]

    result = assess_two_stage_faithfulness_calibration(
        selection_runs=selection,
        validation_runs=validation,
    )

    assert result.selected_threshold == 0.55
    assert result.passed is False
    assert result.validation_runs[1].assessment.positive_pass_rate == 0.8
    assert "锁定验证题" in result.failed_criteria[0]


def test_two_stage_calibration_does_not_select_when_no_candidate_is_stable():
    selection = [
        ([0.54] * 20, [0.53] * 20),
        ([0.61] * 20, [0.60] * 20),
    ]

    result = assess_two_stage_faithfulness_calibration(
        selection_runs=selection,
        validation_runs=[([0.9] * 10, [0.1] * 10)] * 2,
    )

    assert result.selected_threshold is None
    assert result.validation_runs == ()
    assert result.passed is False


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
        _calibration_report(passed=False),
        model="deepseek-chat",
        ragas_version="0.4.3",
    )

    assert policy.status == "calibration_failed"
    assert policy.enforced is False
    assert policy.threshold is None
    assert policy.calibration_run_id == "faithfulness-calibration-test"
    assert "仅展示" in policy.reason


def test_passed_faithfulness_calibration_exposes_selected_threshold():
    policy = resolve_faithfulness_policy(
        _calibration_report(passed=True),
        model="deepseek-chat",
        ragas_version="0.4.3",
    )

    assert policy.status == "enforced"
    assert policy.enforced is True
    assert policy.threshold == 0.55


def test_incomplete_two_stage_report_cannot_enable_faithfulness():
    report = _calibration_report(passed=True).model_copy(
        update={"validation_results": []}
    )

    policy = resolve_faithfulness_policy(
        report,
        model="deepseek-chat",
        ragas_version="0.4.3",
    )

    assert policy.status == "calibration_failed"
    assert policy.enforced is False
    assert policy.threshold is None


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
        update={"calibration_id": "faithfulness-zh-v3", "dataset_version": 3}
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
