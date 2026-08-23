from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.agent_evaluation import (
    AgentCaseExpected,
    AgentEvaluationHistoryMessage,
    AgentToolCallEvidence,
)


TASK_COMPLETION_SELECTION_CASES = 8
TASK_COMPLETION_VALIDATION_CASES = 4
TASK_COMPLETION_REPEAT_COUNT = 2
REWRITE_HARM_CASES = 6
MIN_CALIBRATION_CLASSIFICATION_RATE = 0.90


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskCompletionCalibrationCase(StrictModel):
    case_id: str = Field(pattern=r"^task-completion-[a-z0-9-]+$")
    phase: Literal["selection", "validation"]
    error_type: Literal[
        "wrong_fact",
        "missing_fact",
        "wrong_response_mode",
        "unsupported_claim",
        "unnecessary_clarification",
    ]
    history: list[AgentEvaluationHistoryMessage] = Field(default_factory=list)
    user_query: str = Field(min_length=1, max_length=4000)
    expected: AgentCaseExpected
    tool_calls: list[AgentToolCallEvidence] = Field(default_factory=list)
    positive_response: str = Field(min_length=1, max_length=20000)
    negative_response: str = Field(min_length=1, max_length=20000)

    @model_validator(mode="after")
    def validate_pair(self) -> "TaskCompletionCalibrationCase":
        if self.positive_response == self.negative_response:
            raise ValueError("task completion calibration responses must differ")
        return self


class RewriteHarmCalibrationCase(StrictModel):
    case_id: str = Field(pattern=r"^rewrite-harm-[a-z0-9-]+$")
    original_query: str = Field(min_length=1, max_length=1000)
    rewritten_query: str | None = Field(default=None, max_length=1000)
    raw_hit_count: int = Field(ge=0, le=20)
    rewritten_hit_count: int = Field(ge=0, le=20)
    raw_recall: float | None = Field(default=None, ge=0.0, le=1.0)
    rewritten_recall: float | None = Field(default=None, ge=0.0, le=1.0)
    expected_applicable: bool
    expected_harmed: bool
    expected_signal: Literal[
        "not_rewritten",
        "hit_collapse",
        "missing_recall_scores",
        "recall_drop",
        "no_detected_harm",
    ]


class ReplyAgentRulerCalibrationSuite(StrictModel):
    calibration_id: Literal["reply-agent-rulers-zh-v1"]
    version: Literal[1]
    description: str = Field(min_length=1, max_length=1000)
    task_completion_cases: list[TaskCompletionCalibrationCase] = Field(
        min_length=TASK_COMPLETION_SELECTION_CASES
        + TASK_COMPLETION_VALIDATION_CASES,
        max_length=TASK_COMPLETION_SELECTION_CASES
        + TASK_COMPLETION_VALIDATION_CASES,
    )
    rewrite_harm_cases: list[RewriteHarmCalibrationCase] = Field(
        min_length=REWRITE_HARM_CASES,
        max_length=REWRITE_HARM_CASES,
    )

    @model_validator(mode="after")
    def validate_suite(self) -> "ReplyAgentRulerCalibrationSuite":
        task_ids = [item.case_id for item in self.task_completion_cases]
        rewrite_ids = [item.case_id for item in self.rewrite_harm_cases]
        if len(task_ids) != len(set(task_ids)) or len(rewrite_ids) != len(
            set(rewrite_ids)
        ):
            raise ValueError("calibration case ids must be unique")
        phases = Counter(item.phase for item in self.task_completion_cases)
        expected = {
            "selection": TASK_COMPLETION_SELECTION_CASES,
            "validation": TASK_COMPLETION_VALIDATION_CASES,
        }
        if phases != expected:
            raise ValueError(f"task completion phase counts must equal {expected}")
        if len({item.error_type for item in self.task_completion_cases}) < 4:
            raise ValueError("task completion calibration lacks error-type coverage")
        return self


def default_reply_agent_ruler_calibration_path() -> Path:
    return (
        Path(__file__).resolve().parents[3]
        / "tests"
        / "fixtures"
        / "reply_agent_ruler_calibration.v1.json"
    )


def load_reply_agent_ruler_calibration_suite(
    path: Path | None = None,
) -> ReplyAgentRulerCalibrationSuite:
    dataset_path = path or default_reply_agent_ruler_calibration_path()
    return ReplyAgentRulerCalibrationSuite.model_validate(
        json.loads(dataset_path.read_text(encoding="utf-8"))
    )
