"""档案官触发：DB 派生扫描 + 行锁，不用内存 timer（重启不丢，呼应"重启丢在途"教训）。

闸门（必须先满足）：水位线之后有 ≥1 条 archive_plan.worthy=true 的用户消息。
三条"或"（满足其一即跑）：
  A 冷却：最后一条用户消息距今 ≥ 2h；
  B 最大滞留：距上次归档 ≥ 24h（无归档记录则按最早未归档消息计）；
  C 累积：未归档用户消息 ≥ 20 条。
数值先按此上线、边观察边调。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import SessionLocal
from app.models.archivist_progress import ArchivistProgress
from app.models.message import Message, MessageSenderType
from app.models.service_case import ServiceCase
from app.services.archivist.graph import get_or_create_progress, run_archivist

logger = logging.getLogger(__name__)

COOLDOWN_SECONDS = 2 * 3600        # A 冷却：2 小时
MAX_LATENCY_SECONDS = 24 * 3600    # B 最大滞留：24 小时
MAX_DELTA_COUNT = 20               # C 累积：20 条用户发言


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _is_worthy(analysis_json: object) -> bool:
    if not isinstance(analysis_json, dict):
        return False
    plan = analysis_json.get("archive_plan")
    return bool(isinstance(plan, dict) and plan.get("worthy"))


async def _candidate_case_ids(session: AsyncSession) -> list[int]:
    """有"水位线之后新用户消息"的在服务单（未归档增量非空）。已归档的会随水位线推进自动退出。"""
    stmt = (
        select(Message.service_case_id)
        .join(ServiceCase, ServiceCase.id == Message.service_case_id)
        .outerjoin(
            ArchivistProgress,
            ArchivistProgress.service_case_id == Message.service_case_id,
        )
        .where(
            ServiceCase.deleted_at.is_(None),
            Message.service_case_id.is_not(None),
            Message.sender_type == MessageSenderType.USER,
            Message.id > func.coalesce(ArchivistProgress.last_archived_message_id, 0),
        )
        .group_by(Message.service_case_id)
    )
    return [cid for cid in (await session.scalars(stmt)).all() if cid is not None]


async def _evaluate(
    session: AsyncSession,
    *,
    service_case_id: int,
    watermark: int,
    last_archived_at: datetime | None,
    now: datetime,
) -> tuple[bool, str]:
    """闸门 + A/B/C 判定。返回 (是否触发, 原因)。"""
    rows = (
        await session.execute(
            select(Message.created_at, Message.analysis_json)
            .where(
                Message.service_case_id == service_case_id,
                Message.sender_type == MessageSenderType.USER,
                Message.id > watermark,
            )
            .order_by(Message.id.asc())
        )
    ).all()
    if not rows:
        return False, "no_delta"

    worthy_any = any(_is_worthy(aj) for _, aj in rows)
    if not worthy_any:
        return False, "gate_no_worthy"

    created_times = [_aware(ct) for ct, _ in rows if ct is not None]
    count = len(rows)
    last_user_at = max(created_times) if created_times else None
    first_user_at = min(created_times) if created_times else None

    cond_a = last_user_at is not None and (now - last_user_at).total_seconds() >= COOLDOWN_SECONDS
    anchor = _aware(last_archived_at) or first_user_at
    cond_b = anchor is not None and (now - anchor).total_seconds() >= MAX_LATENCY_SECONDS
    cond_c = count >= MAX_DELTA_COUNT

    if cond_a or cond_b or cond_c:
        reason = ",".join(
            k for k, v in (("A_cooldown", cond_a), ("B_latency", cond_b), ("C_count", cond_c)) if v
        )
        return True, reason
    return False, "not_due"


async def run_archivist_scan(now: datetime | None = None) -> dict:
    """扫一遍候选服务单，对到点的跑档案官。返回统计。供 scheduler 周期调用。"""
    now = now or datetime.now(timezone.utc)
    stats = {"candidates": 0, "ran": 0, "skipped_gate": 0, "skipped_not_due": 0, "contended": 0}

    async with SessionLocal() as session:
        candidate_ids = await _candidate_case_ids(session)
    stats["candidates"] = len(candidate_ids)

    for cid in candidate_ids:
        # 确保进度行存在（幂等，唯一约束防并发重复建）
        async with SessionLocal() as session:
            await get_or_create_progress(session, service_case_id=cid)
            await session.commit()
        # 认领 + 干活：行锁 SKIP LOCKED 防双跑，跑完 commit 释放锁
        try:
            async with SessionLocal() as session:
                progress = await session.scalar(
                    select(ArchivistProgress)
                    .where(ArchivistProgress.service_case_id == cid)
                    .with_for_update(skip_locked=True)
                )
                if progress is None:
                    stats["contended"] += 1
                    continue
                watermark = progress.last_archived_message_id or 0
                should, reason = await _evaluate(
                    session,
                    service_case_id=cid,
                    watermark=watermark,
                    last_archived_at=progress.last_archived_at,
                    now=now,
                )
                if not should:
                    stats["skipped_gate" if reason == "gate_no_worthy" else "skipped_not_due"] += 1
                    continue
                service_case = await session.get(ServiceCase, cid)
                if service_case is None:
                    continue
                result = await run_archivist(session, service_case=service_case, progress=progress)
                await session.commit()
                stats["ran"] += 1
                logger.info(
                    "Archivist ran service_case_id=%s reason=%s delta=%s days=%s contradictions=%s",
                    cid, reason, result.delta_count, result.days_processed, len(result.contradictions),
                )
        except Exception:
            logger.exception("Archivist run failed service_case_id=%s", cid)

    return stats
