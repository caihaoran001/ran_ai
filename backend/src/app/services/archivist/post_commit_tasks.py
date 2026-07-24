"""档案官主事务后的持久化最终一致任务。

主归档事务只负责权威事实、水位线和任务意图；case_memory、FAQ 草稿、优秀案例合成由这里
至少一次执行。处理器必须幂等，进程在处理器完成后、任务状态提交前中断时可以安全重跑。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import SessionLocal
from app.models.archivist_post_commit_task import ArchivistPostCommitTask
from app.services.archivist.case_memory_service import refresh_case_memory
from app.services.archivist.case_synthesis import maybe_synthesize_case
from app.services.knowledge_evolution import create_qa_drafts_from_archivist

logger = logging.getLogger(__name__)

POST_TASK_BATCH_SIZE = 20
POST_TASK_MAX_BACKOFF_SECONDS = 60 * 60


async def enqueue_archivist_post_commit_tasks(
    session: AsyncSession,
    *,
    service_case_id: int,
    watermark_to: int | None,
    run_result: Any,
) -> int:
    """在主归档事务内登记需要执行的后置任务，返回新增任务数。"""
    if not watermark_to:
        return 0

    specs: list[tuple[str, dict[str, Any]]] = []
    memory_payload = _memory_payload(run_result)
    if _has_memory_precipitation(memory_payload):
        specs.append((ArchivistPostCommitTask.TYPE_CASE_MEMORY, memory_payload))

    faq_candidates = getattr(run_result, "faq_candidates", None) or []
    if faq_candidates:
        specs.append(
            (
                ArchivistPostCommitTask.TYPE_FAQ_DRAFTS,
                {"candidates": faq_candidates},
            )
        )

    outcome_written = int(getattr(run_result, "outcome_written", 0) or 0)
    if outcome_written:
        specs.append(
            (
                ArchivistPostCommitTask.TYPE_CASE_SYNTHESIS,
                {"outcome_written": outcome_written},
            )
        )

    created = 0
    for task_type, payload in specs:
        existing = await session.scalar(
            select(ArchivistPostCommitTask.id)
            .where(
                ArchivistPostCommitTask.service_case_id == service_case_id,
                ArchivistPostCommitTask.watermark_to == watermark_to,
                ArchivistPostCommitTask.task_type == task_type,
            )
            .limit(1)
        )
        if existing is not None:
            continue
        session.add(
            ArchivistPostCommitTask(
                service_case_id=service_case_id,
                watermark_to=watermark_to,
                task_type=task_type,
                payload_json=payload,
                status=ArchivistPostCommitTask.STATUS_PENDING,
                attempt_count=0,
            )
        )
        created += 1
    if created:
        await session.flush()
    return created


async def run_archivist_post_commit_tasks(
    *,
    now: datetime | None = None,
    limit: int = POST_TASK_BATCH_SIZE,
) -> dict[str, int]:
    """执行一批到期任务；失败退避后自动重试，永不因达到固定次数而静默丢弃。"""
    now = now or datetime.now(timezone.utc)
    stats = {"claimed": 0, "succeeded": 0, "failed": 0}

    for _ in range(max(0, limit)):
        async with SessionLocal() as session:
            task = await session.scalar(
                select(ArchivistPostCommitTask)
                .where(
                    or_(
                        ArchivistPostCommitTask.status
                        == ArchivistPostCommitTask.STATUS_PENDING,
                        and_(
                            ArchivistPostCommitTask.status
                            == ArchivistPostCommitTask.STATUS_FAILED,
                            or_(
                                ArchivistPostCommitTask.next_attempt_at.is_(None),
                                ArchivistPostCommitTask.next_attempt_at <= now,
                            ),
                        ),
                    )
                )
                .order_by(
                    ArchivistPostCommitTask.next_attempt_at.asc().nullsfirst(),
                    ArchivistPostCommitTask.id.asc(),
                )
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if task is None:
                break

            stats["claimed"] += 1
            task.attempt_count = int(task.attempt_count or 0) + 1
            try:
                await _execute_post_commit_task(task)
            except Exception as exc:
                backoff_exponent = min(max(0, task.attempt_count - 1), 6)
                delay = min(
                    60 * (2**backoff_exponent),
                    POST_TASK_MAX_BACKOFF_SECONDS,
                )
                task.status = ArchivistPostCommitTask.STATUS_FAILED
                task.next_attempt_at = now + timedelta(seconds=delay)
                task.last_error = f"{type(exc).__name__}: {exc}"[:2000]
                stats["failed"] += 1
                logger.exception(
                    "Archivist post task failed task_id=%s type=%s case_id=%s attempt=%s",
                    task.id,
                    task.task_type,
                    task.service_case_id,
                    task.attempt_count,
                )
            else:
                task.status = ArchivistPostCommitTask.STATUS_SUCCEEDED
                task.next_attempt_at = None
                task.last_error = None
                task.completed_at = now
                stats["succeeded"] += 1
            await session.commit()

    return stats


async def _execute_post_commit_task(task: ArchivistPostCommitTask) -> None:
    payload = task.payload_json if isinstance(task.payload_json, dict) else {}
    run_result = SimpleNamespace(**payload)

    if task.task_type == ArchivistPostCommitTask.TYPE_CASE_MEMORY:
        await refresh_case_memory(
            service_case_id=task.service_case_id,
            run_result=run_result,
            watermark_to=task.watermark_to,
            raise_on_error=True,
        )
        return

    if task.task_type == ArchivistPostCommitTask.TYPE_FAQ_DRAFTS:
        async with SessionLocal() as session:
            await create_qa_drafts_from_archivist(
                session,
                service_case_id=task.service_case_id,
                candidates=payload.get("candidates") or [],
            )
            await session.commit()
        return

    if task.task_type == ArchivistPostCommitTask.TYPE_CASE_SYNTHESIS:
        await maybe_synthesize_case(
            service_case_id=task.service_case_id,
            run_result=run_result,
            raise_on_error=True,
        )
        return

    raise ValueError(f"unsupported archivist post task type: {task.task_type}")


def _memory_payload(run_result: Any) -> dict[str, Any]:
    upserts = []
    for item in getattr(run_result, "upserts", None) or []:
        if isinstance(item, dict):
            upserts.append({"filled": item.get("filled") or []})
    return {
        "outcome_written": int(getattr(run_result, "outcome_written", 0) or 0),
        "profile_written": int(getattr(run_result, "profile_written", 0) or 0),
        "personal_corrections_written": int(
            getattr(run_result, "personal_corrections_written", 0) or 0
        ),
        "upserts": upserts,
    }


def _has_memory_precipitation(payload: dict[str, Any]) -> bool:
    if payload.get("outcome_written"):
        return True
    if payload.get("profile_written"):
        return True
    if payload.get("personal_corrections_written"):
        return True
    return any(
        isinstance(item, dict) and bool(item.get("filled"))
        for item in payload.get("upserts") or []
    )
