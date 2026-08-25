"""Chinese calibration suite for the context-recall gate.

The recall gate (0.80) is the only enforced RAG threshold that was never
selected from data: it has been carried over as a product default since the
pre-DeepEval era. This module mirrors the faithfulness calibration contract so
the same two-stage selection core can pick the recall threshold from labelled
Chinese pairs instead.

Recall differs from faithfulness in what varies between the two sides of a
pair: the reply is irrelevant here, so the positive and negative sides share
one authoritative reference and differ in the *retrieved contexts*. Positive
contexts are the real chunk the reference was condensed from; negative
contexts are real, topically adjacent chunks that miss at least one required
clause.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.evaluation.rag_metric_calibration import (
    RagMetricCalibrationAssessment,
    RagMetricThresholdSelection,
    assess_rag_metric_calibration,
    select_stable_calibration_threshold,
)


FIXED_CALIBRATION_CASE_COUNT = 30
SELECTION_CASE_COUNT = 20
VALIDATION_CASE_COUNT = 10
CALIBRATION_REPEAT_COUNT = 2
MIN_STAGE_CLASSIFICATION_RATE = 0.90
MIN_MISS_TYPE_COVERAGE = 3

MissType = Literal["wrong_rule", "partial_fact", "adjacent_topic"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContextRecallCalibrationCase(StrictModel):
    case_id: str = Field(pattern=r"^context-recall-[a-z0-9-]+$")
    phase: Literal["selection", "validation"]
    miss_type: MissType
    user_input: str = Field(min_length=1, max_length=1000)
    reference: str = Field(min_length=1, max_length=2000)
    positive_contexts: list[str] = Field(min_length=1, max_length=3)
    negative_contexts: list[str] = Field(min_length=1, max_length=3)
    positive_sources: list[str] = Field(min_length=1, max_length=3)
    negative_sources: list[str] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def validate_pair(self) -> "ContextRecallCalibrationCase":
        if self.positive_contexts == self.negative_contexts:
            raise ValueError("recall calibration sides must use different contexts")
        if len(self.positive_sources) != len(self.positive_contexts):
            raise ValueError("positive sources must match positive contexts")
        if len(self.negative_sources) != len(self.negative_contexts):
            raise ValueError("negative sources must match negative contexts")
        return self


class ContextRecallCalibrationSuite(StrictModel):
    calibration_id: str = Field(pattern=r"^context-recall-zh-v\d+$")
    version: int = Field(ge=1)
    description: str = Field(min_length=1)
    cases: list[ContextRecallCalibrationCase] = Field(
        min_length=FIXED_CALIBRATION_CASE_COUNT,
        max_length=FIXED_CALIBRATION_CASE_COUNT,
    )

    @model_validator(mode="after")
    def validate_cases(self) -> "ContextRecallCalibrationSuite":
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("calibration suite contains duplicate case_id")
        phase_counts = Counter(case.phase for case in self.cases)
        if phase_counts != {
            "selection": SELECTION_CASE_COUNT,
            "validation": VALIDATION_CASE_COUNT,
        }:
            raise ValueError(
                "context recall calibration requires 20 selection and 10 validation cases"
            )
        if len({case.miss_type for case in self.cases}) < MIN_MISS_TYPE_COVERAGE:
            raise ValueError("context recall calibration lacks miss-type coverage")
        return self


@dataclass(frozen=True)
class ContextRecallCalibrationPair:
    case_id: str
    user_input: str
    reference: str
    positive_contexts: list[str]
    negative_contexts: list[str]
    phase: Literal["selection", "validation"]
    error_type: str


@dataclass(frozen=True)
class ContextRecallCalibrationRunAssessment:
    repeat_index: int
    assessment: RagMetricCalibrationAssessment
    passed: bool


@dataclass(frozen=True)
class ContextRecallTwoStageAssessment:
    threshold_selection: RagMetricThresholdSelection | None
    selection_runs: tuple[ContextRecallCalibrationRunAssessment, ...]
    selected_threshold: float | None
    validation_runs: tuple[ContextRecallCalibrationRunAssessment, ...]
    passed: bool
    failed_criteria: tuple[str, ...]


def default_context_recall_calibration_path() -> Path:
    return (
        Path(__file__).resolve().parents[3]
        / "tests"
        / "fixtures"
        / "rag_context_recall_calibration.v1.json"
    )


def load_context_recall_calibration_suite(
    path: Path | None = None,
) -> ContextRecallCalibrationSuite:
    dataset_path = path or default_context_recall_calibration_path()
    payload = json.loads(dataset_path.read_text(encoding="utf-8"))
    return ContextRecallCalibrationSuite.model_validate(payload)


def build_context_recall_calibration_pairs(
    suite: ContextRecallCalibrationSuite,
) -> list[ContextRecallCalibrationPair]:
    return [
        ContextRecallCalibrationPair(
            case_id=case.case_id,
            user_input=case.user_input,
            reference=case.reference,
            positive_contexts=list(case.positive_contexts),
            negative_contexts=list(case.negative_contexts),
            phase=case.phase,
            error_type=case.miss_type,
        )
        for case in suite.cases
    ]


def assess_context_recall_calibration(
    positive_scores: list[float],
    negative_scores: list[float],
    *,
    threshold: float,
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
) -> ContextRecallCalibrationRunAssessment:
    assessment = assess_context_recall_calibration(
        positive_scores,
        negative_scores,
        threshold=threshold,
    )
    passed = (
        assessment.positive_pass_rate >= MIN_STAGE_CLASSIFICATION_RATE
        and assessment.negative_rejection_rate >= MIN_STAGE_CLASSIFICATION_RATE
    )
    return ContextRecallCalibrationRunAssessment(
        repeat_index=repeat_index,
        assessment=assessment,
        passed=passed,
    )


def assess_two_stage_context_recall_calibration(
    *,
    selection_runs: list[tuple[list[float], list[float]]],
    validation_runs: list[tuple[list[float], list[float]]],
) -> ContextRecallTwoStageAssessment:
    if len(selection_runs) != CALIBRATION_REPEAT_COUNT:
        raise ValueError("context recall calibration requires two selection repeats")
    if len(validation_runs) != CALIBRATION_REPEAT_COUNT:
        raise ValueError("context recall calibration requires two validation repeats")
    if any(
        len(positive) != SELECTION_CASE_COUNT or len(negative) != SELECTION_CASE_COUNT
        for positive, negative in selection_runs
    ):
        raise ValueError("context recall selection repeat must contain 20 pairs")
    if any(
        len(positive) != VALIDATION_CASE_COUNT or len(negative) != VALIDATION_CASE_COUNT
        for positive, negative in validation_runs
    ):
        raise ValueError("context recall validation repeat must contain 10 pairs")

    threshold_selection = select_stable_calibration_threshold(
        selection_runs,
        minimum_classification_rate=MIN_STAGE_CLASSIFICATION_RATE,
    )
    selected_threshold = (
        threshold_selection.selected_threshold if threshold_selection else None
    )
    selection_assessments = (
        tuple(
            _assess_stage_run(
                positive,
                negative,
                repeat_index=index,
                threshold=selected_threshold,
            )
            for index, (positive, negative) in enumerate(selection_runs, start=1)
        )
        if selected_threshold is not None
        else ()
    )
    validation_assessments = (
        tuple(
            _assess_stage_run(
                positive,
                negative,
                repeat_index=index,
                threshold=selected_threshold,
            )
            for index, (positive, negative) in enumerate(validation_runs, start=1)
        )
        if selected_threshold is not None
        else ()
    )
    failed: list[str] = []
    if selected_threshold is None:
        failed.append("20组定门槛题没有两轮共同的90%可用范围")
    elif not all(run.passed for run in validation_assessments):
        failed.append(
            f"10组锁定验证题未在两轮中稳定通过{selected_threshold:.3f}门槛"
        )
    return ContextRecallTwoStageAssessment(
        threshold_selection=threshold_selection,
        selection_runs=selection_assessments,
        selected_threshold=selected_threshold,
        validation_runs=validation_assessments,
        passed=not failed,
        failed_criteria=tuple(failed),
    )
