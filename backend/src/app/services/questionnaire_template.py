from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.questionnaire_template import QuestionnaireTemplate


@lru_cache
def get_questionnaire_template() -> dict[str, Any]:
    path = Path(__file__).with_name("questionnaire_template.json")
    return json.loads(path.read_text(encoding="utf-8"))


def get_questionnaire_fields() -> list[dict[str, Any]]:
    fields = get_questionnaire_template().get("fields", [])
    return fields if isinstance(fields, list) else []


async def get_active_questionnaire_fields(session: AsyncSession) -> list[dict[str, Any]]:
    template = await session.scalar(
        select(QuestionnaireTemplate)
        .where(QuestionnaireTemplate.is_active.is_(True))
        .order_by(QuestionnaireTemplate.updated_at.desc())
        .limit(1)
    )
    if template:
        return template.fields_json
    return get_questionnaire_fields()


async def save_active_questionnaire_fields(
    session: AsyncSession,
    fields: list[dict[str, Any]],
    *,
    name: str = "燃AI 1.0 问卷",
) -> QuestionnaireTemplate:
    prior = list(
        await session.scalars(
            select(QuestionnaireTemplate).where(QuestionnaireTemplate.is_active.is_(True))
        )
    )
    for item in prior:
        item.is_active = False
    template = QuestionnaireTemplate(name=name, fields_json=fields, is_active=True)
    session.add(template)
    await session.commit()
    await session.refresh(template)
    return template
