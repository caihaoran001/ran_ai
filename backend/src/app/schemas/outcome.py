from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.schemas.common import Timestamped


class OutcomeEventRead(Timestamped):
    id: int
    service_case_id: int
    message_id: int | None
    milestone: str
    field_key: str
    event_type: str
    value_text: str
    evidence_text: str
    confidence: float
    source: str
    occurred_at: datetime | None
    metadata_json: dict[str, Any] | None = None


class OutcomeStageStateRead(Timestamped):
    id: int
    service_case_id: int
    milestone: str
    status: str
    collected_fields: dict[str, Any] | None = None
    missing_fields: list[str] | None = None


class OutcomeSnapshotRead(Timestamped):
    id: int
    service_case_id: int
    milestone: str
    status: str
    metrics_json: dict[str, Any] | None = None
    evidence_json: dict[str, Any] | None = None
    ai_summary: str | None = None
    action_items: list[dict[str, Any]] | None = None
    generated_at: datetime | None = None


class OutcomeBundleRead(BaseModel):
    events: list[OutcomeEventRead]
    states: list[OutcomeStageStateRead]
    snapshots: list[OutcomeSnapshotRead]
