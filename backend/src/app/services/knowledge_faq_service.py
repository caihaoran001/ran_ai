from __future__ import annotations

import secrets
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge_faq import KnowledgeFAQ, KnowledgeFAQVersion
from app.services.knowledge_index import upsert_text_chunk


async def create_faq(
    session: AsyncSession,
    *,
    title: str,
    question: str,
    answer: str,
    category: str | None = None,
    tags: list[str] | None = None,
    source_type: str | None = None,
    source_id: str | None = None,
    change_note: str | None = "创建 FAQ",
) -> KnowledgeFAQ:
    faq = KnowledgeFAQ(
        faq_uid=f"faq_{secrets.token_hex(8)}",
        status="active",
        title=title,
        question=question,
        answer=answer,
        category=category,
        tags=tags or [],
        source_type=source_type,
        source_id=source_id,
        version=1,
    )
    session.add(faq)
    await session.flush()
    _add_version(session, faq, change_note)
    await session.commit()
    await session.refresh(faq)
    await sync_faq_to_index(session, faq)
    return faq


async def update_faq(
    session: AsyncSession,
    faq: KnowledgeFAQ,
    *,
    title: str | None = None,
    question: str | None = None,
    answer: str | None = None,
    category: str | None = None,
    tags: list[str] | None = None,
    source_type: str | None = None,
    source_id: str | None = None,
    change_note: str | None = None,
) -> KnowledgeFAQ:
    if title is not None:
        faq.title = title
    if question is not None:
        faq.question = question
    if answer is not None:
        faq.answer = answer
    if category is not None:
        faq.category = category
    if tags is not None:
        faq.tags = tags
    if source_type is not None:
        faq.source_type = source_type
    if source_id is not None:
        faq.source_id = source_id
    faq.version += 1
    faq.status = "active"
    faq.disabled_at = None
    _add_version(session, faq, change_note or "更新 FAQ")
    await session.commit()
    await session.refresh(faq)
    await sync_faq_to_index(session, faq)
    return faq


async def set_faq_status(session: AsyncSession, faq: KnowledgeFAQ, status: str) -> KnowledgeFAQ:
    faq.status = status
    now = datetime.now(timezone.utc)
    if status == "disabled":
        faq.disabled_at = now
    elif status == "archived":
        faq.archived_at = now
    elif status == "active":
        faq.disabled_at = None
        faq.archived_at = None
    await session.commit()
    await session.refresh(faq)
    await sync_faq_to_index(session, faq)
    return faq


async def sync_faq_to_index(session: AsyncSession, faq: KnowledgeFAQ) -> None:
    content = (
        f"## {faq.title}\n\n"
        f"**用户常见问法**\n- {faq.question}\n\n"
        f"**参考回复**\n- {faq.answer}\n"
    )
    chunk = await upsert_text_chunk(
        session,
        chunk_uid=f"faq:{faq.id}",
        source_type="faq",
        source_uri=f"knowledge_faq:{faq.id}",
        title=faq.title,
        content=content,
        metadata={
            "faq_id": faq.id,
            "faq_uid": faq.faq_uid,
            "version": faq.version,
            "category": faq.category,
            "tags": faq.tags or [],
        },
        embed=faq.status == "active",
    )
    if faq.status != "active":
        chunk.status = faq.status
        await session.commit()


def _add_version(session: AsyncSession, faq: KnowledgeFAQ, change_note: str | None) -> None:
    session.add(
        KnowledgeFAQVersion(
            faq=faq,
            version=faq.version,
            title=faq.title,
            question=faq.question,
            answer=faq.answer,
            category=faq.category,
            tags=faq.tags or [],
            change_note=change_note,
        )
    )
