from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TolerantModel(BaseModel):
    # 报告由评测脚本生成、后续还会加字段：多出的字段忽略而不是拒绝，
    # 避免一次 schema 演进就把整个详情页打成 422。
    model_config = ConfigDict(extra="ignore")


class ArchivistEvaluationMessage(TolerantModel):
    sender: str
    content: str
    time: str | None = None


class ArchivistEvaluationCaseBatch(TolerantModel):
    label: str
    messages: list[ArchivistEvaluationMessage] = Field(default_factory=list)


class ArchivistEvaluationEmit(TolerantModel):
    name: str
    args: dict[str, Any] = Field(default_factory=dict)


class ArchivistEvaluationUsage(TolerantModel):
    model: str | None = None
    total_tokens: int | None = Field(default=None, ge=0)
    estimated_cost_yuan: float | None = Field(default=None, ge=0)


class ArchivistCheckinAdded(TolerantModel):
    date: str
    parsed: dict[str, Any] = Field(default_factory=dict)


class ArchivistCheckinUpdated(TolerantModel):
    date: str
    changes: dict[str, Any] = Field(default_factory=dict)


class ArchivistOutcomeAdded(TolerantModel):
    field_key: str
    event_type: str
    value_text: str | None = None
    message_id: int | None = None


class ArchivistDraftAdded(TolerantModel):
    draft_type: str | None = None
    title: str | None = None
    scope: str | None = None


class ArchivistBatchDBChanges(TolerantModel):
    checkins_added: list[ArchivistCheckinAdded] = Field(default_factory=list)
    checkins_updated: list[ArchivistCheckinUpdated] = Field(default_factory=list)
    outcomes_added: list[ArchivistOutcomeAdded] = Field(default_factory=list)
    state_changed: bool = False
    memory_changed: bool = False
    drafts_added: list[ArchivistDraftAdded] = Field(default_factory=list)


class ArchivistEvaluationBatchResult(TolerantModel):
    batch_index: int = Field(ge=0)
    label: str
    watermark_from: int | None = None
    watermark_to: int | None = None
    emitted: list[ArchivistEvaluationEmit] = Field(default_factory=list)
    kept: dict[str, Any] = Field(default_factory=dict)
    dropped: list[dict[str, Any]] = Field(default_factory=list)
    usage: ArchivistEvaluationUsage | None = None
    db_changes: ArchivistBatchDBChanges = Field(
        default_factory=ArchivistBatchDBChanges
    )


class ArchivistEvaluationRepeat(TolerantModel):
    repeat_index: int = Field(ge=0)
    verdict: str
    failed_assertions: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    batch_results: list[ArchivistEvaluationBatchResult] = Field(default_factory=list)


class ArchivistEvaluationCase(TolerantModel):
    group_code: str
    title: str
    expectation: str
    batches: list[ArchivistEvaluationCaseBatch] = Field(default_factory=list)
    repeats: list[ArchivistEvaluationRepeat] = Field(default_factory=list)


class ArchivistEvaluationSummary(TolerantModel):
    schema_version: int = Field(default=1, ge=1)
    run_id: str
    label: str | None = None
    started_at: datetime
    mode: str
    model: str
    suite: str
    groups: int = Field(ge=0)
    repeats: int = Field(ge=0)
    group_runs: int = Field(ge=0)
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    flagged: int = Field(ge=0)
    batches: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    total_cost_yuan: float = Field(ge=0)


class ArchivistEvaluationRunDetail(TolerantModel):
    summary: ArchivistEvaluationSummary
    cases: list[ArchivistEvaluationCase] = Field(default_factory=list)


class ArchivistEvaluationRunList(TolerantModel):
    items: list[ArchivistEvaluationSummary] = Field(default_factory=list)
