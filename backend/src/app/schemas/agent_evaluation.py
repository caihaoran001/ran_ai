from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentEvaluationCounts(StrictModel):
    total: int = Field(ge=0)
    completed: int = Field(ge=0)
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    errors: int = Field(ge=0)


class AgentRateMetric(StrictModel):
    value: float | None = Field(default=None, ge=0.0, le=1.0)
    numerator: int = Field(default=0, ge=0)
    denominator: int = Field(default=0, ge=0)


class AgentEvaluationMetrics(StrictModel):
    task_pass_rate: AgentRateMetric
    tool_selection_accuracy: AgentRateMetric
    argument_accuracy: AgentRateMetric
    unauthorized_action_rate: AgentRateMetric
    literal_constraint_pass_rate: AgentRateMetric
    judge_pass_rate: AgentRateMetric
    judge_task_completion_rate: AgentRateMetric
    judge_groundedness_rate: AgentRateMetric
    judge_clarification_rate: AgentRateMetric
    judge_expression_rate: AgentRateMetric


class AgentJudgePolicy(StrictModel):
    status: Literal["mock", "observational", "enforced"]
    enforced: bool
    model: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=1000)


class AgentEvaluationModelCall(StrictModel):
    component: Literal["agent", "judge"]
    metric_key: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_]{1,119}$",
    )
    call_index: int = Field(ge=1)
    model: str = Field(min_length=1, max_length=120)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    prompt_cache_hit_tokens: int | None = Field(default=None, ge=0)
    estimated_cost_yuan: float | None = Field(default=None, ge=0)
    elapsed_ms: int = Field(default=0, ge=0)
    success: bool
    error_type: str | None = Field(default=None, max_length=120)


class AgentEvaluationUsageTotals(StrictModel):
    call_count: int = Field(default=0, ge=0)
    successful_calls: int = Field(default=0, ge=0)
    failed_calls: int = Field(default=0, ge=0)
    usage_reported_calls: int = Field(default=0, ge=0)
    costed_calls: int = Field(default=0, ge=0)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    prompt_cache_hit_tokens: int = Field(default=0, ge=0)
    known_estimated_cost_yuan: float = Field(default=0, ge=0)
    estimated_cost_yuan: float | None = Field(default=0, ge=0)
    complete: bool = True


class AgentEvaluationUsageSummary(StrictModel):
    tracking_available: bool = False
    currency: Literal["CNY"] = "CNY"
    is_estimate: Literal[True] = True
    agent: AgentEvaluationUsageTotals = Field(
        default_factory=AgentEvaluationUsageTotals
    )
    judge: AgentEvaluationUsageTotals = Field(
        default_factory=AgentEvaluationUsageTotals
    )
    total: AgentEvaluationUsageTotals = Field(
        default_factory=AgentEvaluationUsageTotals
    )


class AgentEvaluationCaseUsage(AgentEvaluationUsageSummary):
    calls: list[AgentEvaluationModelCall] = Field(default_factory=list)


class AgentEvaluationSummary(StrictModel):
    schema_version: Literal[1] = 1
    run_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
    run_mode: Literal["mock", "real"]
    status: Literal["running", "completed", "failed", "interrupted"]
    started_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None
    git_sha: str = Field(min_length=7, max_length=64)
    dataset_id: str = Field(min_length=1, max_length=120)
    dataset_version: int = Field(ge=1)
    repeat_count: int = Field(ge=1, le=10)
    model: str = Field(min_length=1, max_length=120)
    evaluation_framework: Literal["legacy_custom", "deepeval"] = "legacy_custom"
    evaluation_framework_version: str | None = Field(
        default=None,
        min_length=1,
        max_length=40,
    )
    judge_policy: AgentJudgePolicy
    counts: AgentEvaluationCounts
    metrics: AgentEvaluationMetrics
    usage: AgentEvaluationUsageSummary = Field(
        default_factory=AgentEvaluationUsageSummary
    )
    estimated_cost_yuan: float | None = Field(default=None, ge=0)
    error: str | None = Field(default=None, max_length=2000)


