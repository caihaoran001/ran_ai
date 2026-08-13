from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.evaluation.rag_metric_calibration import (
    RagMetricCalibrationAssessment,
    assess_rag_metric_calibration,
)
from app.schemas.rag_evaluation import (
    FaithfulnessCalibrationReport,
    FaithfulnessPolicy,
)


CANDIDATE_THRESHOLDS = (0.50, 0.55, 0.60)
DEFAULT_CALIBRATION_THRESHOLD = 0.55
FIXED_CALIBRATION_CASE_COUNT = 30
SELECTION_CASE_COUNT = 20
VALIDATION_CASE_COUNT = 10
CALIBRATION_REPEAT_COUNT = 2
MIN_STAGE_CLASSIFICATION_RATE = 0.90


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FaithfulnessCalibrationCase(StrictModel):
    case_id: str = Field(pattern=r"^faithfulness-[a-z0-9-]+$")
    source_case_id: str = Field(pattern=r"^rag-(faq|chat)-[a-z0-9-]+$")
    user_input: str = Field(min_length=1, max_length=1000)
    retrieved_contexts: list[str] = Field(min_length=1, max_length=3)
    positive_response: str = Field(min_length=1, max_length=4000)
    negative_response: str = Field(min_length=1, max_length=4000)
    phase: Literal["selection", "validation"]
    error_type: Literal[
        "number_or_unit",
        "negation",
        "condition_omission",
        "fact_reversal",
        "unsupported_advice",
    ]

    @model_validator(mode="after")
    def validate_pair(self) -> "FaithfulnessCalibrationCase":
        if self.positive_response == self.negative_response:
            raise ValueError("faithfulness calibration responses must differ")
        return self


class FaithfulnessCalibrationSuite(StrictModel):
    calibration_id: str = Field(pattern=r"^faithfulness-zh-v\d+$")
    version: int = Field(ge=1)
    description: str = Field(min_length=1)
    cases: list[FaithfulnessCalibrationCase] = Field(
        min_length=FIXED_CALIBRATION_CASE_COUNT,
        max_length=FIXED_CALIBRATION_CASE_COUNT,
    )

    @model_validator(mode="after")
    def validate_cases(self) -> "FaithfulnessCalibrationSuite":
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("calibration suite contains duplicate case_id")
        source_ids = [case.source_case_id for case in self.cases]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("faithfulness calibration requires distinct sources")
        phase_counts = Counter(case.phase for case in self.cases)
        if phase_counts != {
            "selection": SELECTION_CASE_COUNT,
            "validation": VALIDATION_CASE_COUNT,
        }:
            raise ValueError(
                "faithfulness calibration requires 20 selection and 10 validation cases"
            )
        return self


@dataclass(frozen=True)
class FaithfulnessCalibrationPair:
    case_id: str
    source_case_id: str
    user_input: str
    retrieved_contexts: list[str]
    positive_response: str
    negative_response: str
    phase: Literal["selection", "validation"]
    error_type: str


@dataclass(frozen=True)
class FaithfulnessCalibrationRunAssessment:
    repeat_index: int
    assessment: RagMetricCalibrationAssessment
    passed: bool


@dataclass(frozen=True)
class FaithfulnessThresholdAssessment:
    threshold: float
    runs: tuple[FaithfulnessCalibrationRunAssessment, ...]
    eligible: bool
    worst_positive_pass_rate: float
    worst_negative_rejection_rate: float


@dataclass(frozen=True)
class FaithfulnessTwoStageAssessment:
    candidates: tuple[FaithfulnessThresholdAssessment, ...]
    selected_threshold: float | None
    validation_runs: tuple[FaithfulnessCalibrationRunAssessment, ...]
    passed: bool
    failed_criteria: tuple[str, ...]


def default_faithfulness_calibration_path() -> Path:
    return (
        Path(__file__).resolve().parents[3]
        / "tests"
        / "fixtures"
        / "rag_faithfulness_calibration.v4.json"
    )


