from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RagEvaluationCounts(StrictModel):
    total: int = Field(ge=0)
    completed: int = Field(ge=0)
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    skipped: int = Field(ge=0)
    errors: int = Field(ge=0)


class RagMetricValue(StrictModel):
    value: float | None = Field(default=None, ge=0.0, le=1.0)
    numerator: int | None = Field(default=None, ge=0)
    denominator: int | None = Field(default=None, ge=0)


class AnswerRelevancyCalibrationItem(StrictModel):
    case_id: str = Field(pattern=r"^relevancy-[a-z0-9-]+$")
    negative_source_case_id: str = Field(pattern=r"^relevancy-[a-z0-9-]+$")
    user_input: str = Field(min_length=1, max_length=1000)
    positive_response: str = Field(min_length=1, max_length=4000)
    negative_response: str = Field(min_length=1, max_length=4000)
    positive_score: float = Field(ge=0.0, le=1.0)
    negative_score: float = Field(ge=0.0, le=1.0)


class AnswerRelevancyCalibrationMetrics(StrictModel):
    positive_mean: float = Field(ge=0.0, le=1.0)
    negative_mean: float = Field(ge=0.0, le=1.0)
    mean_gap: float = Field(ge=-1.0, le=1.0)
    auc: float = Field(ge=0.0, le=1.0)
    paired_accuracy: float = Field(ge=0.0, le=1.0)
    positive_pass_rate: float = Field(ge=0.0, le=1.0)
    negative_rejection_rate: float = Field(ge=0.0, le=1.0)


class AnswerRelevancyCalibrationReport(StrictModel):
    schema_version: Literal[1] = 1
    calibration_id: str = Field(pattern=r"^answer-relevancy-zh-v\d+$")
    dataset_version: int = Field(ge=1)
    run_id: str = Field(pattern=r"^relevancy-calibration-[A-Za-z0-9._-]+$")
    status: Literal["running", "completed", "failed"]
    started_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None
    git_sha: str = Field(min_length=7, max_length=64)
    model: str = Field(min_length=1, max_length=120)
    embedding_model: str = Field(min_length=1, max_length=120)
    ragas_version: str = Field(min_length=1, max_length=40)
    threshold: float = Field(ge=0.0, le=1.0)
    base_pair_count: int = Field(ge=1)
    completed_pair_count: int = Field(ge=0)
    passed: bool | None = None
    metrics: AnswerRelevancyCalibrationMetrics | None = None
    failed_criteria: list[str] = Field(default_factory=list)
    items: list[AnswerRelevancyCalibrationItem] = Field(default_factory=list)
    estimated_cost_yuan: float | None = Field(default=None, ge=0)
    error: str | None = Field(default=None, max_length=2000)


class RagEvaluationMetrics(StrictModel):
    retrieval_decision_error_rate: RagMetricValue
    rewrite_harm_rate: RagMetricValue
    context_recall: RagMetricValue
    context_precision: RagMetricValue
    faithfulness: RagMetricValue
    answer_relevancy: RagMetricValue


class RagEvaluationSummary(StrictModel):
    schema_version: Literal[1] = 1
    run_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
    run_mode: Literal["mock", "real", "unknown"] = "unknown"
    status: Literal["queued", "running", "completed", "failed", "interrupted"]
    started_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None
    git_sha: str = Field(min_length=7, max_length=64)
    dataset_id: str = Field(min_length=1, max_length=120)
    dataset_version: int = Field(ge=1)
    model: str = Field(min_length=1, max_length=120)
    embedding_model: str = Field(min_length=1, max_length=120)
    ragas_version: str = Field(min_length=1, max_length=40)
    counts: RagEvaluationCounts
    metrics: RagEvaluationMetrics
    critical_fact_errors: int = Field(default=0, ge=0)
    estimated_cost_yuan: float | None = Field(default=None, ge=0)
    error: str | None = Field(default=None, max_length=2000)


class RagHistoryMessage(StrictModel):
    role: Literal["user", "staff", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class RagKnowledgeHit(StrictModel):
    rank: int = Field(ge=1)
    chunk_uid: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=300)
    source_type: str = Field(min_length=1, max_length=40)
    content: str = Field(min_length=1, max_length=12000)


class RagFailureReason(StrictModel):
    stage: Literal["decision", "rewrite", "retrieval", "answer", "ragas", "runtime"]
    code: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    message: str = Field(min_length=1, max_length=2000)
    severity: Literal["info", "warning", "critical"] = "warning"


class RagRewriteExpected(StrictModel):
    must_include: list[str] = Field(default_factory=list)
    must_preserve_meanings: list[str] = Field(default_factory=list)
    # Read-only compatibility for reports written before the fixed positive-fact schema.
    forbidden_inferences: list[str] = Field(default_factory=list, exclude=True)


class RagCaseOrigin(StrictModel):
    kind: Literal["faq_rewrite", "real_chat", "unknown"] = "unknown"
    reference_id: str = Field(default="legacy-report", min_length=1, max_length=160)


class RagCaseExpected(StrictModel):
    retrieval_decision: Literal["retrieve", "skip"]
    response_mode: Literal[
        "answer",
        "acknowledge",
        "clarify",
        "use_runtime_data",
        "support",
    ] | None = None
    rewrite: RagRewriteExpected = Field(default_factory=RagRewriteExpected)
    required_facts: list[str] = Field(default_factory=list)
    # Accepted only so historical report files remain readable; never emitted.
    forbidden_claims: list[str] = Field(default_factory=list, exclude=True)


class RagCaseActual(StrictModel):
    retrieval_decision: Literal["retrieve", "skip"] | None = None
    decision_source: Literal["rewritten", "explicit_skip", "fallback_original", "unavailable"]
    knowledge_query: str | None = Field(default=None, max_length=500)
    raw_query_hits: list[RagKnowledgeHit] = Field(default_factory=list)
    rewritten_query_hits: list[RagKnowledgeHit] = Field(default_factory=list)
    response: str | None = Field(default=None, max_length=20000)


class RagCaseScores(StrictModel):
    context_recall: float | None = Field(default=None, ge=0.0, le=1.0)
    context_precision: float | None = Field(default=None, ge=0.0, le=1.0)
    faithfulness: float | None = Field(default=None, ge=0.0, le=1.0)
    answer_relevancy: float | None = Field(default=None, ge=0.0, le=1.0)


class RagEvaluationCaseResult(StrictModel):
    case_id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=200)
    origin: RagCaseOrigin = Field(default_factory=RagCaseOrigin)
    tags: list[str] = Field(default_factory=list)
    status: Literal["passed", "failed", "skipped", "error"]
    history: list[RagHistoryMessage] = Field(default_factory=list)
    user_query: str = Field(min_length=1, max_length=4000)
    expected: RagCaseExpected
    actual: RagCaseActual
    scores: RagCaseScores
    failure_reasons: list[RagFailureReason] = Field(default_factory=list)
    elapsed_ms: int | None = Field(default=None, ge=0)


class RagEvaluationRunDetail(StrictModel):
    summary: RagEvaluationSummary
    cases: list[RagEvaluationCaseResult] = Field(default_factory=list)


class RagEvaluationRunList(StrictModel):
    items: list[RagEvaluationSummary]
