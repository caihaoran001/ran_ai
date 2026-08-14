from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.evaluation.rag_cases import load_rag_eval_suite
from app.evaluation.rag_metric_calibration import (
    RagMetricCalibrationAssessment,
    RagMetricThresholdSelection,
    assess_rag_metric_calibration,
    select_stable_calibration_threshold,
)
from app.schemas.rag_evaluation import (
    AnswerRelevancyCalibrationReport,
    AnswerRelevancyPolicy,
)


FIXED_CALIBRATION_CASE_COUNT = 30
SELECTION_CASE_COUNT = 20
VALIDATION_CASE_COUNT = 10
CALIBRATION_REPEAT_COUNT = 2
MIN_STAGE_CLASSIFICATION_RATE = 0.90


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AnswerRelevancyCalibrationCase(StrictModel):
    case_id: str = Field(pattern=r"^relevancy-[a-z0-9-]+$")
    source_case_id: str = Field(pattern=r"^rag-(faq|chat)-[a-z0-9-]+$")
    negative_source_case_id: str = Field(pattern=r"^rag-(faq|chat)-[a-z0-9-]+$")
    phase: Literal["selection", "validation"]


class AnswerRelevancyCalibrationSuite(StrictModel):
    calibration_id: str = Field(pattern=r"^answer-relevancy-zh-v\d+$")
    version: int = Field(ge=1)
    description: str = Field(min_length=1)
    cases: list[AnswerRelevancyCalibrationCase] = Field(
        min_length=FIXED_CALIBRATION_CASE_COUNT,
        max_length=FIXED_CALIBRATION_CASE_COUNT,
    )

    @model_validator(mode="after")
    def validate_cases(self) -> "AnswerRelevancyCalibrationSuite":
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("calibration suite contains duplicate case_id")
        source_ids = [case.source_case_id for case in self.cases]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("calibration suite requires distinct source cases")
        source_set = set(source_ids)
        negative_sources = [case.negative_source_case_id for case in self.cases]
        if set(negative_sources) != source_set or len(negative_sources) != len(
            set(negative_sources)
        ):
            raise ValueError("calibration negatives must be a one-to-one answer swap")
        if any(
            case.source_case_id == case.negative_source_case_id for case in self.cases
        ):
            raise ValueError("calibration negative answer cannot come from itself")
        phase_counts = Counter(case.phase for case in self.cases)
        if phase_counts != {
            "selection": SELECTION_CASE_COUNT,
            "validation": VALIDATION_CASE_COUNT,
        }:
            raise ValueError(
                "answer relevancy calibration requires 20 selection and 10 validation cases"
            )
        return self


@dataclass(frozen=True)
class AnswerRelevancyCalibrationPair:
    case_id: str
    source_case_id: str
    negative_source_case_id: str
    user_input: str
    positive_response: str
    negative_response: str
    phase: Literal["selection", "validation"]


@dataclass(frozen=True)
class AnswerRelevancyCalibrationRunAssessment:
    repeat_index: int
    assessment: RagMetricCalibrationAssessment
    passed: bool


@dataclass(frozen=True)
class AnswerRelevancyTwoStageAssessment:
    threshold_selection: RagMetricThresholdSelection | None
    selection_runs: tuple[AnswerRelevancyCalibrationRunAssessment, ...]
    selected_threshold: float | None
    validation_runs: tuple[AnswerRelevancyCalibrationRunAssessment, ...]
    passed: bool
    failed_criteria: tuple[str, ...]


def default_answer_relevancy_calibration_path() -> Path:
    return (
        Path(__file__).resolve().parents[3]
        / "tests"
        / "fixtures"
        / "rag_answer_relevancy_calibration.v3.json"
    )


def load_answer_relevancy_calibration_suite(
    path: Path | None = None,
) -> AnswerRelevancyCalibrationSuite:
    dataset_path = path or default_answer_relevancy_calibration_path()
    payload = json.loads(dataset_path.read_text(encoding="utf-8"))
    return AnswerRelevancyCalibrationSuite.model_validate(payload)


def _reference_response(rag_case) -> str:
    required_fact_ids = (
        rag_case.answer.required_fact_ids or rag_case.retrieval.required_fact_ids
    )
    facts = {fact.fact_id: fact.statement for fact in rag_case.authoritative_facts}
    return "\n".join(facts[fact_id] for fact_id in required_fact_ids)


def build_answer_relevancy_calibration_pairs(
    suite: AnswerRelevancyCalibrationSuite,
) -> list[AnswerRelevancyCalibrationPair]:
    rag_cases = {case.case_id: case for case in load_rag_eval_suite().cases}
    referenced_ids = {
        source_id
        for case in suite.cases
        for source_id in (case.source_case_id, case.negative_source_case_id)
    }
    missing_source_ids = sorted(referenced_ids - set(rag_cases))
    if missing_source_ids:
        raise ValueError(
            "answer relevancy calibration references unknown RAG cases: "
            + ", ".join(missing_source_ids)
        )
    return [
        AnswerRelevancyCalibrationPair(
            case_id=case.case_id,
            source_case_id=case.source_case_id,
            negative_source_case_id=case.negative_source_case_id,
            user_input=rag_cases[case.source_case_id].user_query,
            positive_response=_reference_response(rag_cases[case.source_case_id]),
            negative_response=_reference_response(
                rag_cases[case.negative_source_case_id]
            ),
            phase=case.phase,
        )
        for case in suite.cases
    ]