def load_faithfulness_calibration_suite(
    path: Path | None = None,
) -> FaithfulnessCalibrationSuite:
    dataset_path = path or default_faithfulness_calibration_path()
    payload = json.loads(dataset_path.read_text(encoding="utf-8"))
    return FaithfulnessCalibrationSuite.model_validate(payload)


def build_faithfulness_calibration_pairs(
    suite: FaithfulnessCalibrationSuite,
) -> list[FaithfulnessCalibrationPair]:
    return [
        FaithfulnessCalibrationPair(
            case_id=case.case_id,
            source_case_id=case.source_case_id,
            user_input=case.user_input,
            retrieved_contexts=case.retrieved_contexts,
            positive_response=case.positive_response,
            negative_response=case.negative_response,
            phase=case.phase,
            error_type=case.error_type,
        )
        for case in suite.cases
    ]


def assess_faithfulness_calibration(
    positive_scores: list[float],
    negative_scores: list[float],
    *,
    threshold: float = DEFAULT_CALIBRATION_THRESHOLD,
) -> RagMetricCalibrationAssessment:
    return assess_rag_metric_calibration(
        positive_scores,
        negative_scores,
        threshold=threshold,
    )


def _assess_stage_run(
    positive_scores: list[float],
    negative_scores: list[float],
    *,
    repeat_index: int,
    threshold: float,
) -> FaithfulnessCalibrationRunAssessment:
    assessment = assess_faithfulness_calibration(
        positive_scores,
        negative_scores,
        threshold=threshold,
    )
    passed = (
        assessment.positive_pass_rate >= MIN_STAGE_CLASSIFICATION_RATE
        and assessment.negative_rejection_rate >= MIN_STAGE_CLASSIFICATION_RATE
    )
    return FaithfulnessCalibrationRunAssessment(
        repeat_index=repeat_index,
        assessment=assessment,
        passed=passed,
    )


def assess_two_stage_faithfulness_calibration(
    *,
    selection_runs: list[tuple[list[float], list[float]]],
    validation_runs: list[tuple[list[float], list[float]]],
) -> FaithfulnessTwoStageAssessment:
    if len(selection_runs) != CALIBRATION_REPEAT_COUNT:
        raise ValueError("faithfulness calibration requires two selection repeats")
    if len(validation_runs) != CALIBRATION_REPEAT_COUNT:
        raise ValueError("faithfulness calibration requires two validation repeats")
    if any(
        len(positive) != SELECTION_CASE_COUNT
        or len(negative) != SELECTION_CASE_COUNT
        for positive, negative in selection_runs
    ):
        raise ValueError("faithfulness selection repeat must contain 20 pairs")
    if any(
        len(positive) != VALIDATION_CASE_COUNT
        or len(negative) != VALIDATION_CASE_COUNT
        for positive, negative in validation_runs
    ):
        raise ValueError("faithfulness validation repeat must contain 10 pairs")

    candidates = []
    for threshold in CANDIDATE_THRESHOLDS:
        runs = tuple(
            _assess_stage_run(
                positive,
                negative,
                repeat_index=index,
                threshold=threshold,
            )
            for index, (positive, negative) in enumerate(selection_runs, start=1)
        )
        candidates.append(
            FaithfulnessThresholdAssessment(
                threshold=threshold,
                runs=runs,
                eligible=all(run.passed for run in runs),
                worst_positive_pass_rate=min(
                    run.assessment.positive_pass_rate for run in runs
                ),
                worst_negative_rejection_rate=min(
                    run.assessment.negative_rejection_rate for run in runs
                ),
            )
        )

    eligible = [candidate for candidate in candidates if candidate.eligible]
    selected = (
        max(
            eligible,
            key=lambda candidate: (
                min(
                    candidate.worst_positive_pass_rate,
                    candidate.worst_negative_rejection_rate,
                ),
                (
                    candidate.worst_positive_pass_rate
                    + candidate.worst_negative_rejection_rate
                ),
                -abs(candidate.threshold - DEFAULT_CALIBRATION_THRESHOLD),
            ),
        )
        if eligible
        else None
    )

    validation_assessments = (
        tuple(
            _assess_stage_run(
                positive,
                negative,
                repeat_index=index,
                threshold=selected.threshold,
            )
            for index, (positive, negative) in enumerate(validation_runs, start=1)
        )
        if selected is not None
        else ()
    )
    failed = []
    if selected is None:
        failed.append("20组定门槛题没有候选值能在两轮中稳定达到90%")
    elif not all(run.passed for run in validation_assessments):
        failed.append(
            f"10组锁定验证题未在两轮中稳定通过{selected.threshold:.2f}门槛"
        )
    return FaithfulnessTwoStageAssessment(
        candidates=tuple(candidates),
        selected_threshold=selected.threshold if selected is not None else None,
        validation_runs=validation_assessments,
        passed=not failed,
        failed_criteria=tuple(failed),
    )


