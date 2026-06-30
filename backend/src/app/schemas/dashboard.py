from __future__ import annotations

from datetime import datetime

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
    last_call_at: datetime | None = None


class CaseHealthItem(BaseModel):
    service_case_id: int
    case_no: str
    user_name: str | None = None
    group_id: str
    service_day: int
    last_checkin_date: str | None = None
    days_since_checkin: int | None = None
    action_focus: str | None = None
    risk_level: str | None = None
    needs_human_intervention: bool = False
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


class DashboardSummary(BaseModel):
    service_cases_total: int
    service_cases_in_service: int
    checkins_today: int
    messages_today: int
    messages_outbound_today: int = 0
    pending_documents: int
    new_users_today: int = 0
    risk_users: int = 0
    human_intervention_users: int = 0
    unfollowed_users: int = 0
    renewal_followup_users: int = 0
    repurchase_soon_users: int = 0
    renewal_intent_users: int = 0
    refund_intent_users: int = 0
    lost_users: int = 0
    emotional_users_today: int = 0
    outcome_summary: OutcomeDashboardSummary = Field(default_factory=OutcomeDashboardSummary)
    ai_cost_summary: AICostSummary = Field(default_factory=AICostSummary)
