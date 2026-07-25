"""档案官触发：DB 派生扫描 + 行锁，不用内存 timer（重启不丢，呼应"重启丢在途"教训）。

闸门（必须先满足）：水位线之后有 ≥1 条 archive_plan.worthy=true 的用户消息。
三条"或"（满足其一即跑）：
  Fast 关键信息：delta 含关键类目（checkin/profile_fact/outcome）且用户停说 ≥ 3min（去抖批处理）；
  B 最大滞留：距上次归档 ≥ 24h（无归档记录则按最早未归档消息计）——非关键 worthy 的兜底；
  C 累积：未归档用户消息 ≥ 20 条——安全阀。
关键信息快触发替代了原"静默 2h 才抽"：打卡/个人事实/成效在用户停说约 3 分钟后即抽，
让下游（打卡追问判缺口）看到的基本是最新数据；纯闲聊仍走 24h 兜底。
数值先按此上线、边观察边调。
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import SessionLocal
from app.models.archivist_progress import ArchivistProgress
from app.models.archivist_quarantine import ArchivistQuarantine
from app.models.message import Message, MessagePurpose, MessageSenderType, MessageStatus
from app.models.service_case import ServiceCase
from app.services.agent_observability import (
    COMPLETED as AGENT_RUN_COMPLETED,
    FAILED as AGENT_RUN_FAILED,
    complete_agent_run,
    ensure_agent_run,
)
from app.services.archivist.graph import get_or_create_progress, run_archivist
from app.services.archivist.errors import classify_archivist_failure

logger = logging.getLogger(__name__)

KEY_INFO_SETTLE_SECONDS = 3 * 60   # Fast：含关键信息且用户停说 3 分钟即抽（去抖，批处理连发消息）
MAX_LATENCY_SECONDS = 24 * 3600    # B 最大滞留：24 小时（非关键 worthy 的兜底）
MAX_DELTA_COUNT = 20               # C 累积：20 条用户发言（安全阀）

# 毒消息自愈（防单条稳定报错永久卡死水位线，连累群聊 Agent 画像永不刷新）：
# 只有显式分类为 message 的确定性错误才参与以下计数。网络、限流、模型服务、配置、数据库
# 或未知系统错误只保留水位线原地重试，绝不因连续失败而跳过用户消息。
# message 错误达 SINGLE_STEP 时把批次缩到最老 1 条，将失败范围收敛到单条；
# 仍达 QUARANTINE 时持久化隔离记录 + 跳过它推进水位线 + 清零候选计数。
# 单条模式下若最老那条其实无毒会成功推进，逐条自愈直到走到真正的毒消息才隔离（罕见路径，可接受少量重试）。
# 两者必须差 ≥1：先单条验证最老那条确实自己会炸，再隔离，避免误伤同批的好消息。
# 任意一次成功即清零，故连炸到阈值基本可排除偶发抖动（LLM 超时等）。
SINGLE_STEP_AFTER_FAILURES = 2
QUARANTINE_AFTER_FAILURES = 3

# 关键信息类目：可结构化打卡 / 稳定个人事实 / 阶段成效。命中即走 Fast 快触发。
KEY_ARCHIVE_KINDS = frozenset({"checkin", "profile_fact", "outcome"})


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _is_worthy(analysis_json: object, sender_type: MessageSenderType) -> bool:
    # archive_plan（入营层顺手产出）在此**只当触发闸门**：决定本单要不要跑、跑多快
    # （worthy→闸门放行，kinds∈KEY_ARCHIVE_KINDS→走 Fast 快触发）；**不决定抽什么**。
    # 它刻意宁滥勿缺（gate 提示词明说"抽不到也无妨"），真抽由 graph._extract 恒抽全类、独立判定，
    # 二者结论不必一致——分工使然。改这里前先看 graph._extract 的注释。
    if sender_type == MessageSenderType.STAFF:
        return True
    if not isinstance(analysis_json, dict):
        return False
    plan = analysis_json.get("archive_plan")
    if isinstance(plan, dict) and plan.get("worthy"):
        return True
    # 纯图片单发不走意图识别(视觉不进风控的既有决定)，故无 archive_plan；
    # 但成功解析的餐食图/体重秤图是打卡主来源，视为可归档，交给档案官抽取判断。
    return analysis_json.get("image_parse_status") == "ok"


def _has_key_kind(analysis_json: object, sender_type: MessageSenderType = MessageSenderType.USER) -> bool:
    """本条是否含"关键信息"(可结构化打卡/稳定个人事实/阶段成效) —— 命中即走 Fast 快触发。"""
    if sender_type == MessageSenderType.STAFF:
        return True
    if not isinstance(analysis_json, dict):
        return False
    plan = analysis_json.get("archive_plan")
    if isinstance(plan, dict):
        kinds = plan.get("kinds")
        if isinstance(kinds, list) and any(k in KEY_ARCHIVE_KINDS for k in kinds):
            return True
    # 成功解析的餐食图/体重秤图无 archive_plan，但本身就是打卡主来源，比照关键信息。
    return analysis_json.get("image_parse_status") == "ok"


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
            Message.sender_type.in_([MessageSenderType.USER, MessageSenderType.STAFF]),
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
            select(Message.id, Message.created_at, Message.analysis_json, Message.sender_type)
            .where(
                Message.service_case_id == service_case_id,
                Message.sender_type.in_([MessageSenderType.USER, MessageSenderType.STAFF]),
                Message.id > watermark,
            )
            .order_by(Message.id.asc())
        )
    ).all()
    if not rows:
        return False, "no_delta"

    created_times = [_aware(ct) for _, ct, _, _ in rows if ct is not None]
    last_user_at = max(created_times) if created_times else None
    # A terse response such as “完成” is normally not archive-worthy on its own,
    # but it may be the direct answer to a structured follow-up. Let the extractor
    # decide whether it really answers an expected item; without this exception the
    # silence brake could never be released by concise users.
    followup_reply_possible = False
    if last_user_at is not None:
        followup_reply_possible = bool(
            await session.scalar(
                select(Message.id)
                .where(
                    Message.service_case_id == service_case_id,
                    Message.sender_type == MessageSenderType.SYSTEM,
                    Message.purpose == MessagePurpose.REMINDER,
                    Message.status == MessageStatus.SENT,
                    Message.created_at <= last_user_at,
                    Message.created_at >= last_user_at - timedelta(hours=48),
                )
                .limit(1)
            )
        )
    worthy_any = followup_reply_possible or any(_is_worthy(aj, sender_type) for _, _, aj, sender_type in rows)
    if not worthy_any:
        return False, "gate_no_worthy"

    count = len(rows)
    first_user_at = min(created_times) if created_times else None
    key_any = followup_reply_possible or any(_has_key_kind(aj, sender_type) for _, _, aj, sender_type in rows)

    # Fast：含关键信息(打卡/个人事实/成效)且用户已停说 KEY_INFO_SETTLE_SECONDS —— 3 分钟去抖，
    # 把连发的几条批一次抽，既及时(≈3min 而非旧的 2h)又不至于每条都抽。
    cond_fast = (
        key_any
        and last_user_at is not None
        and (now - last_user_at).total_seconds() >= KEY_INFO_SETTLE_SECONDS
    )
    anchor = _aware(last_archived_at) or first_user_at
    cond_b = anchor is not None and (now - anchor).total_seconds() >= MAX_LATENCY_SECONDS
    cond_c = count >= MAX_DELTA_COUNT

    if cond_fast or cond_b or cond_c:
        reason = ",".join(
            k for k, v in (("fast_key", cond_fast), ("B_latency", cond_b), ("C_count", cond_c)) if v
        )
        return True, reason
    return False, "not_due"


async def run_archivist_scan(now: datetime | None = None) -> dict:
    """扫一遍候选服务单，对到点的跑档案官。返回统计。供 scheduler 周期调用。"""
    now = now or datetime.now(timezone.utc)
    stats = {
        "candidates": 0,
        "ran": 0,
        "skipped_gate": 0,
        "skipped_not_due": 0,
        "contended": 0,
        "failed": 0,
        "quarantined": 0,
    }

    async with SessionLocal() as session:
        candidate_ids = await _candidate_case_ids(session)
    stats["candidates"] = len(candidate_ids)

    for cid in candidate_ids:
        # 确保进度行存在（幂等，唯一约束防并发重复建）
        async with SessionLocal() as session:
            await get_or_create_progress(session, service_case_id=cid)
            await session.commit()
        # 认领 + 干活：行锁 SKIP LOCKED 防双跑，跑完 commit 释放锁
        agent_run_id: int | None = None
        run_started_at = time.monotonic()
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
                run_started_at = time.monotonic()
                agent_run_id = await ensure_agent_run(
                    run_key=f"archivist:{cid}:{watermark}:{uuid4().hex}",
                    agent_type="archivist",
                    run_kind="scheduled_scan",
                    service_case_id=cid,
                    trigger_reason=reason,
                    metadata={"watermark_from": watermark or None},
                )
                failures = progress.quarantine_candidate_failures or 0
                # 毒消息自愈：反复失败到阈值先缩批定位、再隔离，避免单条报错永久卡死水位线。
                if failures >= QUARANTINE_AFTER_FAILURES:
                    quarantined = await _quarantine_oldest_message(
                        session, service_case_id=cid, progress=progress, now=now
                    )
                    await session.commit()
                    stats["quarantined" if quarantined else "skipped_not_due"] += 1
                    continue
                batch_cap = 1 if failures >= SINGLE_STEP_AFTER_FAILURES else None
                result = await run_archivist(
                    session,
                    service_case=service_case,
                    progress=progress,
                    batch_cap=batch_cap,
                    agent_run_id=agent_run_id,
                )
                await session.commit()
                output_breakdown = archivist_output_breakdown(result)
                output_count = sum(output_breakdown.values())
                await complete_agent_run(
                    agent_run_id=agent_run_id,
                    status=AGENT_RUN_COMPLETED,
                    outcome=(
                        "archived_with_output"
                        if output_count
                        else "archived_no_output"
                    ),
                    latency_ms=int((time.monotonic() - run_started_at) * 1000),
                    input_count=result.delta_count,
                    output_count=output_count,
                    dropped_count=result.dropped_count,
                    metadata={
                        "watermark_from": result.watermark_from,
                        "watermark_to": result.watermark_to,
                        "emitted_count": result.emitted_count,
                        "output_breakdown": output_breakdown,
                        "batch_cap": batch_cap,
                    },
                )
                stats["ran"] += 1
                logger.info(
                    "Archivist ran service_case_id=%s reason=%s delta=%s days=%s contradictions=%s",
                    cid, reason, result.delta_count, result.days_processed, len(result.contradictions),
                )
        except Exception as exc:
            # 归档失败：主事务已回滚（未 commit），水位线保持不动，下轮重试。
            # 失败留痕走独立事务，避免与已回滚的事务纠缠。
            logger.exception("Archivist run failed service_case_id=%s", cid)
            stats["failed"] += 1
            await complete_agent_run(
                agent_run_id=agent_run_id,
                status=AGENT_RUN_FAILED,
                outcome="archivist_failed",
                latency_ms=int((time.monotonic() - run_started_at) * 1000),
                error_type=type(exc).__name__,
                metadata={
                    "failure_kind": classify_archivist_failure(exc).kind,
                },
            )
            await _record_failure(cid, error=exc, now=now)

    return stats


def archivist_output_breakdown(result) -> dict[str, int]:
    """归档成功后的确定性产出计数；用于运营效果统计，不参与业务判断。"""
    return {
        "checkin_days": sum(
            1
            for item in (result.upserts or [])
            if item.get("created") or item.get("filled")
        ),
        "outcomes": int(result.outcome_written or 0),
        "profile_facts": int(result.profile_written or 0),
        "state_facts": int(result.state_written or 0),
        "personal_guidance": int(result.personal_corrections_written or 0),
        "corrections": max(
            0,
            int(result.corrections_recorded or 0)
            - int(result.personal_corrections_written or 0),
        ),
        "faq_drafts": int(result.faq_written or 0),
        "memory_updates": int(result.memory_written or 0),
        "case_drafts": int(result.case_written or 0),
    }


async def _quarantine_oldest_message(
    session: AsyncSession,
    *,
    service_case_id: int,
    progress: ArchivistProgress,
    now: datetime,
) -> bool:
    """隔离最老一条未归档消息：判定其为毒消息，告警留痕 + 推进水位线跳过它 + 清零失败计数。

    仅在连续失败达 QUARANTINE_AFTER_FAILURES（此前已缩到单条重试、失败范围收敛到这一条）时调用。
    在持锁事务内执行，由调用方 commit。返回是否确实隔离了一条。
    """
    watermark = progress.last_archived_message_id or 0
    suspect = await session.scalar(
        select(Message)
        .where(
            Message.service_case_id == service_case_id,
            Message.id > watermark,
            Message.sender_type.in_([MessageSenderType.USER, MessageSenderType.STAFF]),
            Message.purpose == MessagePurpose.CHAT,
        )
        .order_by(Message.id.asc())
        .limit(1)
    )
    if suspect is None:
        # 没有可疑消息可跳过（增量已被清空/软删）：清零失败计数避免空转，不动水位线。
        progress.consecutive_failures = 0
        progress.quarantine_candidate_failures = 0
        progress.quarantine_candidate_fingerprint = None
        return False

    prev_reason = progress.last_failure_reason or ""
    logger.error(
        "ARCHIVIST_QUARANTINE service_case_id=%s message_id=%s failures=%s created_at=%s "
        "prev_reason=%s content=%r",
        service_case_id,
        suspect.id,
        progress.consecutive_failures,
        suspect.created_at.isoformat() if suspect.created_at else None,
        prev_reason,
        (suspect.content or "")[:200],
    )
    session.add(
        ArchivistQuarantine(
            service_case_id=service_case_id,
            message_id=suspect.id,
            status=ArchivistQuarantine.STATUS_QUARANTINED,
            failure_fingerprint=progress.quarantine_candidate_fingerprint,
            failure_reason=prev_reason[:2000] or "deterministic message processing failure",
            quarantined_at=now,
            replay_attempts=0,
        )
    )
    progress.last_archived_message_id = suspect.id
    progress.last_archived_at = now
    progress.consecutive_failures = 0
    progress.last_failure_reason = f"QUARANTINED msg={suspect.id}: {prev_reason}"[:500]
    progress.quarantine_candidate_failures = 0
    progress.quarantine_candidate_fingerprint = None
    return True


async def _record_failure(
    service_case_id: int,
    *,
    error: Exception,
    now: datetime,
) -> None:
    """在独立事务里累加失败计数并留痕（主事务已回滚，水位线保持不动）。

    尽力而为：记账本身再失败也只记日志，不影响其余候选单继续扫描。
    """
    classification = classify_archivist_failure(error)
    reason = str(error)
    try:
        async with SessionLocal() as session:
            progress = await session.scalar(
                select(ArchivistProgress).where(
                    ArchivistProgress.service_case_id == service_case_id
                )
            )
            if progress is None:
                return
            progress.consecutive_failures = (progress.consecutive_failures or 0) + 1
            progress.failure_count = (progress.failure_count or 0) + 1
            progress.last_failure_at = now
            progress.last_failure_reason = (reason or "")[:500]
            progress.last_failure_kind = classification.kind
            progress.last_failure_fingerprint = classification.fingerprint
            if classification.kind == "message":
                if (
                    progress.quarantine_candidate_fingerprint
                    == classification.fingerprint
                ):
                    progress.quarantine_candidate_failures = (
                        progress.quarantine_candidate_failures or 0
                    ) + 1
                else:
                    progress.quarantine_candidate_fingerprint = (
                        classification.fingerprint
                    )
                    progress.quarantine_candidate_failures = 1
            else:
                # 系统性故障不能靠跳消息修复；清零有损隔离计数。
                progress.quarantine_candidate_failures = 0
                progress.quarantine_candidate_fingerprint = None
            await session.commit()
    except Exception:
        logger.exception(
            "Archivist failure bookkeeping failed service_case_id=%s", service_case_id
        )


async def replay_archivist_quarantine(
    session: AsyncSession,
    *,
    quarantine_id: int,
    chat_model: object | None = None,
    agent_run_id: int | None = None,
):
    """在不回退正式水位线的前提下，隔离单条原位重跑一次。

    使用临时 progress 只选中目标 message；事实写入、outbox 和“已重放”状态同事务提交，
    由 API 调用方负责 commit。任何异常由调用方 rollback 后记录重放失败。
    """
    quarantine = await session.scalar(
        select(ArchivistQuarantine)
        .where(ArchivistQuarantine.id == quarantine_id)
        .with_for_update()
    )
    if quarantine is None:
        raise LookupError("archivist quarantine not found")
    if quarantine.status != ArchivistQuarantine.STATUS_QUARANTINED:
        raise ValueError("archivist quarantine is not pending replay")

    message = await session.get(Message, quarantine.message_id)
    service_case = await session.get(ServiceCase, quarantine.service_case_id)
    if message is None or service_case is None or service_case.deleted_at is not None:
        raise LookupError("quarantined message or service case no longer exists")

    isolated_progress = ArchivistProgress(
        service_case_id=service_case.id,
        last_archived_message_id=message.id - 1,
    )
    result = await run_archivist(
        session,
        service_case=service_case,
        progress=isolated_progress,
        chat_model=chat_model,
        batch_cap=1,
        agent_run_id=agent_run_id,
    )
    if result.watermark_to != message.id:
        raise RuntimeError(
            f"quarantine replay selected unexpected watermark {result.watermark_to}"
        )

    quarantine.status = ArchivistQuarantine.STATUS_REPLAYED
    quarantine.replay_attempts = int(quarantine.replay_attempts or 0) + 1
    quarantine.replayed_at = datetime.now(timezone.utc)
    quarantine.last_replay_error = None
    return result
