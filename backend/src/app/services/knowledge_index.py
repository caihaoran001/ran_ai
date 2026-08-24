from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, text as sql_text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models.knowledge_chunk import KnowledgeChunk
from app.services.embedding_service import EmbeddingService
from app.services.knowledge_base import _knowledge_files, _query_terms, _split_sections

logger = logging.getLogger(__name__)
VECTOR_DISTANCE_THRESHOLD = 0.4


# 命中标记前缀。检索结果按 f"{KNOWLEDGE_HIT_PREFIX}{n}]" 逐条编号后注入提示词。
KNOWLEDGE_HIT_PREFIX = "[知识库命中 "


@dataclass
class KnowledgeSyncResult:
    scanned: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    embedded: int = 0
    embedding_skipped: int = 0


@dataclass(frozen=True)
class IndexedKnowledgeHit:
    rank: int
    chunk_id: int
    chunk_uid: str
    title: str
    source_type: str
    updated_at: datetime | None
    content: str

    def observation(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "chunk_id": self.chunk_id,
            "chunk_uid": self.chunk_uid,
            "title": self.title,
            "source_type": self.source_type,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "content_excerpt": self.content[:1500],
        }


@dataclass(frozen=True)
class IndexedKnowledgeResult:
    context: str
    hits: list[IndexedKnowledgeHit]


async def sync_knowledge_chunks(
    session: AsyncSession,
    *,
    embed: bool = False,
    force_embedding: bool = False,
    settings: Settings | None = None,
) -> KnowledgeSyncResult:
    settings = settings or get_settings()
    result = KnowledgeSyncResult()
    await session.execute(
        update(KnowledgeChunk)
        .where(KnowledgeChunk.source_type == "markdown")
        .values(status="stale")
    )
    await session.flush()
    for file_path in _knowledge_files(settings):
        metadata_by_id = _metadata_for_file(file_path)
        text = file_path.read_text(encoding="utf-8").strip()
        for section_index, section in enumerate(_split_sections(text), start=1):
            chunk = _chunk_from_section(file_path, section, metadata_by_id, section_index)
            result.scanned += 1
            existing = await session.scalar(
                select(KnowledgeChunk).where(KnowledgeChunk.chunk_uid == chunk["chunk_uid"]).limit(1)
            )
            if existing:
                if existing.content_hash == chunk["content_hash"]:
                    existing.status = "active"
                    result.unchanged += 1
                else:
                    existing.title = chunk["title"]
                    existing.content = chunk["content"]
                    existing.metadata_json = chunk["metadata_json"]
                    existing.content_hash = chunk["content_hash"]
                    existing.embedding = None
                    existing.embedding_model = None
                    existing.embedded_at = None
                    existing.status = "active"
                    result.updated += 1
            else:
                session.add(KnowledgeChunk(**chunk))
                result.created += 1
    await session.commit()
    if embed:
        embedded, skipped = await embed_missing_chunks(session, force=force_embedding, settings=settings)
        result.embedded = embedded
        result.embedding_skipped = skipped
    return result


async def upsert_text_chunk(
    session: AsyncSession,
    *,
    chunk_uid: str,
    source_type: str,
    source_uri: str,
    title: str,
    content: str,
    metadata: dict[str, Any] | None = None,
    embed: bool = True,
    settings: Settings | None = None,
) -> KnowledgeChunk:
    settings = settings or get_settings()
    content_hash = _hash(content)
    existing = await session.scalar(select(KnowledgeChunk).where(KnowledgeChunk.chunk_uid == chunk_uid).limit(1))
    # 防重嵌入：内容未变且已有有效向量时跳过本次重算，避免每次同步都真实调用 Embedding API。
    skip_embed = False
    if existing:
        skip_embed = existing.content_hash == content_hash and bool(existing.embedding_model)
        existing.source_type = source_type
        existing.source_uri = source_uri
        existing.title = title[:300]
        existing.content = content
        existing.metadata_json = metadata or {}
        existing.status = "active"
        if existing.content_hash != content_hash:
            existing.content_hash = content_hash
            existing.embedding = None
            existing.embedding_model = None
            existing.embedded_at = None
        chunk = existing
    else:
        chunk = KnowledgeChunk(
            chunk_uid=chunk_uid,
            source_type=source_type,
            source_uri=source_uri,
            source_title=title[:200],
            title=title[:300],
            content=content,
            metadata_json=metadata or {},
            content_hash=content_hash,
            status="active",
        )
        session.add(chunk)
    await session.commit()
    await session.refresh(chunk)
    if embed and not skip_embed:
        await embed_chunk(session, chunk, settings=settings)
    return chunk


