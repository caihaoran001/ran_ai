from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, NoReturn

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge_draft import KnowledgeDraft
from app.models.knowledge_chunk import KnowledgeChunk
from app.models.knowledge_faq import KnowledgeFAQ
from app.models.message import Message, MessageSenderType
from app.models.service_case import ServiceCase
from app.services.ai_service import AIService
from app.services.knowledge_dedupe import (
    KnowledgeBusinessConflict,
    KnowledgeCandidate,
    KnowledgeDedupeDecision,
    KnowledgeDedupeUnavailable,
    assess_knowledge_draft,
    business_conflict_detail,
    knowledge_draft_idempotency_hash,
    load_pending_conflict,
    record_dedupe_event,
)
from app.services.knowledge_faq_service import create_faq, update_faq
from app.services.knowledge_index import upsert_text_chunk
from app.services.knowledge_base import get_knowledge_base
from app.services.user_state_profile import apply_staff_guidance_to_user_state


logger = logging.getLogger(__name__)


async def approve_draft(
    session: AsyncSession,
    draft: KnowledgeDraft,
    *,
    resolution: str | None = None,
    ai: AIService | None = None,
) -> KnowledgeDraft:
    if draft.status == "approved":
        return draft
    if draft.status != "pending":
        raise ValueError("only pending drafts can be approved")

    existing_target = await _existing_publication(session, draft)
    if existing_target is not None:
        decision = KnowledgeDedupeDecision(
            action="create",
            relation="recovered_publication",
            answer_relation="equivalent",
            reason="检测到该草稿已完成知识发布，已恢复草稿终态。",
            target=existing_target,
        )
        await record_dedupe_event(
            session,
            draft,
            decision,
            resolution="recovered_publication",
            resolved_by="system",
        )
        return await _finalize_approved(session, draft, _target_uri(existing_target))

    if resolution is not None:
        conflict = await load_pending_conflict(session, draft)
        if conflict is None:
            raise KnowledgeBusinessConflict(
                {
                    "code": "knowledge_conflict_expired",
                    "message": "草稿内容或现有知识已变化，请重新执行查重。",
                }
            )
        if resolution == "keep_existing":
            resolved = KnowledgeDedupeDecision(
                action="skip",
                relation=conflict.relation,
                answer_relation=conflict.answer_relation,
                reason=f"审核员确认保留已入库口径。原冲突：{conflict.reason}",
                target=conflict.target,
            )
            await record_dedupe_event(
                session,
                draft,
                resolved,
                resolution="reviewer_kept_existing",
                resolved_by="reviewer",
            )
            return await _finalize_skipped(session, draft, resolved)
        if resolution == "use_candidate":
            resolved = KnowledgeDedupeDecision(
                action="update",
                relation=conflict.relation,
                answer_relation=conflict.answer_relation,
                reason=f"审核员确认采用本次候选口径。原冲突：{conflict.reason}",
                target=conflict.target,
            )
            try:
                target_uri = await _apply_update_decision(session, draft, resolved)
            except Exception as exc:
                await _raise_apply_failure(session, draft, resolved, exc)
            await record_dedupe_event(
                session,
                draft,
                resolved,
                resolution="reviewer_used_candidate",
                resolved_by="reviewer",
            )
            return await _finalize_approved(session, draft, target_uri)
        raise ValueError("unsupported knowledge conflict resolution")

    cached_conflict = await load_pending_conflict(session, draft)
    if cached_conflict is not None:
        raise KnowledgeBusinessConflict(business_conflict_detail(draft, cached_conflict))

    try:
        decision = await assess_knowledge_draft(session, draft, ai=ai)
    except KnowledgeDedupeUnavailable as exc:
        failed = KnowledgeDedupeDecision(
            action="error",
            relation="error",
            answer_relation=None,
            reason=str(exc),
        )
        await record_dedupe_event(
            session,
            draft,
            failed,
            resolution="check_failed",
            resolved_by="system",
        )
        await session.commit()
        raise

    if decision.action == "conflict":
        await record_dedupe_event(
            session,
            draft,
            decision,
            resolution="pending_conflict",
            resolved_by="system",
        )
        await session.commit()
        raise KnowledgeBusinessConflict(business_conflict_detail(draft, decision))
    if decision.action == "skip":
        await record_dedupe_event(
            session,
            draft,
            decision,
            resolution="skipped_duplicate",
            resolved_by="system",
        )
        return await _finalize_skipped(session, draft, decision)
    if decision.action == "update":
        try:
            target_uri = await _apply_update_decision(session, draft, decision)
        except Exception as exc:
            await _raise_apply_failure(session, draft, decision, exc)
        await record_dedupe_event(
            session,
            draft,
            decision,
            resolution="updated_existing",
            resolved_by="system",
        )
        return await _finalize_approved(session, draft, target_uri)

    try:
        target_uri = await _publish_new_draft(session, draft)
    except Exception as exc:
        await _raise_apply_failure(session, draft, decision, exc)
    await record_dedupe_event(
        session,
        draft,
        decision,
        resolution="published_new",
        resolved_by="system",
    )
    return await _finalize_approved(session, draft, target_uri)


