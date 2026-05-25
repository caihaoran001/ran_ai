from __future__ import annotations

from pydantic import BaseModel


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
