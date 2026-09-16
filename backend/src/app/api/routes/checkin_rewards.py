from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_session
from app.models.checkin_reward import (
    CheckinRewardProgram,
    CheckinRewardQualification,
    CheckinRewardShipment,
)
from app.models.checkin import Checkin
from app.models.day28_benefit_grant import Day28BenefitGrant
from app.models.message import Message, MessageDirection, MessageSenderType
from app.models.service_case import ServiceCase
from app.schemas.checkin_reward import (
    Day28BenefitGrantCaseRead,
    Day28BenefitGrantRead,
    MarkShipmentRead,
    RewardAdminCaseRead,
    RewardCheckinDayRead,
    RewardCheckinItemRead,
    RewardCheckinSourceMessageRead,
    RewardQualificationRead,
    RewardShipmentRead,
)
from app.services.checkin_presentation import load_personalized_checkin_labels
from app.services.checkin_rewards import (
    OPEN_SHIPMENT_STATUSES,
    _qualification_display,
    deliver_pending_shipment_notices,
    reward_checkin_evidence_message_ids,
    reward_checkin_items,
    valid_reward_checkins,
)
from app.services.service_case_queries import active_service_case_condition


router = APIRouter()


def _operational_status(
    *,
    program: CheckinRewardProgram | None,
    unshipped: list[CheckinRewardQualification],
    shipments: list[CheckinRewardShipment],
) -> str:
    active = next(
        (row for row in reversed(shipments) if row.status in OPEN_SHIPMENT_STATUSES),
        None,
    )
    if active is not None:
        return active.status
    if unshipped:
        return "accumulating" if program and program.current_choice == "accumulate" else "waiting_choice"
    if any(row.status == "shipped" for row in shipments):
        return "shipped"
    return "qualified"


