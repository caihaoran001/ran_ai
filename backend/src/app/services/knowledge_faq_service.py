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
    # 口径字段（title/question/answer）实际变化才升版本并留快照；
    # 只改 category/tags/source_type/source_id 时原位更新，版本号不变、不产生新版本。
    content_changed = False
    if title is not None and title != faq.title:
        faq.title = title
        content_changed = True
    if question is not None and question != faq.question:
        faq.question = question
        content_changed = True
    if answer is not None and answer != faq.answer:
        faq.answer = answer
        content_changed = True
    if category is not None:
        faq.category = category
    if tags is not None:
        faq.tags = tags
    if source_type is not None:
        faq.source_type = source_type
    if source_id is not None:
        faq.source_id = source_id
    faq.status = "active"
    faq.disabled_at = None
    if content_changed:
        faq.version += 1
        _add_version(session, faq, change_note or "更新 FAQ")
    await session.commit()
    await session.refresh(faq)
    await sync_faq_to_index(session, faq)
    return faq


async def confirm_faq(session: AsyncSession, faq: KnowledgeFAQ) -> KnowledgeFAQ:
    """确认当前版本。启用/停用状态与此无关，允许对停用条目确认。"""
    faq.last_confirmed_version = faq.version
    faq.last_confirmed_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(faq)
    await sync_faq_to_index(session, faq)
    return faq


async def unconfirm_faq(session: AsyncSession, faq: KnowledgeFAQ) -> KnowledgeFAQ:
    """手动取消确认：清空指向（区别于编辑失效——编辑失效保留上次确认记录供追溯）。"""
    faq.last_confirmed_version = None
    faq.last_confirmed_at = None
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
            "confirm_state": faq.confirmation_state,
            "last_confirmed_version": faq.last_confirmed_version,
            "last_confirmed_at": faq.last_confirmed_at.isoformat() if faq.last_confirmed_at else None,
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
