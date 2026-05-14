from __future__ import annotations

from pydantic import BaseModel


class DashboardSummary(BaseModel):
    service_cases_total: int
    service_cases_in_service: int
    checkins_today: int
    messages_today: int
    pending_documents: int
