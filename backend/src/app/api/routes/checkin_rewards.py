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
from app.models.service_case import ServiceCase
from app.schemas.checkin_reward import (
    MarkShipmentRead,
    RewardAdminCaseRead,
    RewardQualificationRead,
    RewardShipmentRead,
)
from app.services.checkin_rewards import (
    OPEN_SHIPMENT_STATUSES,
    _qualification_display,
    deliver_pending_shipment_notices,
    valid_reward_checkin_dates,
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

    response: list[RewardAdminCaseRead] = []
    for service_case in service_cases:
        qualifications = qualifications_by_case.get(service_case.id, [])
        shipments = shipments_by_case.get(service_case.id, [])
        program = programs.get(service_case.id)
        if not qualifications and not shipments:
            continue
        shipped_ids = {row.id for row in shipments if row.status == "shipped"}
        unshipped = [row for row in qualifications if row.shipment_id not in shipped_ids]
        valid_dates = await valid_reward_checkin_dates(
            session,
            service_case=service_case,
        )
        response.append(
            RewardAdminCaseRead(
                service_case_id=service_case.id,
                case_no=service_case.case_no,
                user_name=service_case.user.name if service_case.user else "未绑定用户",
                group_id=service_case.group_id,
                group_name=service_case.group_name,
                service_status=service_case.status.value,
                complete_checkin_days=len(valid_dates),
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
