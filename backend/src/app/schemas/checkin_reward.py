from __future__ import annotations

from datetime import date, datetime
from typing import Literal

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
    # 该单归属的批次：current / round:<id> / legacy:<YYYY-MM-DD>
    batch_key: str


class RewardAdminBatchRead(BaseModel):
    key: str
    kind: Literal["current", "round", "legacy"]
    started_on: date | None
    closed_at: datetime | None
    # 仅 legacy 批次：该批奖励生成当天（北京日期）
    reward_created_on: date | None
    case_count: int


class RewardAdminListRead(BaseModel):
    rewards_enabled: bool
    batches: list[RewardAdminBatchRead]
    cases: list[RewardAdminCaseRead]


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
