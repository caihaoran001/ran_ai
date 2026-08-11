from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.evaluation.rag_metric_calibration import (
    RagMetricCalibrationAssessment,
    assess_rag_metric_calibration,
)
from app.schemas.rag_evaluation import (
    FaithfulnessCalibrationReport,
    FaithfulnessPolicy,
)


CALIBRATION_THRESHOLD = 0.85
FIXED_CALIBRATION_CASE_COUNT = 10


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FaithfulnessCalibrationCase(StrictModel):
    case_id: str = Field(pattern=r"^faithfulness-[a-z0-9-]+$")
    source_case_id: str = Field(pattern=r"^rag-(faq|chat)-[a-z0-9-]+$")
    user_input: str = Field(min_length=1, max_length=1000)
    retrieved_contexts: list[str] = Field(min_length=1, max_length=3)
    positive_response: str = Field(min_length=1, max_length=4000)
    negative_response: str = Field(min_length=1, max_length=4000)

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
        return self


@dataclass(frozen=True)
class FaithfulnessCalibrationPair:
    case_id: str
    source_case_id: str
    user_input: str
    retrieved_contexts: list[str]
    positive_response: str
    negative_response: str


def default_faithfulness_calibration_path() -> Path:
    return (
        Path(__file__).resolve().parents[3]
        / "tests"
        / "fixtures"
        / "rag_faithfulness_calibration.v1.json"
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
        )
        for case in suite.cases
    ]


def assess_faithfulness_calibration(
    positive_scores: list[float],
    negative_scores: list[float],
    *,
    threshold: float = CALIBRATION_THRESHOLD,
) -> RagMetricCalibrationAssessment:
    return assess_rag_metric_calibration(
        positive_scores,
        negative_scores,
        threshold=threshold,
    )


def resolve_faithfulness_policy(
    report: FaithfulnessCalibrationReport | None,
    *,
    model: str,
    ragas_version: str,
    threshold: float = CALIBRATION_THRESHOLD,
) -> FaithfulnessPolicy:
    if report is None:
        return FaithfulnessPolicy(
            status="not_calibrated",
            enforced=False,
            reason="没有可用的中文校准报告，回复忠实度仅展示、不参与通过判定",
        )

    mismatches = []
    if report.model != model:
        mismatches.append("判官模型")
    if report.ragas_version != ragas_version:
        mismatches.append("RAGAS 版本")
    if report.threshold != threshold:
        mismatches.append("判定门槛")
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
        reason="中文正负样本校准通过，回复忠实度参与本批次通过判定",
    )
