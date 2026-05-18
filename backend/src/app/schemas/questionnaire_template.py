from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class QuestionnaireTemplateRead(BaseModel):
    name: str
    template_key: str = "intake"
    title: str | None = None
    description: str | None = None
    fields: list[dict[str, Any]]


class QuestionnaireTemplateUpdate(BaseModel):
    name: str = "燃AI 1.0 问卷"
    template_key: str = "intake"
    title: str | None = None
    description: str | None = None
    fields: list[dict[str, Any]] = Field(default_factory=list)


class QuestionnaireTemplateConfigRead(BaseModel):
    templates: list[QuestionnaireTemplateRead]
    schedule: dict[str, Any]


class QuestionnaireTemplateConfigUpdate(BaseModel):
    templates: list[QuestionnaireTemplateUpdate] = Field(default_factory=list)
    schedule: dict[str, Any] = Field(default_factory=dict)
