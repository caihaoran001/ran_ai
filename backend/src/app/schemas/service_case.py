from __future__ import annotations

from datetime import date, datetime

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
    auto_send_documents: bool | None = None
    ai_auto_reply_enabled: bool | None = None
    checkin_link_enabled: bool | None = None
    morning_push_enabled: bool | None = None
    inactive_wake_enabled: bool | None = None
    needs_human_intervention: bool | None = None
    reset_risk: bool | None = None  # 人工解除高风险标记


class ServiceCaseRead(Timestamped):
    id: int
    case_no: str
    user_id: int | None
    group_id: str
    group_name: str | None = None
    questionnaire_token: str
    status: ServiceCaseStatus
    started_at: datetime | None
    service_plan_start_date: date | None = None
    ended_at: datetime | None
    content_generated_at: datetime | None
    questionnaire_to_llm_output_ms: int | None
    notes: str | None
    auto_send_documents: bool = False
    ai_auto_reply_enabled: bool = True
    checkin_link_enabled: bool = True
    morning_push_enabled: bool = True
    inactive_wake_enabled: bool = True
    risk_level: str = "normal"
    risk_flags: list[dict] | None = None
    needs_human_intervention: bool = False
    intervention_reasons: list[dict] | None = None
    renewal_intent_flags: list[dict] | None = None
    refund_intent_flags: list[dict] | None = None
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
    ai_total_calls: int = 0
    ai_total_tokens: int = 0
    ai_estimated_cost_yuan: float = 0.0