async def _raise_apply_failure(
    session: AsyncSession,
    draft: KnowledgeDraft,
    decision: KnowledgeDedupeDecision,
    exc: Exception,
) -> NoReturn:
    logger.exception(
        "Knowledge dedupe apply failed draft_id=%s action=%s",
        draft.id,
        decision.action,
    )
    await session.rollback()
    failed = KnowledgeDedupeDecision(
        action="error",
        relation=decision.relation,
        answer_relation=decision.answer_relation,
        reason=f"查重完成，但知识写入失败：{type(exc).__name__}",
        target=decision.target,
    )
    try:
        await record_dedupe_event(
            session,
            draft,
            failed,
            resolution="apply_failed",
            resolved_by="system",
        )
        await session.commit()
    except Exception:
        logger.exception("Failed to persist knowledge apply failure draft_id=%s", draft.id)
        await session.rollback()
    if isinstance(exc, KnowledgeDedupeUnavailable):
        raise exc
    raise KnowledgeDedupeUnavailable("知识写入失败，草稿仍保留在待审核列表") from exc


async def _existing_publication(
    session: AsyncSession,
    draft: KnowledgeDraft,
) -> KnowledgeCandidate | None:
    if draft.draft_type == "case":
        chunk = await session.scalar(
            select(KnowledgeChunk)
            .where(
                KnowledgeChunk.chunk_uid == f"case:{draft.id}",
                KnowledgeChunk.status == "active",
            )
            .limit(1)
        )
        if chunk is None:
            return None
        content_json = draft.content_json if isinstance(draft.content_json, dict) else {}
        try:
            service_case_id = int(content_json.get("service_case_id"))
        except (TypeError, ValueError):
            service_case_id = None
        return KnowledgeCandidate(
            chunk_id=chunk.id,
            item_id=draft.id,
            draft_type="case",
            title=draft.title,
            question=draft.question,
            answer=draft.answer,
            category=draft.category,
            tags=list(draft.tags or []),
            distance=0.0,
            source_message_ids=_positive_ids(draft.source_message_ids),
            service_case_id=service_case_id,
        )

    faq = await session.scalar(
        select(KnowledgeFAQ)
        .where(
            KnowledgeFAQ.status == "active",
            KnowledgeFAQ.source_type == "knowledge_draft",
            KnowledgeFAQ.source_id == str(draft.id),
        )
        .limit(1)
    )
    if faq is None:
        return None
    chunk = await session.scalar(
        select(KnowledgeChunk)
        .where(
            KnowledgeChunk.chunk_uid == f"faq:{faq.id}",
            KnowledgeChunk.status == "active",
        )
        .limit(1)
    )
    if chunk is None:
        return None
    return KnowledgeCandidate(
        chunk_id=chunk.id,
        item_id=faq.id,
        draft_type="qa",
        title=faq.title,
        question=faq.question,
        answer=faq.answer,
        category=faq.category,
        tags=list(faq.tags or []),
        distance=0.0,
        source_message_ids=[],
        service_case_id=None,
    )