async def embed_missing_chunks(
    session: AsyncSession,
    *,
    force: bool = False,
    settings: Settings | None = None,
    batch_size: int = 8,
) -> tuple[int, int]:
    settings = settings or get_settings()
    embedding_service = EmbeddingService(settings)
    if not embedding_service.configured:
        return 0, await _active_chunk_count(session)
    conditions = [KnowledgeChunk.status == "active"]
    if not force:
        conditions.append(
            (KnowledgeChunk.embedding.is_(None)) | (KnowledgeChunk.embedding_model != embedding_service.model)
        )
    chunks = list(
        await session.scalars(
            select(KnowledgeChunk)
            .where(*conditions)
            .order_by(KnowledgeChunk.id.asc())
        )
    )
    embedded = 0
    try:
        for index in range(0, len(chunks), batch_size):
            batch = chunks[index : index + batch_size]
            vectors = await embedding_service.embed_texts([_embedding_text(chunk) for chunk in batch])
            now = datetime.now(timezone.utc)
            pgvector_available = await _pgvector_available(session)
            for chunk, vector in zip(batch, vectors, strict=False):
                chunk.embedding = vector
                chunk.embedding_model = embedding_service.model
                chunk.embedded_at = now
                if pgvector_available:
                    await _store_embedding_vector(session, chunk.id, vector)
                embedded += 1
            await session.commit()
    finally:
        await embedding_service.close()
    return embedded, max(len(chunks) - embedded, 0)


async def embed_chunk(
    session: AsyncSession,
    chunk: KnowledgeChunk,
    *,
    settings: Settings | None = None,
) -> bool:
    settings = settings or get_settings()
    embedding_service = EmbeddingService(settings)
    if not embedding_service.configured:
        return False
    try:
        vector = await embedding_service.embed_text(_embedding_text(chunk))
        chunk.embedding = vector
        chunk.embedding_model = embedding_service.model
        chunk.embedded_at = datetime.now(timezone.utc)
        if await _pgvector_available(session):
            await _store_embedding_vector(session, chunk.id, vector)
        await session.commit()
        return True
    finally:
        await embedding_service.close()


async def retrieve_indexed_knowledge(
    session: AsyncSession,
    query: str,
    *,
    settings: Settings | None = None,
    exclude_source_types: set[str] | None = None,
) -> str:
    result = await retrieve_indexed_knowledge_result(
        session,
        query,
        settings=settings,
        exclude_source_types=exclude_source_types,
    )
    return result.context