@router.get("/checkin-rewards", response_model=list[RewardAdminCaseRead])
async def list_checkin_rewards(
    session: AsyncSession = Depends(get_session),
) -> list[RewardAdminCaseRead]:
    service_cases = list(
        await session.scalars(
            select(ServiceCase)
            .where(active_service_case_condition())
            .options(selectinload(ServiceCase.user))
            .order_by(ServiceCase.updated_at.desc(), ServiceCase.id.desc())
        )
    )
    case_ids = [row.id for row in service_cases]
    if not case_ids:
        return []
    programs = {
        row.service_case_id: row
        for row in await session.scalars(
            select(CheckinRewardProgram).where(
                CheckinRewardProgram.service_case_id.in_(case_ids)
            )
        )
    }
    qualifications_by_case: dict[int, list[CheckinRewardQualification]] = {}
    for row in await session.scalars(
        select(CheckinRewardQualification)
        .where(CheckinRewardQualification.service_case_id.in_(case_ids))
        .order_by(CheckinRewardQualification.tier_days)
    ):
        qualifications_by_case.setdefault(row.service_case_id, []).append(row)
    shipments_by_case: dict[int, list[CheckinRewardShipment]] = {}
    shipments_by_id: dict[int, CheckinRewardShipment] = {}
    for row in await session.scalars(
        select(CheckinRewardShipment)
        .where(CheckinRewardShipment.service_case_id.in_(case_ids))
        .order_by(CheckinRewardShipment.id)
    ):
        shipments_by_case.setdefault(row.service_case_id, []).append(row)
        shipments_by_id[row.id] = row

    reward_case_ids = {
        *qualifications_by_case.keys(),
        *shipments_by_case.keys(),
    }
    valid_checkins_by_case: dict[int, list[Checkin]] = {}
    evidence_ids_by_checkin: dict[int, list[int]] = {}
    all_evidence_ids: set[int] = set()
    for service_case in service_cases:
        if service_case.id not in reward_case_ids:
            continue
        valid_checkins = await valid_reward_checkins(
            session,
            service_case=service_case,
        )
        valid_checkins_by_case[service_case.id] = valid_checkins
        for checkin in valid_checkins:
            message_ids = reward_checkin_evidence_message_ids(checkin)
            evidence_ids_by_checkin[checkin.id] = message_ids
            all_evidence_ids.update(message_ids)
    source_messages = {
        row.id: row
        for row in (
            list(
                await session.scalars(
                    select(Message).where(
                        Message.id.in_(all_evidence_ids),
                        Message.service_case_id.in_(reward_case_ids),
                        Message.direction == MessageDirection.INBOUND,
                        Message.sender_type == MessageSenderType.USER,
                    )
                )
            )
            if all_evidence_ids
            else []
        )
    }
    labels_by_id_by_case: dict[int, dict[str, str]] = {}
    for service_case_id, valid_checkins in valid_checkins_by_case.items():
        case_item_ids = {
            str(item_id)
            for checkin in valid_checkins
            for item_id in (
                (checkin.parsed_json or {}).get("custom_checkin", {}).keys()
                if isinstance(checkin.parsed_json, dict)
                and isinstance((checkin.parsed_json or {}).get("custom_checkin"), dict)
                else []
            )
        }
        labels_by_id_by_case[service_case_id] = await load_personalized_checkin_labels(
            session,
            service_case_id=service_case_id,
            item_ids=case_item_ids,
        )

    response: list[RewardAdminCaseRead] = []
    for service_case in service_cases:
        qualifications = qualifications_by_case.get(service_case.id, [])
        shipments = shipments_by_case.get(service_case.id, [])
        program = programs.get(service_case.id)
        if not qualifications and not shipments:
            continue
        shipped_ids = {row.id for row in shipments if row.status == "shipped"}
        unshipped = [row for row in qualifications if row.shipment_id not in shipped_ids]
        valid_checkins = valid_checkins_by_case.get(service_case.id, [])
        labels_by_id = labels_by_id_by_case.get(service_case.id, {})
        response.append(
            RewardAdminCaseRead(
                service_case_id=service_case.id,
                case_no=service_case.case_no,
                user_name=service_case.user.name if service_case.user else "未绑定用户",
                group_id=service_case.group_id,
                group_name=service_case.group_name,
                service_status=service_case.status.value,
                complete_checkin_days=len(valid_checkins),
                current_choice=program.current_choice if program else None,
                unshipped_rewards=_qualification_display(unshipped),
                operational_status=_operational_status(
                    program=program,
                    unshipped=unshipped,
                    shipments=shipments,
                ),
                qualifications=[
                    RewardQualificationRead(
                        id=row.id,
                        tier_days=row.tier_days,
                        reward=_qualification_display([row]),
                        qualified_on=row.qualified_on,
                        shipment_id=row.shipment_id,
                        shipment_status=(
                            shipments_by_id[row.shipment_id].status
                            if row.shipment_id in shipments_by_id
                            else None
                        ),
                    )
                    for row in qualifications
                ],
                shipments=[
                    RewardShipmentRead(
                        id=shipment.id,
                        status=shipment.status,
                        rewards=_qualification_display(
                            [row for row in qualifications if row.shipment_id == shipment.id]
                        ),
                        recipient_name=shipment.recipient_name,
                        recipient_phone=shipment.recipient_phone,
                        shipping_address=shipment.shipping_address,
                        requested_at=shipment.requested_at,
                        shipped_at=shipment.shipped_at,
                        marked_by=shipment.marked_by,
                    )
                    for shipment in reversed(shipments)
                    if shipment.status != "cancelled"
                ],
                checkin_days=[
                    RewardCheckinDayRead(
                        checkin_id=checkin.id,
                        checkin_date=checkin.checkin_date,
                        cumulative_day=index,
                        items=[
                            RewardCheckinItemRead(**item)
                            for item in reward_checkin_items(
                                checkin,
                                labels_by_id=labels_by_id,
                            )
                        ],
                        source_messages=[
                            RewardCheckinSourceMessageRead(
                                id=source.id,
                                message_type=source.message_type,
                                content=source.content,
                                created_at=source.created_at,
                            )
                            for message_id in evidence_ids_by_checkin[checkin.id]
                            if (source := source_messages.get(message_id)) is not None
                            and source.service_case_id == service_case.id
                        ],
                        original_submission=(
                            checkin.content
                            if not evidence_ids_by_checkin[checkin.id]
                            and checkin.content
                            not in {"（聊天自动归档）", "（历史打卡漏记修复）"}
                            else None
                        ),
                    )
                    for index, checkin in enumerate(valid_checkins, start=1)
                ],
            )
        )
    status_order = {"pending_shipment": 0, "missing_address": 1, "waiting_choice": 2, "accumulating": 3, "shipped": 4}
    response.sort(key=lambda row: (status_order.get(row.operational_status, 9), row.user_name))
    return response


