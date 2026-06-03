from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import Timestamped


class QuestionnairePublic(BaseModel):
    service_case_id: int
    token: str
    template_key: str = "intake"
    title: str | None = None
    description: str | None = None
    allow_repeat: bool = False
    user_name: str | None
    fields: list[dict[str, Any]]
    latest_submission: QuestionnaireSubmissionRead | None = None
    default_values: dict[str, Any] = Field(default_factory=dict)


class QuestionnaireSubmit(BaseModel):
    answers: dict[str, Any] = Field(default_factory=dict)


class QuestionnaireSubmissionRead(Timestamped):
    id: int
    service_case_id: int
    template_key: str = "intake"
    answers_json: dict[str, Any]
