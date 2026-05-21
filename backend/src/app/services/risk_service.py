from __future__ import annotations

from app.models.service_case import ServiceCase


def is_high_risk(service_case: ServiceCase | None, text: str | None = None) -> bool:
    if service_case and service_case.risk_level == "high":
        return True
    return False
