from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import Timestamped


class QuestionnairePublic(BaseModel):
    service_case_id: int
    token: str
    user_name: str | None
    fields: list[dict[str, Any]]
    latest_submission: QuestionnaireSubmissionRead | None = None


class QuestionnaireSubmit(BaseModel):
    answers: dict[str, Any] = Field(default_factory=dict)


class QuestionnaireSubmissionRead(Timestamped):
    id: int
    service_case_id: int
    answers_json: dict[str, Any]
