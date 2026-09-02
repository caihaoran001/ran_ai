from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.questionnaire_template import QuestionnaireTemplate
from app.models.service_case import ServiceCaseLanguage
from app.services.checkin_questionnaires import default_checkin_questionnaire_templates


INTAKE_TEMPLATE_KEY = "intake"


@lru_cache
def _load_template_json(filename: str) -> dict[str, Any]:
    path = Path(__file__).with_name(filename)
    return json.loads(path.read_text(encoding="utf-8"))


def get_questionnaire_template(language: ServiceCaseLanguage | str = ServiceCaseLanguage.ZH) -> dict[str, Any]:
    filename = "questionnaire_template_en.json" if language == "en" else "questionnaire_template.json"
    return _load_template_json(filename)


def get_questionnaire_fields(language: ServiceCaseLanguage | str = ServiceCaseLanguage.ZH) -> list[dict[str, Any]]:
    fields = get_questionnaire_template(language).get("fields", [])
    return fields if isinstance(fields, list) else []


def default_questionnaire_template(
    template_key: str = INTAKE_TEMPLATE_KEY,
    language: ServiceCaseLanguage | str = ServiceCaseLanguage.ZH,
) -> dict[str, Any]:
    if template_key == INTAKE_TEMPLATE_KEY:
        template = get_questionnaire_template(language)
        default_name = "HumanBurning AI 1.0 Assessment" if language == "en" else "燃人AI 1.0 问卷"
        default_title = "HumanBurning Intake Assessment" if language == "en" else "燃人AI服务问卷"
        default_desc = "Please complete the following information." if language == "en" else "请完成以下信息。"
        return {
            "template_key": INTAKE_TEMPLATE_KEY,
            "language": language,
            "name": str(template.get("name") or default_name),
            "title": str(template.get("title") or default_title),
            "description": str(template.get("description") or default_desc),
            "fields": get_questionnaire_fields(language),
        }
    for template in default_checkin_questionnaire_templates():
        if template.get("template_key") == template_key:
            return {**template, "language": language}
    raise KeyError(template_key)


def default_questionnaire_templates(
    language: ServiceCaseLanguage | str = ServiceCaseLanguage.ZH,
) -> list[dict[str, Any]]:
    intake = default_questionnaire_template(INTAKE_TEMPLATE_KEY, language=language)
    # 只有入营问卷具备完整英文版本。日打卡 H5 已退役，不能把其中文配置伪装成英文模板。
    if language == ServiceCaseLanguage.EN:
        return [intake]
    return [
        intake,
        *[{**template, "language": language} for template in default_checkin_questionnaire_templates()],
    ]


async def get_active_questionnaire_template(
    session: AsyncSession,
    template_key: str = INTAKE_TEMPLATE_KEY,
    language: ServiceCaseLanguage | str = ServiceCaseLanguage.ZH,
) -> dict[str, Any]:
    template = await session.scalar(
        select(QuestionnaireTemplate)
        .where(
            QuestionnaireTemplate.is_active.is_(True),
            QuestionnaireTemplate.template_key == template_key,
            QuestionnaireTemplate.language == language,
        )
        .order_by(QuestionnaireTemplate.updated_at.desc())
        .limit(1)
    )
    if template:
        return {
            "template_key": template.template_key,
            "language": template.language,
            "name": template.name,
            "title": template.title,
            "description": template.description,
            "fields": template.fields_json,
        }
    return default_questionnaire_template(template_key, language=language)


async def get_active_questionnaire_fields(
    session: AsyncSession,
    template_key: str = INTAKE_TEMPLATE_KEY,
    language: ServiceCaseLanguage | str = ServiceCaseLanguage.ZH,
) -> list[dict[str, Any]]:
    return list((await get_active_questionnaire_template(session, template_key, language=language)).get("fields") or [])


async def get_all_active_questionnaire_templates(
    session: AsyncSession,
    language: ServiceCaseLanguage | str = ServiceCaseLanguage.ZH,
) -> list[dict[str, Any]]:
    templates = {item["template_key"]: item for item in default_questionnaire_templates(language=language)}
    active = list(
        await session.scalars(
            select(QuestionnaireTemplate)
            .where(
                QuestionnaireTemplate.is_active.is_(True),
                QuestionnaireTemplate.language == language,
            )
            .order_by(QuestionnaireTemplate.template_key.asc(), QuestionnaireTemplate.updated_at.desc())
        )
    )
    for template in active:
        templates[template.template_key] = {
            "template_key": template.template_key,
            "language": template.language,
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
    name: str = "燃人AI 1.0 问卷",
    template_key: str = INTAKE_TEMPLATE_KEY,
    language: str = "zh",
    title: str | None = None,
    description: str | None = None,
) -> QuestionnaireTemplate:
    prior = list(
        await session.scalars(
            select(QuestionnaireTemplate).where(
                QuestionnaireTemplate.is_active.is_(True),
                QuestionnaireTemplate.template_key == template_key,
                QuestionnaireTemplate.language == language,
            )
        )
    )
    for item in prior:
        item.is_active = False
    template = QuestionnaireTemplate(
        template_key=template_key,
        language=language,
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
    language: str = "zh",
) -> list[dict[str, Any]]:
    for template in templates:
        template_key = str(template.get("template_key") or INTAKE_TEMPLATE_KEY)
        await save_active_questionnaire_fields(
            session,
            list(template.get("fields") or []),
            name=str(template.get("name") or template_key),
            template_key=template_key,
            # 批量编辑的语言由接口参数决定，不能让旧页面 payload 里的值把
            # 英文模板写回中文分区，或跨语言覆盖另一份模板。
            language=language,
            title=template.get("title"),
            description=template.get("description"),
        )
    return await get_all_active_questionnaire_templates(session, language=language)