async def retrieve_indexed_knowledge_result(
    session: AsyncSession,
    query: str,
    *,
    settings: Settings | None = None,
    exclude_source_types: set[str] | None = None,
) -> IndexedKnowledgeResult:
    settings = settings or get_settings()
    query = query.strip()
    if not query:
        return IndexedKnowledgeResult(context="", hits=[])
    vec_results: list[tuple[int, str]] = []
    text_results: list[tuple[int, str]] = []

    if settings.embedding_api_key:
        vec_results = await _retrieve_by_embedding(
            session, query, settings=settings, exclude_source_types=exclude_source_types
        )
    text_results = await _retrieve_by_text(
        session,
        query,
        top_k=settings.knowledge_base_top_k * 2,
        exclude_source_types=exclude_source_types,
    )

    # Reciprocal Rank Fusion (RRF)
    # RRF Score = 1 / (rank + k), typical k=60
    rrf_k = 60
    scores: dict[int, float] = {}
    contents: dict[int, str] = {}
    vector_ranks: dict[int, int] = {}
    text_ranks: dict[int, int] = {}

    for rank, (chunk_id, content) in enumerate(vec_results, start=1):
        scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (rank + rrf_k)
        contents[chunk_id] = content
        vector_ranks[chunk_id] = rank

    for rank, (chunk_id, content) in enumerate(text_results, start=1):
        scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (rank + rrf_k)
        contents[chunk_id] = content
        text_ranks[chunk_id] = rank

    if not scores:
        return IndexedKnowledgeResult(context="", hits=[])

    # Exact business-keyword hits are curated by the whitelist.  When a
    # vector-only result and a text-only result receive the same RRF score,
    # prefer the exact text hit instead of depending on insertion order (the
    # old vector-first insertion made generic semantic matches win every tie).
    missing_rank = len(scores) + 1
    ranked_ids = sorted(
        scores,
        key=lambda cid: (
            -scores[cid],
            text_ranks.get(cid, missing_rank),
            vector_ranks.get(cid, missing_rank),
            cid,
        ),
    )
    top_ids = ranked_ids[:settings.knowledge_base_top_k]
    chunk_rows = list(
        await session.scalars(
            select(KnowledgeChunk).where(KnowledgeChunk.id.in_(top_ids))
        )
    )
    chunks_by_id = {chunk.id: chunk for chunk in chunk_rows}
    hits: list[IndexedKnowledgeHit] = []
    for rank, chunk_id in enumerate(top_ids, start=1):
        chunk = chunks_by_id.get(chunk_id)
        if chunk is None:
            continue
        hits.append(
            IndexedKnowledgeHit(
                rank=rank,
                chunk_id=chunk.id,
                chunk_uid=chunk.chunk_uid,
                title=chunk.title,
                source_type=chunk.source_type,
                updated_at=chunk.updated_at,
                content=contents.get(chunk_id, chunk.content).strip(),
            )
        )

    context = "\n\n".join(
        f"{KNOWLEDGE_HIT_PREFIX}{hit.rank}]\n{hit.content}" for hit in hits
    )
    return IndexedKnowledgeResult(context=context, hits=hits)


async def retrieve_case_context_db(
    session: AsyncSession,
    query: str,
    *,
    settings: Settings | None = None,
    limit: int = 1,
) -> str:
    """HNSW semantic retrieval for approved case chunks (source_type='case').

    Only runs when an embedding key is configured — cases are narrative text and
    keyword matching would produce noisy false positives, so there is no text
    fallback.  Returns the skill.md coaching header prepended to the matched
    case content, or an empty string when no cases are stored yet.
    """
    settings = settings or get_settings()
    query = query.strip()
    if not query or not settings.embedding_api_key:
        return ""

    if not await _pgvector_available(session):
        raise RuntimeError(
            "【严重配置错误】检测到数据库未安装 pgvector 扩展，或 'knowledge_chunks' 表缺少 'embedding_vector' 字段。"
            "pgvector 是燃人AI 1.0 系统运行的强制基础设施依赖，请检查 Postgres 容器配置！"
        )

    embedding_service = EmbeddingService(settings)
    if not embedding_service.configured:
        return ""
    try:
        query_vector = await embedding_service.embed_text(query)
    except Exception:
        logger.exception("Case embedding retrieval failed")
        return ""
    finally:
        await embedding_service.close()

    qv_str = "[" + ",".join(str(x) for x in query_vector) + "]"
    rows = list(
        await session.execute(
            sql_text(
                """
                SELECT content
                FROM knowledge_chunks
                WHERE status = 'active'
                  AND source_type = 'case'
                  AND embedding_vector IS NOT NULL
                  AND embedding_model = :model
                  AND embedding_vector <=> CAST(:qv AS vector) < :max_distance
                ORDER BY embedding_vector <=> CAST(:qv AS vector)
                LIMIT :limit
                """
            ),
            {
                "model": settings.embedding_model,
                "qv": qv_str,
                "max_distance": settings.case_vector_distance_threshold,
                "limit": limit,
            },
        )
    )
    snippets = [row[0].strip() for row in rows]
    if not snippets:
        return ""

    from app.services.case_library import _skill_text
    skill = _skill_text()
    return "\n\n".join([skill, *snippets]) if skill else "\n\n".join(snippets)


