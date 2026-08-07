from __future__ import annotations

from datetime import datetime

from typing import Literal

from pydantic import Field
from pydantic import BaseModel


class DashboardMetric(BaseModel):
    key: str
    label: str
    value: float | int | None = None
    unit: str = ""
    numerator: float | int | None = None
    denominator: int | None = None
    description: str
    status: str = "ok"


class OutcomeDashboardSummary(BaseModel):
    active_cases: int = 0
    eligible_day7_cases: int = 0
    eligible_day28_cases: int = 0
    metrics: list[DashboardMetric] = Field(default_factory=list)


class AICostSummary(BaseModel):
    total_calls: int = 0
    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated_cost_yuan: float = 0.0
    deepseek_cost_yuan: float = 0.0
    vision_cost_yuan: float = 0.0


class AIBalanceSummary(BaseModel):
    """DeepSeek 账户余额（外部实时查询）。available=False 表示查不到/不适用。"""

    available: bool = False
    balance_yuan: float | None = None
    currency: str = "CNY"
    is_available: bool = True
    is_low: bool = False
    alert_threshold_yuan: float = 0.0
    fetched_at: datetime | None = None


class AICostCaseItem(BaseModel):
    service_case_id: int
    case_no: str
    user_id: int | None = None
    user_name: str | None = None
    group_id: str
    total_calls: int = 0
    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated_cost_yuan: float = 0.0
    deepseek_cost_yuan: float = 0.0
    vision_cost_yuan: float = 0.0
    last_call_at: datetime | None = None


class CaseHealthItem(BaseModel):
    service_case_id: int
    case_no: str
    user_name: str | None = None
    group_id: str
    service_day: int
    last_checkin_date: str | None = None
    days_since_checkin: int | None = None
    risk_level: str | None = None
    stage_reports: list[str] = Field(default_factory=list)
    last_inbound_at: datetime | None = None


class EmotionalAlertMessage(BaseModel):
    message_id: int
    content: str
    detected_at: datetime
    level: str | None = None
    reason: str | None = None
    evidence: str | None = None


class EmotionalAlertCaseItem(BaseModel):
    service_case_id: int
    case_no: str
    user_id: int | None = None
    user_name: str | None = None
    group_id: str
    group_name: str | None = None
    messages: list[EmotionalAlertMessage] = Field(default_factory=list)


class DashboardCaseRef(BaseModel):
    service_case_id: int
    case_no: str
    user_id: int | None = None
    user_name: str | None = None
    user_phone: str | None = None
    group_id: str
    group_name: str | None = None


class DashboardMessageEvent(BaseModel):
    event_id: str
    message_id: int | None = None
    content: str
    occurred_at: datetime | None = None
    reason: str | None = None
    evidence: str | None = None
    level: str | None = None
    ai_reply_message_id: int | None = None
    ai_reply_content: str | None = None
    ai_reply_created_at: datetime | None = None


class DashboardCaseMessageEvents(BaseModel):
    case: DashboardCaseRef
    events: list[DashboardMessageEvent] = Field(default_factory=list)


class DashboardRiskReason(BaseModel):
    level: str | None = None
    reason: str | None = None
    evidence: str | None = None


class DashboardCaseInsightItem(BaseModel):
    """通用『服务单洞察』条目：风险原因 / 闲置天数 / 服务天数按需填充。"""

    case: DashboardCaseRef
    risk_level: str | None = None
    idle_days: int | None = None
    last_inbound_at: datetime | None = None
    # 本轮沉默里系统已自动召回的次数与最近一次时间（0 = 还没召回过或功能未开启）。
    recall_count: int = 0
    last_recall_at: datetime | None = None
    service_day: int | None = None
    started_at: datetime | None = None
    reasons: list[DashboardRiskReason] = Field(default_factory=list)


class DashboardCheckinEvent(BaseModel):
    checkin_id: int
    checkin_date: str
    content: str
    parsed_json: dict | None = None
    created_at: datetime


class DashboardCaseCheckins(BaseModel):
    case: DashboardCaseRef
    checkins: list[DashboardCheckinEvent] = Field(default_factory=list)


class DashboardPendingDocument(BaseModel):
    document_id: int
    document_type: str
    title: str
    status: str
    created_at: datetime
    updated_at: datetime
    # 内部运营状态：需人工复核的入营文件不会自动发送，后台必须醒目标记。
    manual_review_required: bool = False
    manual_review_reasons: list[str] = Field(default_factory=list)


class DashboardCasePendingDocuments(BaseModel):
    case: DashboardCaseRef
    documents: list[DashboardPendingDocument] = Field(default_factory=list)


class PopulationDistributionBucket(BaseModel):
    label: str
    count: int


class PopulationDistribution(BaseModel):
    key: str
    label: str
    description: str = ""
    multi_select: bool = False  # 多选维度的各桶人数按服务单去重，桶人数之和可超过总人数
    total: int  # 有效样本数（不含未采集）
    unknown: int = 0  # 未采集/无法解析的用户数
    buckets: list[PopulationDistributionBucket] = Field(default_factory=list)


class PopulationGeoCity(BaseModel):
    city: str
    count: int


class PopulationGeoProvince(BaseModel):
    province: str
    count: int
    cities: list[PopulationGeoCity] = Field(default_factory=list)


class PopulationGeography(BaseModel):
    total: int  # 已匹配到市的用户数
    unknown: int = 0  # 未采集或未能匹配到城市的用户数
    provinces: list[PopulationGeoProvince] = Field(default_factory=list)


class PopulationPortrait(BaseModel):
    total_users: int  # 纳入统计的活跃服务单数
    distributions: list[PopulationDistribution] = Field(default_factory=list)
    geography: PopulationGeography | None = None


class PopulationInsightJTBD(BaseModel):
    when: str
    want_to: str
    so_i_can: str


class PopulationInsightSegment(BaseModel):
    name: str
    entry_point: str
    deeper_goal: str
    evidence_basis: str


class PopulationInsightContent(BaseModel):
    headline: str
    summary: str
    common_jtbd: PopulationInsightJTBD
    segments: list[PopulationInsightSegment] = Field(default_factory=list)
    shared_patterns: list[str] = Field(default_factory=list)
    product_implications: list[str] = Field(default_factory=list)
    evidence_limits: list[str] = Field(default_factory=list)


class PopulationInsightResponse(BaseModel):
    status: Literal["empty", "generating", "ready", "failed"] = "empty"
    content: PopulationInsightContent | None = None
    included_users: int = 0
    profiled_users: int = 0
    model_input_users: int = 0
    evidence_level: str = "暂无洞察"
    generated_at: datetime | None = None
    generated_by_model: str | None = None
    refresh_reason: str | None = None
    next_automatic_refresh_at: datetime | None = None
    last_refresh_failed: bool = False


class DashboardSummary(BaseModel):
    service_cases_total: int
    service_cases_in_service: int
    checkins_today: int
    messages_today: int
    messages_outbound_today: int = 0
    pending_documents: int
    new_users_today: int = 0
    risk_users: int = 0
    handoff_events_today: int = 0
    unfollowed_users: int = 0
    renewal_followup_users: int = 0
    repurchase_soon_users: int = 0
    renewal_intent_users: int = 0
    refund_intent_users: int = 0
    refund_pending_users: int = 0
    lost_users: int = 0
    emotional_users_today: int = 0
    outcome_summary: OutcomeDashboardSummary = Field(default_factory=OutcomeDashboardSummary)
    ai_cost_summary: AICostSummary = Field(default_factory=AICostSummary)
