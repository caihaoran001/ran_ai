from __future__ import annotations

from pydantic import BaseModel, Field


class QuestionnaireTemplateRead(BaseModel):
    name: str
    fields: list[dict]


class QuestionnaireTemplateUpdate(BaseModel):
    name: str = "燃AI 1.0 问卷"
    fields: list[dict] = Field(default_factory=list)
