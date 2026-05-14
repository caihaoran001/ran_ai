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
from app.schemas.user import UserDetail, UserListItem

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
    return list(result)


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

    case_ids = [case.id for case in user.service_cases]
    latest_profile = None
    if case_ids:
        latest_profile = await session.scalar(
            select(UserProfile)
            .where(UserProfile.service_case_id.in_(case_ids))
            .order_by(UserProfile.created_at.desc())
            .limit(1)
        )

    data = UserDetail.model_validate(user)
    data.latest_profile = latest_profile
    return data


@router.delete("/{user_id}", response_model=UserDetail)
async def delete_user(
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
