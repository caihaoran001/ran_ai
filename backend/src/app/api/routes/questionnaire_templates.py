from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.schemas.questionnaire_template import (
    QuestionnaireTemplateConfigRead,
    QuestionnaireTemplateConfigUpdate,
    QuestionnaireTemplateRead,
    QuestionnaireTemplateUpdate,
)
from app.services.checkin_questionnaires import (
    default_checkin_schedule,
    get_checkin_questionnaire_schedule,
    save_checkin_questionnaire_schedule,
)
from app.services.questionnaire_template import (
    default_questionnaire_templates,
    get_active_questionnaire_template,
    get_all_active_questionnaire_templates,
    get_active_questionnaire_fields,
    save_questionnaire_templates,
    save_active_questionnaire_fields,
)

router = APIRouter()


@router.get("", response_model=QuestionnaireTemplateRead)
async def get_template(
    language: str = Query(default="zh"),
    session: AsyncSession = Depends(get_session),
) -> QuestionnaireTemplateRead:
    template = await get_active_questionnaire_template(session, language=language)
    return QuestionnaireTemplateRead(**template)


@router.put("", response_model=QuestionnaireTemplateRead)
async def update_template(
    payload: QuestionnaireTemplateUpdate,
    session: AsyncSession = Depends(get_session),
) -> QuestionnaireTemplateRead:
    template = await save_active_questionnaire_fields(
        session,
        payload.fields,
        name=payload.name,
        template_key=payload.template_key,
        language=payload.language or "zh",
        title=payload.title,
        description=payload.description,
    )
    return QuestionnaireTemplateRead(
        name=template.name,
        template_key=template.template_key,
        language=template.language,
        title=template.title,
        description=template.description,
        fields=template.fields_json,
    )


@router.get("/config", response_model=QuestionnaireTemplateConfigRead)
async def get_template_config(
    language: str = Query(default="zh"),
    session: AsyncSession = Depends(get_session),
) -> QuestionnaireTemplateConfigRead:
    templates = [
        QuestionnaireTemplateRead(**item)
        for item in await get_all_active_questionnaire_templates(session, language=language)
    ]
    schedule = await get_checkin_questionnaire_schedule(session)
    return QuestionnaireTemplateConfigRead(templates=templates, schedule=schedule)


@router.put("/config", response_model=QuestionnaireTemplateConfigRead)
async def update_template_config(
    payload: QuestionnaireTemplateConfigUpdate,
    language: str = Query(default="zh"),
    session: AsyncSession = Depends(get_session),
) -> QuestionnaireTemplateConfigRead:
    templates = [
        QuestionnaireTemplateRead(**item)
        for item in await save_questionnaire_templates(
            session,
            [item.model_dump() for item in payload.templates],
            language=language,
        )
    ]
    schedule = await save_checkin_questionnaire_schedule(session, payload.schedule)
    return QuestionnaireTemplateConfigRead(templates=templates, schedule=schedule)


@router.post("/reset", response_model=QuestionnaireTemplateConfigRead)
async def reset_templates(
    template_key: str | None = Query(default=None),
    language: str = Query(default="zh"),
    session: AsyncSession = Depends(get_session),
) -> QuestionnaireTemplateConfigRead:
    defaults = default_questionnaire_templates(language=language)
    if template_key:
        defaults = [t for t in defaults if t.get("template_key") == template_key]
    templates = [
        QuestionnaireTemplateRead(**item)
        for item in await save_questionnaire_templates(session, defaults, language=language)
    ]
    schedule = (
        await save_checkin_questionnaire_schedule(session, default_checkin_schedule())
        if template_key is None
        else await get_checkin_questionnaire_schedule(session)
    )
    return QuestionnaireTemplateConfigRead(templates=templates, schedule=schedule)