async def knowledge_index_stats(session: AsyncSession) -> dict[str, Any]:
    total = await _active_chunk_count(session)
    embedded = await session.scalar(
        select(func.count(KnowledgeChunk.id)).where(
            KnowledgeChunk.status == "active",
            KnowledgeChunk.embedding.is_not(None),
        )
    )
    by_source = await session.execute(
        select(KnowledgeChunk.source_type, func.count(KnowledgeChunk.id))
        .where(KnowledgeChunk.status == "active")
        .group_by(KnowledgeChunk.source_type)
        .order_by(KnowledgeChunk.source_type.asc())
    )
    return {
        "total_chunks": int(total or 0),
        "embedded_chunks": int(embedded or 0),
        "by_source_type": {source_type: count for source_type, count in by_source.all()},
    }


async def _retrieve_by_embedding(
    session: AsyncSession,
    query: str,
    *,
    settings: Settings,
    exclude_source_types: set[str] | None = None,
) -> list[tuple[int, str]]:
    if not await _pgvector_available(session):
        raise RuntimeError(
            "【严重配置错误】检测到数据库未安装 pgvector 扩展，或 'knowledge_chunks' 表缺少 'embedding_vector' 字段。"
            "pgvector 是燃人AI 1.0 系统运行的强制基础设施依赖，请检查 Postgres 容器配置！"
        )
    embedded_count = await session.scalar(
        select(func.count(KnowledgeChunk.id)).where(
            KnowledgeChunk.status == "active",
            KnowledgeChunk.embedding.is_not(None),
            KnowledgeChunk.embedding_model == settings.embedding_model,
        )
    )
    if not embedded_count:
        return []
    embedding_service = EmbeddingService(settings)
    if not embedding_service.configured:
        return []
    try:
        query_vector = await embedding_service.embed_text(query)
    except Exception:
        logger.exception("Embedding retrieval failed; falling back to text retrieval")
        return []
    finally:
        await embedding_service.close()

    qv_str = "[" + ",".join(str(x) for x in query_vector) + "]"
    params: dict[str, Any] = {
        "model": settings.embedding_model,
        "qv": qv_str,
        "max_distance": settings.faq_vector_distance_threshold,
        "limit": settings.knowledge_base_top_k * 2,
    }
    exclude_clause = ""
    if exclude_source_types:
        params["exclude_types"] = list(exclude_source_types)
        exclude_clause = "AND source_type <> ALL(:exclude_types)\n              "
    res = await session.execute(
        sql_text(
            f"""
            SELECT id, content
            FROM knowledge_chunks
            WHERE status = 'active'
              AND embedding_vector IS NOT NULL
              AND embedding_model = :model
              {exclude_clause}AND embedding_vector <=> CAST(:qv AS vector) < :max_distance
            ORDER BY embedding_vector <=> CAST(:qv AS vector)
            LIMIT :limit
            """
        ),
        params,
    )
    return [(row[0], row[1].strip()) for row in res.all()]


