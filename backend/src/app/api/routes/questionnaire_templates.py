from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.schemas.questionnaire_template import (
    QuestionnaireTemplateRead,
    QuestionnaireTemplateUpdate,
)
from app.services.questionnaire_template import (
    get_active_questionnaire_fields,
    save_active_questionnaire_fields,
)

router = APIRouter()


@router.get("", response_model=QuestionnaireTemplateRead)
async def get_template(
    session: AsyncSession = Depends(get_session),
) -> QuestionnaireTemplateRead:
    fields = await get_active_questionnaire_fields(session)
    return QuestionnaireTemplateRead(name="燃AI 1.0 问卷", fields=fields)


@router.put("", response_model=QuestionnaireTemplateRead)
async def update_template(
    payload: QuestionnaireTemplateUpdate,
    session: AsyncSession = Depends(get_session),
) -> QuestionnaireTemplateRead:
    template = await save_active_questionnaire_fields(
        session,
        payload.fields,
        name=payload.name,
    )
    return QuestionnaireTemplateRead(name=template.name, fields=template.fields_json)
