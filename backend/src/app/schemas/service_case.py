from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from app.models.service_case import ServiceCaseStatus
from app.schemas.common import Timestamped


class UserRead(Timestamped):
    id: int
    name: str
    phone: str | None
    wecom_user_id: str | None
    notes: str | None


class ServiceCaseUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_id: str | None = None
    status: ServiceCaseStatus | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    notes: str | None = None
    auto_send_documents: bool | None = None
    ai_auto_reply_enabled: bool | None = None
    refund_pending_confirmation: bool | None = None  # 运营驳回退款候选(置 False)时用
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
    risk_level: str = "normal"
    risk_flags: list[dict] | None = None
    intervention_reasons: list[dict] | None = None
    renewal_intent_flags: list[dict] | None = None
    refund_intent_flags: list[dict] | None = None
    refund_pending_confirmation: bool = False
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
    ai_deepseek_cost_yuan: float = 0.0
    ai_vision_cost_yuan: float = 0.0


class ServiceCaseAIContextPreview(BaseModel):
    context: str
