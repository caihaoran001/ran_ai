from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.models.user_document import DocumentStatus, DocumentType
from app.schemas.common import Timestamped


class UserProfileRead(Timestamped):
    id: int
    service_case_id: int
    profile_json: dict
    summary: str
    generated_by_model: str | None


class DocumentUpdate(BaseModel):
    title: str | None = None
    content_md: str | None = None
    status: DocumentStatus | None = None


class UserDocumentRead(Timestamped):
    id: int
    service_case_id: int
    document_type: DocumentType
    title: str
    content_md: str
    file_path: str | None
    status: DocumentStatus
    generated_by_model: str | None
    reviewed_at: datetime | None
    sent_at: datetime | None
