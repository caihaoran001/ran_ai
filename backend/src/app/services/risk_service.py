from __future__ import annotations

from typing import Any

from app.models.service_case import ServiceCase


def merge_case_risks(service_case: ServiceCase, risks: list[dict[str, str]]) -> None:
    if not risks:
        return
    existing = service_case.risk_flags or []
    seen = {(item.get("code"), item.get("keyword")) for item in existing if isinstance(item, dict)}
    for risk in risks:
        key = (risk.get("code"), risk.get("keyword"))
        if key not in seen:
            existing.append(risk)
            seen.add(key)
    service_case.risk_flags = existing
    
    # Only elevate to "high" if there is at least one L3 risk or level='high'
    has_l3_risk = any(r.get("client_level") == "L3" or r.get("level") == "high" for r in risks)
    if has_l3_risk:
        service_case.risk_level = "high"
    elif not service_case.risk_level or service_case.risk_level == "normal":
        has_l2_risk = any(r.get("client_level") == "L2" or r.get("level") == "medium" for r in risks)
        if has_l2_risk:
            service_case.risk_level = "medium"


def is_high_risk(service_case: ServiceCase | None, text: str | None = None) -> bool:
    if service_case and service_case.risk_level == "high":
        return True
    return False
