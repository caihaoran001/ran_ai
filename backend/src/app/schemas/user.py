from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import Timestamped
from app.schemas.document import UserProfileRead
from app.schemas.service_case import ServiceCaseRead


class UserStateProfileRead(Timestamped):
    id: int
    service_case_id: int
    state_json: dict
    summary: str | None = None
    source: str


class UserOperationsFact(BaseModel):
    domain: str
    domain_label: str
    field_key: str | None = None
    value: str
    evidence: str | None = None
    source: str | None = None
    occurred_at: datetime | None = None


class UserOperationsSnapshot(BaseModel):
    attention_code: str
    attention_label: str
    attention_reason: str
    confirmed_guidance: UserOperationsFact | None = None
    latest_change: UserOperationsFact | None = None
    checkin_days: int = 0
    complete_checkin_days: int = 0
    last_checkin_at: datetime | None = None
    updated_at: datetime | None = None


class UserListItem(Timestamped):
    id: int
    name: str
    phone: str | None
    wecom_user_id: str | None
    notes: str | None
    deleted_at: datetime | None = None
    service_cases: list[ServiceCaseRead] = Field(default_factory=list)


class UserDetail(UserListItem):
    latest_profile: UserProfileRead | None = None
    latest_state_profile: UserStateProfileRead | None = None
    operations_snapshot: UserOperationsSnapshot | None = None
