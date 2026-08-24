from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import Timestamped


class KnowledgeFAQVersionRead(Timestamped):
    id: int
    faq_id: int
    version: int
    title: str
    question: str
    answer: str
    category: str | None = None
    tags: list[str] | None = None
    change_note: str | None = None


class KnowledgeFAQRead(Timestamped):
    id: int
    faq_uid: str
    status: str
    title: str
    question: str
    answer: str
    category: str | None = None
    tags: list[str] | None = None
    source_type: str | None = None
    source_id: str | None = None
    version: int
    disabled_at: datetime | None = None
    archived_at: datetime | None = None
    confirmation_state: str
    last_confirmed_version: int | None = None
    last_confirmed_at: datetime | None = None


class KnowledgeFAQCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=1)
    answer: str = Field(min_length=1)
    category: str | None = None
    tags: list[str] = Field(default_factory=list)


class KnowledgeFAQUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    question: str | None = Field(default=None, min_length=1)
    answer: str | None = Field(default=None, min_length=1)
    category: str | None = None
    tags: list[str] | None = None
    change_note: str | None = None
