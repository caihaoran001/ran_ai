from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.db.session import get_session
from app.models.questionnaire import QuestionnaireSubmission
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user import User
from app.models.user_profile import UserProfile
from app.schemas.questionnaire import (
    QuestionnairePublic,
    QuestionnaireSubmissionRead,
    QuestionnaireSubmit,
)
from app.services.document_service import generate_profile, generate_documents
from app.db.session import SessionLocal
from app.services.questionnaire_template import get_active_questionnaire_fields

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/{token}", response_model=QuestionnairePublic)
async def get_questionnaire(
    token: str,
    session: AsyncSession = Depends(get_session),
) -> QuestionnairePublic:
    service_case = await _case_by_token(session, token, include_deleted=True)
    latest_submission = await _latest_submission(session, service_case.id)
    return QuestionnairePublic(
        service_case_id=service_case.id,
        token=token,
        user_name=service_case.user.name if service_case.user else None,
        fields=await get_active_questionnaire_fields(session),
        latest_submission=latest_submission,
    )


@router.post(
    "/{token}/submit",
    response_model=QuestionnaireSubmissionRead,
    status_code=status.HTTP_201_CREATED,
)
async def submit_questionnaire(
    token: str,
    payload: QuestionnaireSubmit,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> QuestionnaireSubmission:
    service_case = await _case_by_token(session, token, include_deleted=False)
    if await _latest_submission(session, service_case.id):
        raise HTTPException(status_code=409, detail="questionnaire already submitted")
    fields = await get_active_questionnaire_fields(session)
    submission = QuestionnaireSubmission(
        service_case_id=service_case.id,
        answers_json=payload.answers,
    )
    service_case.status = ServiceCaseStatus.QUESTIONNAIRE_SUBMITTED
    await _sync_user_from_answers(session, service_case, fields, payload.answers)
    session.add(submission)
    await session.commit()
    await session.refresh(submission)
    if get_settings().ai_api_key:
        background_tasks.add_task(_generate_profile_and_documents, service_case.id)
    return submission


async def _case_by_token(
    session: AsyncSession,
    token: str,
    *,
    include_deleted: bool,
) -> ServiceCase:
    conditions = [ServiceCase.questionnaire_token == token]
    if not include_deleted:
        conditions.append(ServiceCase.deleted_at.is_(None))
    service_case = await session.scalar(
        select(ServiceCase)
        .where(*conditions)
        .options(selectinload(ServiceCase.user))
    )
    if not service_case:
        raise HTTPException(status_code=404, detail="questionnaire not found")
    return service_case


async def _latest_submission(
    session: AsyncSession, service_case_id: int
) -> QuestionnaireSubmission | None:
    return await session.scalar(
        select(QuestionnaireSubmission)
        .where(QuestionnaireSubmission.service_case_id == service_case_id)
        .order_by(QuestionnaireSubmission.created_at.desc())
        .limit(1)
    )


async def _generate_profile_and_documents(service_case_id: int) -> None:
    async with SessionLocal() as session:
        existing = await session.scalar(
            select(UserProfile.id)
            .where(UserProfile.service_case_id == service_case_id)
            .limit(1)
        )
        if existing:
            return
        service_case = await session.get(ServiceCase, service_case_id)
        if not service_case:
            return
        try:
            await generate_profile(session, service_case)
            await generate_documents(session, service_case, auto_send=get_settings().auto_send_documents)
        except Exception:
            logger.exception("Failed to generate profile and documents for service_case_id=%s", service_case_id)


async def _sync_user_from_answers(
    session: AsyncSession,
    service_case: ServiceCase,
    fields: list[dict],
    answers: dict,
) -> None:
    user = service_case.user
    if not user and service_case.user_id:
        user = await session.get(User, service_case.user_id)
    if not user:
        return

    name = _answer_by_label(fields, answers, ("真实姓名", "姓名", "name"))
    phone = _answer_by_label(fields, answers, ("手机号", "手机", "phone"))
    if name:
        user.name = name[:100]
    if phone and not user.phone:
        user.phone = phone[:32]


def _answer_by_label(fields: list[dict], answers: dict, keywords: tuple[str, ...]) -> str | None:
    for field in fields:
        key = field.get("key") or field.get("id")
        if not key:
            continue
        label = str(field.get("title") or field.get("label") or "")
        if any(keyword in label for keyword in keywords):
            value = answers.get(key)
            if isinstance(value, str):
                value = value.strip()
                if value:
                    return value
    for key, value in answers.items():
        if any(keyword.lower() in str(key).lower() for keyword in keywords):
            if isinstance(value, str):
                value = value.strip()
                if value:
                    return value
    return None
