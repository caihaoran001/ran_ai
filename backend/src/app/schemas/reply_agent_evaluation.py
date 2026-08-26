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


class ReplyAgentAverageMetric(StrictModel):
    """A mean plus the two numbers a mean hides.

    A pass rate of 24/32 reads the same whether the eight failures each missed
    one clause or returned nothing at all, and those call for opposite fixes
    (tune retrieval vs. the knowledge base has no such content). `minimum` and
    `zero_count` make the difference visible without opening the case list.
    """

    value: float | None = Field(default=None, ge=0.0, le=1.0)
    denominator: int = Field(default=0, ge=0)
    minimum: float | None = Field(default=None, ge=0.0, le=1.0)
    zero_count: int = Field(default=0, ge=0)


class ReplyAgentBoundaryViolation(StrictModel):
    """一道题踩了非黑即白的红线，需要人先看这几道。"""

    case_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    codes: list[str] = Field(default_factory=list, max_length=20)
    detail: str = Field(min_length=1, max_length=2000)


class ReplyAgentLowScore(StrictModel):
    """一道题某项分数低于参考线。不判死，但要能一眼看到差多远。

    通过率会把"漏了一个知识点"和"检索一条都没捞到"压成同一个字。真正常见的
    失败恰好不是零分：两个知识点的题漏一个是 0.5 分，在平均分里不显眼，但业务
    上就是该说的一半没说。
    """

    case_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    metric_key: str = Field(min_length=1, max_length=64)
    metric_label: str = Field(min_length=1, max_length=200)
    score: float = Field(ge=0.0, le=1.0)
    reference: float | None = Field(default=None, ge=0.0, le=1.0)
    reason: str | None = Field(default=None, max_length=4000)


class ReplyAgentEvaluationMetrics(StrictModel):
    task_pass_rate: ReplyAgentRateMetric
    retrieval_decision_accuracy: ReplyAgentRateMetric
    rewrite_harm_rate: ReplyAgentRateMetric = Field(
        default_factory=ReplyAgentRateMetric
    )
    context_recall_pass_rate: ReplyAgentRateMetric
    context_precision_pass_rate: ReplyAgentRateMetric
    faithfulness_pass_rate: ReplyAgentRateMetric
    tool_selection_accuracy: ReplyAgentRateMetric
    argument_accuracy: ReplyAgentRateMetric
    behavior_accuracy: ReplyAgentRateMetric
    handoff_accuracy: ReplyAgentRateMetric = Field(default_factory=ReplyAgentRateMetric)
    answer_correctness_observed: ReplyAgentRateMetric
    answer_relevancy_average: ReplyAgentAverageMetric = Field(
        default_factory=ReplyAgentAverageMetric
    )
    # Averages sit beside the gates, not instead of them: a gate says how many
    # cases cleared a calibrated line, the average says how far the rest were
    # from it. Historical reports predate these fields and read back as zeroed.
    context_recall_average: ReplyAgentAverageMetric = Field(
        default_factory=ReplyAgentAverageMetric
    )
    context_precision_average: ReplyAgentAverageMetric = Field(
        default_factory=ReplyAgentAverageMetric
    )
    faithfulness_average: ReplyAgentAverageMetric = Field(
        default_factory=ReplyAgentAverageMetric
    )
    answer_correctness_average: ReplyAgentAverageMetric = Field(
        default_factory=ReplyAgentAverageMetric
    )
    judge_task_completion_observed: ReplyAgentRateMetric = Field(
        default_factory=ReplyAgentRateMetric
    )
    judge_groundedness_observed: ReplyAgentRateMetric = Field(
        default_factory=ReplyAgentRateMetric
    )
    judge_clarification_observed: ReplyAgentRateMetric = Field(
        default_factory=ReplyAgentRateMetric
    )
    judge_expression_observed: ReplyAgentRateMetric = Field(
        default_factory=ReplyAgentRateMetric
    )


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


class ReplyAgentRulerPolicy(StrictModel):
    retrieval_reference_policy_version: str = Field(
        default="compound-fact-v1", min_length=1, max_length=80
    )
    rewrite_harm_policy_version: str = Field(min_length=1, max_length=80)
    rewrite_recall_drop_threshold: float = Field(ge=0.0, le=1.0)
    task_completion_policy_version: str = Field(min_length=1, max_length=80)
    task_completion_threshold: float = Field(ge=0.0, le=1.0)
    task_completion_calibration_id: str = Field(min_length=1, max_length=120)


class ReplyAgentEvaluationSummary(StrictModel):
    schema_version: Literal[1] = 1
    run_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
    run_mode: Literal["mock", "real"]
    status: Literal["running", "completed", "failed", "interrupted"]
    started_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None
    git_sha: str = Field(min_length=7, max_length=64)
    dataset_id: Literal[
        "reply-agent-core-v1",
        "reply-agent-core-v2",
        "reply-agent-core-v3",
        "reply-agent-core-v4",
    ]
    dataset_version: Literal[1, 2, 3, 4]
    model: str = Field(min_length=1, max_length=120)
    evaluation_framework: Literal["deepeval"] = "deepeval"
    evaluation_framework_version: str = Field(min_length=1, max_length=40)
    score_source: Literal["fresh_run", "saved_output_reanalysis"] = "fresh_run"
    source_run_id: str | None = Field(default=None, max_length=120)
    ruler_policy: ReplyAgentRulerPolicy | None = None
    counts: ReplyAgentEvaluationCounts
    coverage: ReplyAgentEvaluationCoverage
    metrics: ReplyAgentEvaluationMetrics
    # 三份名单代替原来的"通过率"当报告主视图。历史报告没有这些字段，读回来是空的。
    boundary_violations: list[ReplyAgentBoundaryViolation] = Field(
        default_factory=list, max_length=200
    )
    retrieval_returned_nothing: list[ReplyAgentBoundaryViolation] = Field(
        default_factory=list, max_length=200
    )
    lowest_scores: list[ReplyAgentLowScore] = Field(
        default_factory=list, max_length=400
    )
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
    knowledge_query: str | None = Field(default=None, max_length=500)
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