async def _retrieve_by_text(
    session: AsyncSession,
    query: str,
    *,
    top_k: int,
    exclude_source_types: set[str] | None = None,
) -> list[tuple[int, str]]:
    """Keyword fallback retrieval backed by the pg_trgm GIN index.

    Scores each active chunk by how many query terms appear in its content using
    ILIKE.  PostgreSQL uses the ``ix_knowledge_chunks_content_trgm`` GIN index
    for every ILIKE pattern, so no full sequential scan occurs regardless of
    table size.  Only the top-K content strings are transferred to Python.
    """
    query_terms = _query_terms(query)
    if not query_terms:
        return []

    from functools import reduce
    import operator as _op
    from sqlalchemy import case, or_, text as sa_text

    # One CASE expression per term → sum = match-count score
    score_parts = [
        case((KnowledgeChunk.content.ilike(f"%{term}%"), 1), else_=0)
        for term in query_terms
    ]
    score_expr = reduce(_op.add, score_parts)
    any_match = or_(*(KnowledgeChunk.content.ilike(f"%{term}%") for term in query_terms))

    conditions = [KnowledgeChunk.status == "active", any_match]
    if exclude_source_types:
        conditions.append(KnowledgeChunk.source_type.notin_(exclude_source_types))
    rows = list(
        await session.execute(
            select(KnowledgeChunk.id, KnowledgeChunk.content, score_expr.label("score"))
            .where(*conditions)
            .order_by(sa_text("score DESC"))
            .limit(top_k)
        )
    )
    return [(row[0], row[1].strip()) for row in rows]





async def _pgvector_available(session: AsyncSession) -> bool:
    value = await session.scalar(
        sql_text(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'knowledge_chunks'
                  AND column_name = 'embedding_vector'
            )
            """
        )
    )
    return bool(value)


async def _store_embedding_vector(session: AsyncSession, chunk_id: int, vector: list[float]) -> None:
    vector_text = "[" + ",".join(str(x) for x in vector) + "]"
    await session.execute(
        sql_text(
            """
            UPDATE knowledge_chunks
            SET embedding_vector = CAST(:vector AS vector)
            WHERE id = :chunk_id
            """
        ),
        {"vector": vector_text, "chunk_id": chunk_id},
    )


async def _active_chunk_count(session: AsyncSession) -> int:
    value = await session.scalar(select(func.count(KnowledgeChunk.id)).where(KnowledgeChunk.status == "active"))
    return int(value or 0)


def _chunk_from_section(
    file_path: Path,
    section: str,
    metadata_by_id: dict[str, dict[str, Any]],
    section_index: int,
) -> dict[str, Any]:
    title = _section_title(section)
    qa_id = _qa_id(title)
    metadata = metadata_by_id.get(qa_id, {}) if qa_id else {}
    source_uri = str(file_path)
    raw_stable = f"{source_uri}\n{section_index}\n{title}"
    stable_id = qa_id or f"section-{section_index:04d}-{_hash(raw_stable)[:12]}"
    return {
        "chunk_uid": f"md:{file_path.name}:{stable_id}",
        "source_type": "markdown",
        "source_uri": source_uri,
        "source_title": file_path.stem,
        "title": title[:300],
        "content": section.strip(),
        "metadata_json": metadata,
        "content_hash": _hash(section),
        "status": "active",
    }


def _metadata_for_file(file_path: Path) -> dict[str, dict[str, Any]]:
    metadata_path = file_path.with_suffix(".metadata.json")
    if not metadata_path.exists():
        return {}
    try:
        data = json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    blocks = data.get("blocks") if isinstance(data, dict) else None
    if not isinstance(blocks, list):
        return {}
    return {str(item.get("id")): item for item in blocks if isinstance(item, dict) and item.get("id")}


def _section_title(section: str) -> str:
    for line in section.splitlines():
        if line.startswith("## "):
            return line[3:].strip()
        if line.startswith("# "):
            continue
    first = section.strip().splitlines()[0] if section.strip() else "未命名知识块"
    return first.lstrip("#").strip() or "未命名知识块"


def _qa_id(title: str) -> str | None:
    match = re.match(r"^(QA-\d{4}(?:-\d{2})?)\b", title)
    return match.group(1) if match else None


def _embedding_text(chunk: KnowledgeChunk) -> str:
    return f"{chunk.title}\n\n{chunk.content}".strip()





def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
