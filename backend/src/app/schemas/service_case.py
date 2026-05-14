from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.service_case import ServiceCaseStatus
from app.schemas.common import Timestamped


class UserCreate(BaseModel):
    name: str = Field(default="", max_length=100)
    phone: str | None = None
    wecom_user_id: str | None = None
    notes: str | None = None


class UserRead(Timestamped):
    id: int
    name: str
    phone: str | None
    wecom_user_id: str | None
    notes: str | None


class ServiceCaseCreate(BaseModel):
    group_id: str = Field(min_length=1, max_length=128)
    user: UserCreate
    case_no: str | None = None
    notes: str | None = None


class ServiceCaseUpdate(BaseModel):
    group_id: str | None = None
    status: ServiceCaseStatus | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    notes: str | None = None


class ServiceCaseRead(Timestamped):
    id: int
    case_no: str
    user_id: int | None
    group_id: str
    questionnaire_token: str
    status: ServiceCaseStatus
    started_at: datetime | None
    ended_at: datetime | None
    notes: str | None
    deleted_at: datetime | None = None
    user: UserRead | None = None


class ServiceCaseStats(BaseModel):
    message_count: int
    inbound_message_count: int
    outbound_message_count: int
    avg_daily_inbound_messages: float
    last_message_at: datetime | None
    last_user_message_at: datetime | None
    checkin_count: int
    last_checkin_at: datetime | None