async def _publish_new_draft(session: AsyncSession, draft: KnowledgeDraft) -> str:
    if draft.draft_type == "case":
        return await _publish_case(session, draft)
    faq = await create_faq(
        session,
        title=draft.title,
        question=draft.question,
        answer=draft.answer,
        category=draft.category,
        tags=draft.tags or [],
        source_type="knowledge_draft",
        source_id=str(draft.id),
        change_note=f"由知识草稿 #{draft.id} 批准入库",
    )
    return f"knowledge_faq:{faq.id}"


async def _apply_update_decision(
    session: AsyncSession,
    draft: KnowledgeDraft,
    decision: KnowledgeDedupeDecision,
) -> str:
    target = decision.target
    if target is None:
        raise KnowledgeDedupeUnavailable("查重更新缺少目标知识，草稿尚未入库")
    if draft.draft_type == "case":
        chunk = await session.get(KnowledgeChunk, target.chunk_id)
        if chunk is None or chunk.status != "active":
            raise KnowledgeDedupeUnavailable("待替代的案例已经变化，请重新查重")
        return await _publish_case(session, draft, supersedes_chunk=chunk)

    faq = await session.get(KnowledgeFAQ, target.item_id)
    if faq is None or faq.status != "active":
        raise KnowledgeDedupeUnavailable("待更新的参考问答已经变化，请重新查重")
    await update_faq(
        session,
        faq,
        title=draft.title,
        question=decision.canonical_question or draft.question,
        answer=decision.canonical_answer or draft.answer,
        category=draft.category,
        tags=draft.tags or [],
        source_type="knowledge_draft",
        source_id=str(draft.id),
        change_note=f"由知识草稿 #{draft.id} 语义归并更新：{decision.reason}",
    )
    return f"knowledge_faq:{faq.id}"


async def _finalize_approved(
    session: AsyncSession,
    draft: KnowledgeDraft,
    target_uri: str,
) -> KnowledgeDraft:
    draft.status = "approved"
    draft.approved_at = datetime.now(timezone.utc)
    draft.rejected_at = None
    draft.rejected_reason = None
    draft.published_to = target_uri
    await session.commit()
    await session.refresh(draft)
    get_knowledge_base.cache_clear()
    return draft


async def _finalize_skipped(
    session: AsyncSession,
    draft: KnowledgeDraft,
    decision: KnowledgeDedupeDecision,
) -> KnowledgeDraft:
    draft.status = "rejected"
    draft.rejected_at = datetime.now(timezone.utc)
    draft.rejected_reason = f"系统查重：{decision.reason}"
    draft.published_to = _target_uri(decision.target) if decision.target else None
    await session.commit()
    await session.refresh(draft)
    return draft


def _target_uri(target: KnowledgeCandidate) -> str:
    if target.draft_type == "case":
        return f"knowledge_chunk:{target.chunk_id}"
    return f"knowledge_faq:{target.item_id}"


async def create_draft_candidate(
    session: AsyncSession,
    *,
    draft_type: str,
    title: str,
    question: str,
    answer: str,
    category: str | None = None,
    tags: list[str] | None = None,
    reason: str | None = None,
    evidence: str | None = None,
    sanitized_text: str | None = None,
    source_message_ids: list[int] | None = None,
    content_json: dict[str, Any] | None = None,
) -> KnowledgeDraft | None:
    """创建待审核草稿，不提交事务；供知识扫描和档案官共用。"""

    clean_type = str(draft_type).strip().lower()
    if clean_type not in {"qa", "case"}:
        raise ValueError(f"unsupported knowledge draft type: {draft_type}")
    clean_question = _clean(question)
    clean_answer = _clean(answer)
    if not clean_question or not clean_answer:
        return None
    content_hash = _content_hash(
        clean_type,
        clean_question,
        clean_answer,
        source_message_ids=source_message_ids,
        sanitized_text=sanitized_text,
        content_json=content_json,
    )
    if await _has_draft(session, content_hash):
        return None
    draft = KnowledgeDraft(
        draft_type=clean_type,
        status="pending",
        title=(_clean(title) or clean_question[:80] or "知识库候选")[:200],
        question=clean_question,
        answer=clean_answer,
        category=_clean(category)[:100] or None,
        tags=_string_list(tags),
        reason=_clean(reason) or None,
        evidence=_clean(evidence) or None,
        sanitized_text=_clean(sanitized_text)[:8000] or None,
        source_message_ids=_positive_ids(source_message_ids),
        content_json=content_json or {},
        content_hash=content_hash,
    )
    session.add(draft)
    await session.flush()
    return draft