class AgentEvaluationHistoryMessage(StrictModel):
    role: Literal["user", "staff", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class AgentExpectedToolCall(StrictModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{1,119}$")
    args_subset: dict[str, Any] = Field(default_factory=dict)
    min_calls: int = Field(default=1, ge=1, le=10)
    max_calls: int = Field(default=1, ge=1, le=10)

    @model_validator(mode="after")
    def validate_call_bounds(self) -> "AgentExpectedToolCall":
        if self.min_calls > self.max_calls:
            raise ValueError("min_calls must be less than or equal to max_calls")
        return self


class AgentCaseExpected(StrictModel):
    response_mode: Literal["reply", "clarify", "no_reply"]
    required_tools: list[AgentExpectedToolCall] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    allow_additional_tools: bool = False
    required_literals: list[str] = Field(default_factory=list)
    forbidden_literals: list[str] = Field(default_factory=list)
    required_facts: list[str] = Field(default_factory=list)
    forbidden_claims: list[str] = Field(default_factory=list)
    clarification_requirement: str | None = Field(default=None, max_length=1000)


class AgentToolCallEvidence(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    args: dict[str, Any] = Field(default_factory=dict)
    result: Any = None
    ok: bool
    error: str | None = Field(default=None, max_length=2000)
    elapsed_ms: int = Field(default=0, ge=0)


class AgentCaseActual(StrictModel):
    response_mode: Literal["reply", "no_reply", "unavailable"]
    response: str | None = Field(default=None, max_length=20000)
    tool_calls: list[AgentToolCallEvidence] = Field(default_factory=list)


class AgentDeterministicCheck(StrictModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{1,119}$")
    label: str = Field(min_length=1, max_length=200)
    passed: bool
    message: str = Field(min_length=1, max_length=2000)
    severity: Literal["info", "warning", "critical"] = "warning"


class AgentJudgeDimension(StrictModel):
    key: Literal[
        "task_completion",
        "groundedness",
        "clarification",
        "expression",
    ]
    passed: bool | None = None
    reason: str = Field(min_length=1, max_length=2000)
    evidence: str | None = Field(default=None, max_length=2000)


class AgentFrameworkMetric(StrictModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,119}$")
    label: str = Field(min_length=1, max_length=200)
    kind: Literal["deterministic", "llm"]
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    passed: bool | None = None
    reason: str | None = Field(default=None, max_length=4000)


class AgentJudgeResult(StrictModel):
    status: Literal["mock", "completed", "skipped", "error"]
    passed: bool | None = None
    dimensions: list[AgentJudgeDimension] = Field(default_factory=list)
    framework_metrics: list[AgentFrameworkMetric] = Field(default_factory=list)
    error: str | None = Field(default=None, max_length=2000)


class AgentFailureReason(StrictModel):
    stage: Literal["deterministic", "judge", "runtime"]
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{1,119}$")
    message: str = Field(min_length=1, max_length=2000)
    severity: Literal["warning", "critical"] = "warning"


class AgentEvaluationCaseResult(StrictModel):
    case_id: str = Field(pattern=r"^agent-[a-z0-9-]+$")
    title: str = Field(min_length=1, max_length=200)
    origin: Literal["production_derived", "boundary"]
    scenario: str = Field(pattern=r"^[a-z][a-z0-9_]{1,79}$")
    tags: list[str] = Field(default_factory=list)
    round_index: int = Field(ge=1, le=10)
    status: Literal["passed", "failed", "error"]
    history: list[AgentEvaluationHistoryMessage] = Field(default_factory=list)
    user_query: str = Field(min_length=1, max_length=4000)
    expected: AgentCaseExpected
    actual: AgentCaseActual
    deterministic_checks: list[AgentDeterministicCheck] = Field(default_factory=list)
    judge: AgentJudgeResult
    failure_reasons: list[AgentFailureReason] = Field(default_factory=list)
    usage: AgentEvaluationCaseUsage = Field(
        default_factory=AgentEvaluationCaseUsage
    )
    elapsed_ms: int | None = Field(default=None, ge=0)


class AgentEvaluationRunDetail(StrictModel):
    summary: AgentEvaluationSummary
    cases: list[AgentEvaluationCaseResult] = Field(default_factory=list)


class AgentEvaluationRunList(StrictModel):
    items: list[AgentEvaluationSummary] = Field(default_factory=list)
