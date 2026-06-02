from __future__ import annotations

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