async def create_qa_drafts_from_archivist(
    session: AsyncSession,
    *,
    service_case_id: int,
    candidates: list[dict[str, Any]] | None,
    created_drafts: list[KnowledgeDraft] | None = None,
) -> int:
    """把档案官候选分流为待审核通用 FAQ 或班主任确认的个人指导。

    不提交事务，由调用方 commit；返回新建草稿数。检索正文只放问法/参考回复，
    其余归 content_json（遵守 QA 表四入库限制）。
    """
    created = 0
    for item in candidates or []:
        if not isinstance(item, dict):
            continue
        question = _clean(item.get("question"))
        answer = _clean(item.get("answer"))
        if not question or not answer:
            continue
        source_message_ids = _positive_ids(item.get("source_message_ids"))
        user_message = await _user_question_evidence(session, service_case_id, source_message_ids)
        if user_message is None:
            continue
        scope = str(item.get("scope") or "general")
        staff_ids = _positive_ids([item.get("staff_message_id")])
        staff = await session.get(Message, staff_ids[0]) if staff_ids else None
        if (
            not _is_staff_message_for_case(staff, service_case_id)
            or staff.id <= user_message.id
        ):
            continue
        if staff.id not in source_message_ids:
            source_message_ids.append(staff.id)
        if scope == "personal":
            service_case = await session.get(ServiceCase, service_case_id)
            if service_case is None:
                continue
            await apply_staff_guidance_to_user_state(
                session,
                service_case=service_case,
                staff_message=staff,
                guidance=answer,
                evidence=_clean(item.get("evidence_text")) or question,
                confidence=1.0,
            )
            continue
        if scope != "general":
            continue
        draft = await create_draft_candidate(
            session,
            draft_type="qa",
            title=question[:80] or "知识库候选",
            question=question,
            answer=answer,
            category=_clean(item.get("category"))[:100] or None,
            tags=_string_list(item.get("tags")),
            reason="档案官从群聊问答对自动提炼",
            evidence=_clean(item.get("evidence_text")) or None,
            source_message_ids=source_message_ids,
            content_json={
                **item,
                "scan_type": "archivist_delta",
                "service_case_id": service_case_id,
            },
        )
        if draft is not None:
            created += 1
            if created_drafts is not None:
                created_drafts.append(draft)
    return created


async def _user_question_evidence(
    session: AsyncSession,
    service_case_id: int,
    message_ids: list[int],
) -> Message | None:
    for message_id in message_ids:
        message = await session.get(Message, message_id)
        if (
            message
            and message.service_case_id == service_case_id
            and message.sender_type == MessageSenderType.USER
            and _clean(message.content)
        ):
            return message
    return None


def _is_staff_message_for_case(message: Message | None, service_case_id: int) -> bool:
    return bool(
        message
        and message.service_case_id == service_case_id
        and message.sender_type == MessageSenderType.STAFF
    )


async def reject_draft(session: AsyncSession, draft: KnowledgeDraft, reason: str | None = None) -> KnowledgeDraft:
    draft.status = "rejected"
    draft.rejected_at = datetime.now(timezone.utc)
    draft.rejected_reason = reason
    await session.commit()
    await session.refresh(draft)
    return draft