def assess_answer_relevancy_calibration(
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
) -> AnswerRelevancyCalibrationRunAssessment:
    assessment = assess_answer_relevancy_calibration(
        positive_scores,
        negative_scores,
        threshold=threshold,
    )
    passed = (
        assessment.positive_pass_rate >= MIN_STAGE_CLASSIFICATION_RATE
        and assessment.negative_rejection_rate >= MIN_STAGE_CLASSIFICATION_RATE
    )
    return AnswerRelevancyCalibrationRunAssessment(
        repeat_index=repeat_index,
        assessment=assessment,
        passed=passed,
    )


def assess_two_stage_answer_relevancy_calibration(
    *,
    selection_runs: list[tuple[list[float], list[float]]],
    validation_runs: list[tuple[list[float], list[float]]],
) -> AnswerRelevancyTwoStageAssessment:
    if len(selection_runs) != CALIBRATION_REPEAT_COUNT:
        raise ValueError("answer relevancy calibration requires two selection repeats")
    if len(validation_runs) != CALIBRATION_REPEAT_COUNT:
        raise ValueError("answer relevancy calibration requires two validation repeats")
    if any(
        len(positive) != SELECTION_CASE_COUNT
        or len(negative) != SELECTION_CASE_COUNT
        for positive, negative in selection_runs
    ):
        raise ValueError("answer relevancy selection repeat must contain 20 pairs")
    if any(
        len(positive) != VALIDATION_CASE_COUNT
        or len(negative) != VALIDATION_CASE_COUNT
        for positive, negative in validation_runs
    ):
        raise ValueError("answer relevancy validation repeat must contain 10 pairs")

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
    failed = []
    if selected_threshold is None:
        failed.append("20组定门槛题没有两轮共同的90%可用范围")
    elif not all(run.passed for run in validation_assessments):
        failed.append(
            f"10组锁定验证题未在两轮中稳定通过{selected_threshold:.3f}门槛"
        )
    return AnswerRelevancyTwoStageAssessment(
        threshold_selection=threshold_selection,
        selection_runs=selection_assessments,
        selected_threshold=selected_threshold,
        validation_runs=validation_assessments,
        passed=not failed,
        failed_criteria=tuple(failed),
    )


def resolve_answer_relevancy_policy(
    report: AnswerRelevancyCalibrationReport | None,
    *,
    model: str,
    embedding_model: str,
    ragas_version: str,
) -> AnswerRelevancyPolicy:
    if report is None:
        return AnswerRelevancyPolicy(
            status="not_calibrated",
            enforced=False,
            reason="没有可用的中文校准报告，回复相关性仅展示、不参与通过判定",
        )

    expected_suite = load_answer_relevancy_calibration_suite()
    mismatches = []
    if (
        report.calibration_id != expected_suite.calibration_id
        or report.dataset_version != expected_suite.version
    ):
        mismatches.append("校准题版本")
    if report.model != model:
        mismatches.append("判官模型")
    if report.embedding_model != embedding_model:
        mismatches.append("向量模型")
    if report.ragas_version != ragas_version:
        mismatches.append("RAGAS 版本")
    if mismatches:
        return AnswerRelevancyPolicy(
            status="not_calibrated",
            enforced=False,
            calibration_run_id=report.run_id,
            reason=(
                f"最近校准的{'、'.join(mismatches)}与本批次不一致，"
                "回复相关性仅展示、不参与通过判定"
            ),
        )

    complete_result = (
        report.schema_version == 2
        and report.threshold_selection is not None
        and report.threshold_selection.selected_threshold == report.selected_threshold
        and report.threshold_selection.selected_threshold == report.threshold
        and report.threshold_selection.minimum_classification_rate
        == MIN_STAGE_CLASSIFICATION_RATE
        and len(report.selection_results) == CALIBRATION_REPEAT_COUNT
        and all(run.passed for run in report.selection_results)
        and len(report.validation_results) == CALIBRATION_REPEAT_COUNT
        and all(run.passed for run in report.validation_results)
        and report.status == "completed"
        and report.completed_pair_count == report.base_pair_count
        and report.metrics is not None
        and report.repeat_count == CALIBRATION_REPEAT_COUNT
        and report.selection_case_count == SELECTION_CASE_COUNT
        and report.validation_case_count == VALIDATION_CASE_COUNT
        and report.threshold == report.selected_threshold
    )
    if not complete_result or report.passed is not True:
        criteria = "；".join(report.failed_criteria)
        reason = "最近一次中文校准未通过"
        if not complete_result:
            reason = "最近一次中文校准未完整完成"
        if criteria:
            reason = f"{reason}：{criteria}"
        return AnswerRelevancyPolicy(
            status="calibration_failed",
            enforced=False,
            calibration_run_id=report.run_id,
            reason=f"{reason}；回复相关性仅展示、不参与通过判定",
        )

    return AnswerRelevancyPolicy(
        status="enforced",
        enforced=True,
        calibration_run_id=report.run_id,
        threshold=report.selected_threshold,
        reason=(
            f"20组定门槛与10组锁定验证均通过，回复相关性按"
            f"{report.selected_threshold:.3f}参与本批次通过判定"
        ),
    )
