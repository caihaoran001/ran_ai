from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import Timestamped


class KnowledgeDraftRead(Timestamped):
    id: int
    draft_type: str
    status: str
    title: str
    question: str
    answer: str
    category: str | None = None
    tags: list[str] | None = None
    reason: str | None = None
    evidence: str | None = None
    sanitized_text: str | None = None
    source_message_ids: list[int] | None = None
    content_json: dict | None = None
    content_hash: str
    published_to: str | None = None
    approved_at: datetime | None = None
    rejected_at: datetime | None = None
    rejected_reason: str | None = None


class KnowledgeDraftUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    question: str | None = Field(default=None, min_length=1)
    answer: str | None = Field(default=None, min_length=1)
    category: str | None = None
    tags: list[str] | None = None
    reason: str | None = None
    evidence: str | None = None


class KnowledgeDraftReject(BaseModel):
    reason: str | None = None


class KnowledgeDraftApprovalRequest(BaseModel):
    resolution: Literal["use_candidate", "keep_existing"] | None = None


class KnowledgeIndexSyncRequest(BaseModel):
    embed: bool = False
    force_embedding: bool = False


class KnowledgeIndexSyncResult(BaseModel):
    scanned: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    embedded: int = 0
    embedding_skipped: int = 0


class KnowledgeIndexStats(BaseModel):
    total_chunks: int = 0
    embedded_chunks: int = 0
    by_source_type: dict[str, int] = Field(default_factory=dict)