async def _publish_case(
    session: AsyncSession,
    draft: KnowledgeDraft,
    *,
    supersedes_chunk: KnowledgeChunk | None = None,
) -> str:
    """Publish an approved case draft to knowledge_chunks (source_type='case').

    The chunk is formatted as structured Markdown so the embedding captures the
    full semantic context (scenario, process, outcome, tags) in one dense vector.
    Embedding is requested immediately via upsert_text_chunk(embed=True).
    """
    tags = "、".join(draft.tags or [])
    content = (
        f"## 案例：{draft.title}\n\n"
        f"**适用场景**：{draft.question}\n\n"
        f"**用户背景**：{draft.category or '已脱敏用户'}\n\n"
        f"**之前状态**：{draft.evidence or draft.question}\n\n"
        f"**服务过程**：{draft.reason or '服务过程中识别到可复用的坚持与改善片段。'}\n\n"
        f"**改善结果**：{draft.answer}\n\n"
        f"**标签**：{tags or '无'}\n"
        f"**注意**：自动采集并经人工审核后入库；仅可作为鼓励案例，不承诺疗效。"
    )
    chunk = await upsert_text_chunk(
        session,
        chunk_uid=f"case:{draft.id}",
        source_type="case",
        source_uri=f"knowledge_draft:{draft.id}",
        title=draft.title,
        content=content,
        metadata={
            "draft_id": draft.id,
            "tags": draft.tags or [],
            "category": draft.category,
            "service_case_id": (
                draft.content_json.get("service_case_id")
                if isinstance(draft.content_json, dict)
                else None
            ),
            "supersedes_chunk_id": supersedes_chunk.id if supersedes_chunk else None,
        },
        embed=True,
    )
    if supersedes_chunk is not None and supersedes_chunk.id != chunk.id:
        old_metadata = (
            supersedes_chunk.metadata_json
            if isinstance(supersedes_chunk.metadata_json, dict)
            else {}
        )
        supersedes_chunk.status = "superseded"
        supersedes_chunk.metadata_json = {
            **old_metadata,
            "superseded_by_chunk_id": chunk.id,
            "superseded_by_draft_id": draft.id,
        }
        await session.commit()
    return f"knowledge_chunk:{chunk.id}"


async def _existing_case_summaries(
    session: AsyncSession,
    *,
    service_case_id: int,
    limit: int = 8,
) -> list[dict[str, Any]]:
    drafts = list(
        await session.scalars(
            select(KnowledgeDraft)
            .where(
                KnowledgeDraft.draft_type == "case",
                KnowledgeDraft.status.in_(("pending", "approved")),
            )
            .order_by(KnowledgeDraft.created_at.asc())
        )
    )
    summaries: list[dict[str, Any]] = []
    for draft in drafts:
        context = draft.content_json if isinstance(draft.content_json, dict) else {}
        if context.get("service_case_id") != service_case_id:
            continue
        summaries.append(
            {
                "title": draft.title[:40],
                "stage": str(context.get("stage") or ""),
                "category": (draft.category or "")[:40],
                "evidence": (draft.evidence or "")[:100],
                "answer_summary": (draft.answer or "")[:140],
            }
        )
    return summaries[-limit:]


async def _has_draft(session: AsyncSession, content_hash: str) -> bool:
    existing = await session.scalar(select(KnowledgeDraft.id).where(KnowledgeDraft.content_hash == content_hash).limit(1))
    return existing is not None


def _clean(value: object) -> str:
    if value is None or isinstance(value, (dict, list)):
        return ""
    return str(value).strip()


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip()[:30] for item in value if str(item).strip()][:8]


def _positive_ids(values: list[int] | None) -> list[int]:
    result: list[int] = []
    for item in values or []:
        try:
            parsed = int(item)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            result.append(parsed)
    return result[:20]


def _content_hash(
    draft_type: str,
    question: str,
    answer: str,
    *,
    source_message_ids: list[int] | None = None,
    sanitized_text: str | None = None,
    content_json: dict[str, Any] | None = None,
) -> str:
    return knowledge_draft_idempotency_hash(
        draft_type,
        question,
        answer,
        source_message_ids=source_message_ids,
        sanitized_text=sanitized_text,
        content_json=content_json,
    )
