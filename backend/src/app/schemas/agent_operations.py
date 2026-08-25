from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class AgentOutcomeCount(BaseModel):
    key: str
    count: int


class AgentRuntimeSummary(BaseModel):
    runs: int = 0
    completed_runs: int = 0
    failed_runs: int = 0
    running_runs: int = 0
    model_calls: int = 0
    tool_calls: int = 0
    successful_tool_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_yuan: float = 0.0
    input_count: int = 0
    output_count: int = 0
    dropped_count: int = 0
    latency_p50_ms: int | None = None
    latency_p95_ms: int | None = None
    outcomes: list[AgentOutcomeCount] = Field(default_factory=list)


class ArchivistRuntimeSummary(AgentRuntimeSummary):
    output_breakdown: dict[str, int] = Field(default_factory=dict)
    draft_statuses: dict[str, int] = Field(default_factory=dict)


class AgentToolStat(BaseModel):
    name: str
    calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    rejected_calls: int = 0
    success_rate: float | None = None
    average_latency_ms: int | None = None
    errors: list[AgentOutcomeCount] = Field(default_factory=list)


class AICostScenarioStat(BaseModel):
    scenario: str
    calls: int = 0
    successful_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_yuan: float = 0.0


class AICostCaseStat(BaseModel):
    service_case_id: int
    case_no: str
    user_name: str | None = None
    calls: int = 0
    total_tokens: int = 0
    estimated_cost_yuan: float = 0.0
    last_call_at: datetime | None = None
    scenarios: list[AICostScenarioStat] = Field(default_factory=list)


class AICostOverview(BaseModel):
    calls: int = 0
    successful_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_yuan: float = 0.0
    service_case_count: int = 0
    unassigned_cost_yuan: float = 0.0
    scenarios: list[AICostScenarioStat] = Field(default_factory=list)
    cases: list[AICostCaseStat] = Field(default_factory=list)


class AgentConversationMessage(BaseModel):
    id: int
    role: str
    content: str
    message_type: str
    status: str
    created_at: datetime
    sent_at: datetime | None = None
    error: str | None = None


class RecentAgentRun(BaseModel):
    id: int
    agent_type: str
    run_kind: str
    status: str
    outcome: str | None = None
    service_case_id: int | None = None
    case_no: str | None = None
    user_name: str | None = None
    model_calls: int = 0
    tool_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_yuan: float = 0.0
    input_count: int = 0
    output_count: int = 0
    latency_ms: int | None = None
    error_type: str | None = None
    started_at: datetime
    finished_at: datetime | None = None


class AgentKnowledgeHit(BaseModel):
    rank: int
    chunk_id: int
    chunk_uid: str
    title: str
    source_type: str
    updated_at: datetime | None = None
    content_excerpt: str
    vector_rank: int | None = None
    vector_distance: float | None = None
    keyword_rank: int | None = None
    confirmation_state: str | None = None


class AgentKnowledgeDecision(BaseModel):
    """本轮参考问答检索的决策。searched=按改写查询检索，skipped=判定无需检索，
    fallback=改写缺失退回原问题；hits 仅记录真实运行当时保存的命中。"""

    decision: str
    original_query: str | None = None
    query: str | None = None
    effective_query: str | None = None
    hit_count: int | None = None
    primary_hit_count: int | None = None
    rewrite_fallback_used: bool = False
    skipped_product_terms: list[str] = Field(default_factory=list)
    hits: list[AgentKnowledgeHit] = Field(default_factory=list)


class AgentFailureDetail(BaseModel):
    stage: str
    error_type: str | None = None
    summary: str
    retryable: bool = False
    timeout_seconds: float | None = None
    elapsed_ms: int | None = None
    failed_at: datetime | None = None


class AgentRunDetail(RecentAgentRun):
    input_message: AgentConversationMessage | None = None
    output_message: AgentConversationMessage | None = None
    knowledge: AgentKnowledgeDecision | None = None
    failure: AgentFailureDetail | None = None


class AgentRunPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[AgentRunDetail] = Field(default_factory=list)


class ArchivistInputMessage(BaseModel):
    id: int
    role: str
    content: str
    created_at: datetime
    is_context: bool = False


class ArchivistBatchChange(BaseModel):
    kind: str
    action: str
    title: str
    before: str | None = None
    after: str | None = None
    evidence: str | None = None
    items: list[dict] = Field(default_factory=list)


class ArchivistBatchRun(RecentAgentRun):
    trigger_reason: str | None = None
    input_source: str = "unavailable"
    input_messages: list[ArchivistInputMessage] = Field(default_factory=list)
    changes: list[ArchivistBatchChange] = Field(default_factory=list)
    changes_detail_available: bool = False
    dropped_items: list[dict] = Field(default_factory=list)


class AgentOperationsOverview(BaseModel):
    days: int
    period_start: datetime
    generated_at: datetime
    all_agents: AgentRuntimeSummary
    reply: AgentRuntimeSummary
    reminders: AgentRuntimeSummary
    archivist: ArchivistRuntimeSummary
    tools: list[AgentToolStat] = Field(default_factory=list)
    costs: AICostOverview
    recent_runs: list[RecentAgentRun] = Field(default_factory=list)
    archivist_batches: list[ArchivistBatchRun] = Field(default_factory=list)
