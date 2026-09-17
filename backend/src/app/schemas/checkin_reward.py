from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel


class RewardQualificationRead(BaseModel):
    id: int
    tier_days: int
    reward: str
    qualified_on: date
    shipment_id: int | None
    shipment_status: str | None


class RewardShipmentRead(BaseModel):
    id: int
    status: str
    rewards: str
    recipient_name: str | None
    recipient_phone: str | None
    shipping_address: str | None
    requested_at: datetime
    shipped_at: datetime | None
    marked_by: str | None


class RewardCheckinItemRead(BaseModel):
    order: int
    item_id: str
    question: str
    answer: str


class RewardCheckinSourceMessageRead(BaseModel):
    id: int
    message_type: str
    content: str
    created_at: datetime


class RewardCheckinDayRead(BaseModel):
    checkin_id: int
    checkin_date: date
    cumulative_day: int
    items: list[RewardCheckinItemRead]
    source_messages: list[RewardCheckinSourceMessageRead]
    original_submission: str | None


class RewardAdminCaseRead(BaseModel):
    service_case_id: int
    case_no: str
    user_name: str
    group_id: str
    group_name: str | None
    service_status: str
    complete_checkin_days: int
    participating: bool
    current_choice: str | None
    unshipped_rewards: str
    operational_status: str
    qualifications: list[RewardQualificationRead]
    shipments: list[RewardShipmentRead]
    checkin_days: list[RewardCheckinDayRead]


class Day28BenefitGrantRead(BaseModel):
    id: int
    benefit_code: str
    # 券配置改过名或整条被删掉时退回展示 code，不让台账凭空少一行。
    benefit_name: str
    status: str
    granted_at: datetime
    message_id: int | None


class Day28BenefitGrantCaseRead(BaseModel):
    service_case_id: int
    case_no: str
    user_name: str
    group_name: str | None
    grants: list[Day28BenefitGrantRead]


class MarkShipmentRead(BaseModel):
    ok: bool
    shipment_id: int
    status: str
    shipped_at: datetime
    marked_by: str
    user_notification_sent: bool
