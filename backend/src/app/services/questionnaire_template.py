from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.questionnaire_template import QuestionnaireTemplate
from app.services.checkin_questionnaires import default_checkin_questionnaire_templates


INTAKE_TEMPLATE_KEY = "intake"


@lru_cache
def get_questionnaire_template() -> dict[str, Any]:
    path = Path(__file__).with_name("questionnaire_template.json")
    return json.loads(path.read_text(encoding="utf-8"))


def get_questionnaire_fields() -> list[dict[str, Any]]:
    fields = get_questionnaire_template().get("fields", [])
    return fields if isinstance(fields, list) else []


def default_questionnaire_template(template_key: str = INTAKE_TEMPLATE_KEY) -> dict[str, Any]:
    if template_key == INTAKE_TEMPLATE_KEY:
        template = get_questionnaire_template()
        return {
            "template_key": INTAKE_TEMPLATE_KEY,
            "name": str(template.get("name") or "燃AI 1.0 问卷"),
            "title": str(template.get("title") or "燃AI服务问卷"),
            "description": str(template.get("description") or "请完成以下信息。"),
            "fields": get_questionnaire_fields(),
        }
    for template in default_checkin_questionnaire_templates():
        if template.get("template_key") == template_key:
            return template
    raise KeyError(template_key)


def default_questionnaire_templates() -> list[dict[str, Any]]:
    return [default_questionnaire_template(INTAKE_TEMPLATE_KEY), *default_checkin_questionnaire_templates()]


async def get_active_questionnaire_template(
    session: AsyncSession,
    template_key: str = INTAKE_TEMPLATE_KEY,
) -> dict[str, Any]:
    template = await session.scalar(
        select(QuestionnaireTemplate)
        .where(
            QuestionnaireTemplate.is_active.is_(True),
            QuestionnaireTemplate.template_key == template_key,
        )
        .order_by(QuestionnaireTemplate.updated_at.desc())
        .limit(1)
    )
    if template:
        return {
            "template_key": template.template_key,
            "name": template.name,
            "title": template.title,
            "description": template.description,
            "fields": template.fields_json,
        }
    return default_questionnaire_template(template_key)


async def get_active_questionnaire_fields(
    session: AsyncSession,
    template_key: str = INTAKE_TEMPLATE_KEY,
) -> list[dict[str, Any]]:
    return list((await get_active_questionnaire_template(session, template_key)).get("fields") or [])


async def get_all_active_questionnaire_templates(session: AsyncSession) -> list[dict[str, Any]]:
    templates = {item["template_key"]: item for item in default_questionnaire_templates()}
    active = list(
        await session.scalars(
            select(QuestionnaireTemplate)
            .where(QuestionnaireTemplate.is_active.is_(True))
            .order_by(QuestionnaireTemplate.template_key.asc(), QuestionnaireTemplate.updated_at.desc())
        )
    )
    for template in active:
        templates[template.template_key] = {
            "template_key": template.template_key,
            "name": template.name,
            "title": template.title,
            "description": template.description,
            "fields": template.fields_json,
        }
    return list(templates.values())


async def save_active_questionnaire_fields(
    session: AsyncSession,
    fields: list[dict[str, Any]],
    *,
    name: str = "燃AI 1.0 问卷",
    template_key: str = INTAKE_TEMPLATE_KEY,
    title: str | None = None,
    description: str | None = None,
) -> QuestionnaireTemplate:
    prior = list(
        await session.scalars(
            select(QuestionnaireTemplate).where(
                QuestionnaireTemplate.is_active.is_(True),
                QuestionnaireTemplate.template_key == template_key,
            )
        )
    )
    for item in prior:
        item.is_active = False
    template = QuestionnaireTemplate(
        template_key=template_key,
        name=name,
        title=title,
        description=description,
        fields_json=fields,
        is_active=True,
    )
    session.add(template)
    await session.commit()
    await session.refresh(template)
    return template


async def save_questionnaire_templates(
    session: AsyncSession,
    templates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    for template in templates:
        template_key = str(template.get("template_key") or INTAKE_TEMPLATE_KEY)
        await save_active_questionnaire_fields(
            session,
            list(template.get("fields") or []),
            name=str(template.get("name") or template_key),
            template_key=template_key,
            title=template.get("title"),
            description=template.get("description"),
        )
    return await get_all_active_questionnaire_templates(session)