@router.post(
    "/checkin-rewards/shipments/{shipment_id}/mark-shipped",
    response_model=MarkShipmentRead,
)
async def mark_reward_shipment_shipped(
    shipment_id: int,
    x_admin_operator: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> MarkShipmentRead:
    shipment = await session.scalar(
        select(CheckinRewardShipment)
        .where(CheckinRewardShipment.id == shipment_id)
        .with_for_update()
    )
    if shipment is None:
        raise HTTPException(status_code=404, detail="reward shipment not found")
    if shipment.status == "shipped":
        raise HTTPException(status_code=409, detail="reward shipment is already shipped")
    if shipment.status != "pending_shipment":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="recipient name, phone and detailed address are required before shipping",
        )
    if not all(
        str(value or "").strip()
        for value in (
            shipment.recipient_name,
            shipment.recipient_phone,
            shipment.shipping_address,
        )
    ):
        raise HTTPException(status_code=409, detail="shipping information is incomplete")
    now = datetime.now(timezone.utc)
    operator = str(x_admin_operator or "shared-admin").strip()[:100] or "shared-admin"
    shipment.status = "shipped"
    shipment.shipped_at = now
    shipment.marked_by = operator
    shipment.notification_status = "pending"
    await session.commit()

    # 发货事实先落账；群通知失败不回滚已发货，由定时器按账本重试。
    await deliver_pending_shipment_notices(shipment_ids=[shipment.id])
    await session.refresh(shipment)
    return MarkShipmentRead(
        ok=True,
        shipment_id=shipment.id,
        status="shipped",
        shipped_at=now,
        marked_by=operator,
        user_notification_sent=shipment.notification_status == "sent",
    )


# --------------------------------------------------------------------------
# 第28天权益发放台账
#
# 与打卡奖励同属"权益发放"，共用 /rewards 页面，所以放在同一个路由模块，
# 但数据是完全独立的一张表：打卡奖励看的是达标与寄出，这里看的是券的领取
# 入口有没有发出去。系统看不到客户领没领（商城未打通），台账只能到这一步。
# --------------------------------------------------------------------------


@router.get("/day28-benefit-grants", response_model=list[Day28BenefitGrantCaseRead])
async def list_day28_benefit_grants(
    session: AsyncSession = Depends(get_session),
) -> list[Day28BenefitGrantCaseRead]:
    from app.services.day28_benefit_settings import get_benefit_config

    grants = list(
        await session.scalars(
            select(Day28BenefitGrant).order_by(
                Day28BenefitGrant.granted_at.desc(), Day28BenefitGrant.id.desc()
            )
        )
    )
    if not grants:
        return []

    service_cases = {
        row.id: row
        for row in await session.scalars(
            select(ServiceCase)
            .where(ServiceCase.id.in_({row.service_case_id for row in grants}))
            .options(selectinload(ServiceCase.user))
        )
    }
    try:
        names = {
            benefit.code: benefit.name for benefit in (await get_benefit_config(session)).benefits
        }
    except ValueError:
        # 券配置被改坏时台账仍要看得见（这里是发放事实，不是配置页），退回展示 code。
        names = {}

    grouped: dict[int, list[Day28BenefitGrantRead]] = {}
    for grant in grants:
        grouped.setdefault(grant.service_case_id, []).append(
            Day28BenefitGrantRead(
                id=grant.id,
                benefit_code=grant.benefit_code,
                benefit_name=names.get(grant.benefit_code, grant.benefit_code),
                status=grant.status,
                granted_at=grant.granted_at,
                message_id=grant.message_id,
            )
        )

    response: list[Day28BenefitGrantCaseRead] = []
    for case_id, rows in grouped.items():
        service_case = service_cases.get(case_id)
        if service_case is None:
            continue
        response.append(
            Day28BenefitGrantCaseRead(
                service_case_id=case_id,
                case_no=service_case.case_no,
                user_name=service_case.user.name if service_case.user else "未绑定用户",
                group_name=service_case.group_name,
                grants=rows,
            )
        )
    return response