def resolve_faithfulness_policy(
    report: FaithfulnessCalibrationReport | None,
    *,
    model: str,
    ragas_version: str,
) -> FaithfulnessPolicy:
    if report is None:
        return FaithfulnessPolicy(
            status="not_calibrated",
            enforced=False,
            reason="没有可用的中文校准报告，回复忠实度仅展示、不参与通过判定",
        )

    expected_suite = load_faithfulness_calibration_suite()
    mismatches = []
    if (
        report.calibration_id != expected_suite.calibration_id
        or report.dataset_version != expected_suite.version
    ):
        mismatches.append("校准题版本")
    if report.model != model:
        mismatches.append("判官模型")
    if report.ragas_version != ragas_version:
        mismatches.append("RAGAS 版本")
    if mismatches:
        return FaithfulnessPolicy(
            status="not_calibrated",
            enforced=False,
            calibration_run_id=report.run_id,
            reason=(
                f"最近校准的{'、'.join(mismatches)}与本批次不一致，"
                "回复忠实度仅展示、不参与通过判定"
            ),
        )

    complete_result = (
        report.schema_version == 2
        and len(report.candidate_results) == len(CANDIDATE_THRESHOLDS)
        and {item.threshold for item in report.candidate_results}
        == set(CANDIDATE_THRESHOLDS)
        and all(
            len(candidate.runs) == CALIBRATION_REPEAT_COUNT
            for candidate in report.candidate_results
        )
        and any(
            candidate.threshold == report.selected_threshold
            and candidate.eligible
            for candidate in report.candidate_results
        )
        and len(report.validation_results) == CALIBRATION_REPEAT_COUNT
        and all(run.passed for run in report.validation_results)
        and report.status == "completed"
        and report.completed_pair_count == report.base_pair_count
        and report.metrics is not None
        and report.repeat_count == CALIBRATION_REPEAT_COUNT
        and report.selection_case_count == SELECTION_CASE_COUNT
        and report.validation_case_count == VALIDATION_CASE_COUNT
        and report.selected_threshold in CANDIDATE_THRESHOLDS
        and report.threshold == report.selected_threshold
    )
    if not complete_result or report.passed is not True:
        criteria = "；".join(report.failed_criteria)
        reason = "最近一次中文校准未通过"
        if not complete_result:
            reason = "最近一次中文校准未完整完成"
        if criteria:
            reason = f"{reason}：{criteria}"
        return FaithfulnessPolicy(
            status="calibration_failed",
            enforced=False,
            calibration_run_id=report.run_id,
            reason=f"{reason}；回复忠实度仅展示、不参与通过判定",
        )

    return FaithfulnessPolicy(
        status="enforced",
        enforced=True,
        calibration_run_id=report.run_id,
        threshold=report.selected_threshold,
        reason=(
            f"20组定门槛与10组锁定验证均通过，回复忠实度按"
            f"{report.selected_threshold:.2f}参与本批次通过判定"
        ),
    )
