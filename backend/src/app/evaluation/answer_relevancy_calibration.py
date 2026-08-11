from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.rag_evaluation import (
    AnswerRelevancyCalibrationReport,
    AnswerRelevancyPolicy,
)


CALIBRATION_THRESHOLD = 0.80
MIN_AUC = 0.90
MIN_PAIRED_ACCURACY = 0.80
MIN_POSITIVE_PASS_RATE = 0.80
MIN_NEGATIVE_REJECTION_RATE = 0.80
MIN_MEAN_GAP = 0.20
FIXED_CALIBRATION_CASE_COUNT = 10


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AnswerRelevancyCalibrationCase(StrictModel):
    case_id: str = Field(pattern=r"^relevancy-[a-z0-9-]+$")
    user_input: str = Field(min_length=1, max_length=1000)
    positive_response: str = Field(min_length=1, max_length=4000)
    negative_source_case_id: str = Field(pattern=r"^relevancy-[a-z0-9-]+$")


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
        known = set(case_ids)
        for case in self.cases:
            if case.negative_source_case_id == case.case_id:
                raise ValueError("calibration negative answer cannot come from itself")
            if case.negative_source_case_id not in known:
                raise ValueError("calibration negative source does not exist")
        negative_sources = [case.negative_source_case_id for case in self.cases]
        if set(negative_sources) != known or len(negative_sources) != len(
            set(negative_sources)
        ):
            raise ValueError("calibration negatives must be a one-to-one answer swap")
        return self


@dataclass(frozen=True)
class AnswerRelevancyCalibrationPair:
    case_id: str
    negative_source_case_id: str
    user_input: str
    positive_response: str
    negative_response: str


@dataclass(frozen=True)
class AnswerRelevancyCalibrationAssessment:
    positive_mean: float
    negative_mean: float
    mean_gap: float
    auc: float
    paired_accuracy: float
    positive_pass_rate: float
    negative_rejection_rate: float
    passed: bool
    failed_criteria: tuple[str, ...]


def default_answer_relevancy_calibration_path() -> Path:
    return (
        Path(__file__).resolve().parents[3]
        / "tests"
        / "fixtures"
        / "rag_answer_relevancy_calibration.v1.json"
    )


def load_answer_relevancy_calibration_suite(
    path: Path | None = None,
) -> AnswerRelevancyCalibrationSuite:
    dataset_path = path or default_answer_relevancy_calibration_path()
    payload = json.loads(dataset_path.read_text(encoding="utf-8"))
    return AnswerRelevancyCalibrationSuite.model_validate(payload)


def build_answer_relevancy_calibration_pairs(
    suite: AnswerRelevancyCalibrationSuite,
) -> list[AnswerRelevancyCalibrationPair]:
    responses = {case.case_id: case.positive_response for case in suite.cases}
    return [
        AnswerRelevancyCalibrationPair(
            case_id=case.case_id,
            negative_source_case_id=case.negative_source_case_id,
            user_input=case.user_input,
            positive_response=case.positive_response,
            negative_response=responses[case.negative_source_case_id],
        )
        for case in suite.cases
    ]


def _auc(positive_scores: list[float], negative_scores: list[float]) -> float:
    comparisons = 0.0
    for positive in positive_scores:
        for negative in negative_scores:
            if positive > negative:
                comparisons += 1.0
            elif positive == negative:
                comparisons += 0.5
    return comparisons / (len(positive_scores) * len(negative_scores))


def assess_answer_relevancy_calibration(
    positive_scores: list[float],
    negative_scores: list[float],
    *,
    threshold: float = CALIBRATION_THRESHOLD,
) -> AnswerRelevancyCalibrationAssessment:
    if not positive_scores or len(positive_scores) != len(negative_scores):
        raise ValueError(
            "calibration requires equal non-empty positive and negative scores"
        )
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("calibration threshold must be between 0 and 1")
    if any(not 0.0 <= score <= 1.0 for score in positive_scores + negative_scores):
        raise ValueError("calibration scores must be between 0 and 1")

    positive_mean = sum(positive_scores) / len(positive_scores)
    negative_mean = sum(negative_scores) / len(negative_scores)
    mean_gap = positive_mean - negative_mean
    auc = _auc(positive_scores, negative_scores)
    paired_accuracy = sum(
        positive > negative
        for positive, negative in zip(positive_scores, negative_scores, strict=True)
    ) / len(positive_scores)
    positive_pass_rate = sum(score >= threshold for score in positive_scores) / len(
        positive_scores
    )
    negative_rejection_rate = sum(score < threshold for score in negative_scores) / len(
        negative_scores
    )

    checks = {
        f"AUC低于{MIN_AUC:.2f}": auc >= MIN_AUC,
        f"正负配对准确率低于{MIN_PAIRED_ACCURACY:.2f}": (
            paired_accuracy >= MIN_PAIRED_ACCURACY
        ),
        f"正例在{threshold:.2f}门槛下通过率低于{MIN_POSITIVE_PASS_RATE:.2f}": (
            positive_pass_rate >= MIN_POSITIVE_PASS_RATE
        ),
        f"负例在{threshold:.2f}门槛下拒绝率低于{MIN_NEGATIVE_REJECTION_RATE:.2f}": (
            negative_rejection_rate >= MIN_NEGATIVE_REJECTION_RATE
        ),
        f"正负均值差低于{MIN_MEAN_GAP:.2f}": mean_gap >= MIN_MEAN_GAP,
    }
    failed = tuple(label for label, passed in checks.items() if not passed)
    return AnswerRelevancyCalibrationAssessment(
        positive_mean=positive_mean,
        negative_mean=negative_mean,
        mean_gap=mean_gap,
        auc=auc,
        paired_accuracy=paired_accuracy,
        positive_pass_rate=positive_pass_rate,
        negative_rejection_rate=negative_rejection_rate,
        passed=not failed,
        failed_criteria=failed,
    )


def resolve_answer_relevancy_policy(
    report: AnswerRelevancyCalibrationReport | None,
    *,
    model: str,
    embedding_model: str,
    ragas_version: str,
    threshold: float = CALIBRATION_THRESHOLD,
) -> AnswerRelevancyPolicy:
    if report is None:
        return AnswerRelevancyPolicy(
            status="not_calibrated",
            enforced=False,
            reason="没有可用的中文校准报告，回复相关性仅展示、不参与通过判定",
        )

    mismatches = []
    if report.model != model:
        mismatches.append("判官模型")
    if report.embedding_model != embedding_model:
        mismatches.append("向量模型")
    if report.ragas_version != ragas_version:
        mismatches.append("RAGAS 版本")
    if report.threshold != threshold:
        mismatches.append("判定门槛")
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
        report.status == "completed"
        and report.completed_pair_count == report.base_pair_count
        and report.metrics is not None
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
        reason="中文正负样本校准通过，回复相关性参与本批次通过判定",
    )
