from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReplyAgentEvaluationCounts(StrictModel):
    total: int = Field(ge=0)
    completed: int = Field(ge=0)
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    errors: int = Field(ge=0)


class ReplyAgentRateMetric(StrictModel):
    value: float | None = Field(default=None, ge=0.0, le=1.0)
    numerator: int = Field(default=0, ge=0)
    denominator: int = Field(default=0, ge=0)


class ReplyAgentEvaluationMetrics(StrictModel):
    task_pass_rate: ReplyAgentRateMetric
    retrieval_decision_accuracy: ReplyAgentRateMetric
    context_recall_pass_rate: ReplyAgentRateMetric
    context_precision_pass_rate: ReplyAgentRateMetric
    faithfulness_pass_rate: ReplyAgentRateMetric
    tool_selection_accuracy: ReplyAgentRateMetric
    argument_accuracy: ReplyAgentRateMetric
    behavior_accuracy: ReplyAgentRateMetric
    answer_correctness_observed: ReplyAgentRateMetric


class ReplyAgentEvaluationCoverage(StrictModel):
    retrieval_counts: dict[str, int] = Field(default_factory=dict)
    execution_counts: dict[str, int] = Field(default_factory=dict)
    metric_group_counts: dict[str, int] = Field(default_factory=dict)
    retrieval_family_counts: dict[str, int] = Field(default_factory=dict)


class ReplyAgentUsageTotals(StrictModel):
    call_count: int = Field(default=0, ge=0)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    prompt_cache_hit_tokens: int = Field(default=0, ge=0)
    known_estimated_cost_yuan: float = Field(default=0, ge=0)
    estimated_cost_yuan: float | None = Field(default=0, ge=0)
    complete: bool = True


class ReplyAgentEvaluationSummary(StrictModel):
    schema_version: Literal[1] = 1
    run_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
    run_mode: Literal["mock", "real"]
    status: Literal["running", "completed", "failed", "interrupted"]
    started_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None
    git_sha: str = Field(min_length=7, max_length=64)
    dataset_id: Literal["reply-agent-core-v1"]
    dataset_version: Literal[1]
    model: str = Field(min_length=1, max_length=120)
    evaluation_framework: Literal["deepeval"] = "deepeval"
    evaluation_framework_version: str = Field(min_length=1, max_length=40)
    counts: ReplyAgentEvaluationCounts
    coverage: ReplyAgentEvaluationCoverage
    metrics: ReplyAgentEvaluationMetrics
    usage: ReplyAgentUsageTotals = Field(default_factory=ReplyAgentUsageTotals)
    estimated_cost_yuan: float | None = Field(default=0, ge=0)
    error: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def align_cost(self) -> "ReplyAgentEvaluationSummary":
        self.estimated_cost_yuan = self.usage.estimated_cost_yuan
        return self


class ReplyAgentMetricResult(StrictModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,119}$")
    label: str = Field(min_length=1, max_length=200)
    group: Literal["M0", "MR", "MT", "MB"]
    kind: Literal["deterministic", "llm"]
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    passed: bool | None = None
    enforced: bool = True
    reason: str | None = Field(default=None, max_length=12000)


class ReplyAgentExpected(StrictModel):
    retrieval_decision: Literal["retrieve", "skip"]
    response_mode: str = Field(min_length=1, max_length=80)
    metric_groups: list[Literal["M0", "MR", "MT", "MB"]] = Field(min_length=1)
    retrieval_families: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)
    requires_handoff: bool = False


class ReplyAgentActual(StrictModel):
    retrieval_decision: Literal["retrieve", "skip"] | None = None
    response_mode: str | None = Field(default=None, max_length=80)
    response: str | None = Field(default=None, max_length=20000)
    tool_names: list[str] = Field(default_factory=list)
    handoff_triggered: bool | None = None


class ReplyAgentFailureReason(StrictModel):
    stage: str = Field(min_length=1, max_length=80)
    code: str = Field(min_length=1, max_length=160)
    message: str = Field(min_length=1, max_length=4000)
    severity: Literal["info", "warning", "critical"] = "warning"


class ReplyAgentEvaluationCaseResult(StrictModel):
    case_id: str = Field(pattern=r"^RA-\d{3}$")
    title: str = Field(min_length=1, max_length=200)
    source_case_ids: list[str] = Field(min_length=1, max_length=10)
    execution_kind: Literal["rag", "agent", "handoff"]
    tags: list[str] = Field(default_factory=list)
    status: Literal["passed", "failed", "error"]
    user_query: str = Field(min_length=1, max_length=4000)
    expected: ReplyAgentExpected
    actual: ReplyAgentActual
    metrics: list[ReplyAgentMetricResult] = Field(default_factory=list)
    failure_reasons: list[ReplyAgentFailureReason] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)
    usage: dict[str, Any] = Field(default_factory=dict)
    elapsed_ms: int | None = Field(default=None, ge=0)


class ReplyAgentEvaluationRunDetail(StrictModel):
    summary: ReplyAgentEvaluationSummary
    cases: list[ReplyAgentEvaluationCaseResult] = Field(default_factory=list)


class ReplyAgentEvaluationRunList(StrictModel):
    items: list[ReplyAgentEvaluationSummary] = Field(default_factory=list)
