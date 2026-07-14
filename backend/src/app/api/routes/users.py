from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_session
from app.models.service_case import ServiceCase
from app.models.user import User
from app.models.user_profile import UserProfile
from app.models.user_state_profile import UserStateProfile
from app.schemas.user import UserDetail, UserListItem
from app.services.intake_facts import customer_type_for_profile

router = APIRouter()


@router.get("", response_model=list[UserListItem])
async def list_users(
    deleted: bool = False,
    session: AsyncSession = Depends(get_session),
) -> list[User]:
    deleted_filter = User.deleted_at.is_not(None) if deleted else User.deleted_at.is_(None)
    result = await session.scalars(
        select(User)
        .where(deleted_filter)
        .options(selectinload(User.service_cases).selectinload(ServiceCase.user))
        .order_by((User.deleted_at if deleted else User.created_at).desc())
    )
    users = list(result)
    for u in users:
        if not deleted:
            u.service_cases = [sc for sc in u.service_cases if sc.deleted_at is None]
    return users


@router.get("/{user_id}", response_model=UserDetail)
async def get_user(
    user_id: int,
    session: AsyncSession = Depends(get_session),
) -> UserDetail:
    user = await session.scalar(
        select(User)
        .where(User.id == user_id)
        .options(selectinload(User.service_cases).selectinload(ServiceCase.user))
    )
    if not user:
        raise HTTPException(status_code=404, detail="user not found")

    if not user.deleted_at:
        user.service_cases = [sc for sc in user.service_cases if sc.deleted_at is None]
    latest_profile = None
    latest_state_profile = None
    current_case = max(
        user.service_cases,
        key=lambda case: case.created_at,
        default=None,
    )
    if current_case:
        latest_profile = await session.scalar(
            select(UserProfile)
            .where(UserProfile.service_case_id == current_case.id)
            .order_by(UserProfile.created_at.desc())
            .limit(1)
        )
        latest_state_profile = await session.scalar(
            select(UserStateProfile)
            .where(UserStateProfile.service_case_id == current_case.id)
            .order_by(UserStateProfile.updated_at.desc())
            .limit(1)
        )

    data = UserDetail.model_validate(user)
    if latest_profile:
        profile_data = latest_profile.profile_json if isinstance(latest_profile.profile_json, dict) else {}
        data.latest_profile = latest_profile
        data.latest_profile.profile_json = {
            **profile_data,
            "customer_type": customer_type_for_profile(profile_data, current_case.risk_level),
        }
    data.latest_state_profile = latest_state_profile
    return data


@router.delete("/{user_id}", response_model=UserDetail)
async def delete_user(
    user_id: int,
    session: AsyncSession = Depends(get_session),
) -> UserDetail:
    user = await session.scalar(
        select(User)
        .where(User.id == user_id, User.deleted_at.is_(None))
        .options(selectinload(User.service_cases).selectinload(ServiceCase.user))
    )
    if not user:
        raise HTTPException(status_code=404, detail="user not found")
    if not user.deleted_at:
        deleted_at = datetime.now(timezone.utc)
        user.deleted_at = deleted_at
        for service_case in user.service_cases:
            if not service_case.deleted_at:
                service_case.deleted_at = deleted_at
        await session.commit()
        await session.refresh(user)

    data = UserDetail.model_validate(user)
    data.latest_profile = None
    return data
