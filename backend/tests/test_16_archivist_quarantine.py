"""档案官毒消息自愈——缩批定位 + 隔离跳过健康自测。

验证：
- batch_cap 缩批：只处理最老 1 条增量、水位线只推进过这一条（定位毒消息用）；
- _quarantine_oldest_message：判定最老那条为毒消息，推进水位线跳过它、清零连续失败计数、留痕。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import select

from app.models.archivist_progress import ArchivistProgress
from app.models.message import (
    Message,
    MessageDirection,
    MessagePurpose,
    MessageSenderType,
    MessageStatus,
)
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user import User
from app.services.archivist.graph import run_archivist
from app.services.archivist.trigger import _quarantine_oldest_message


class _SilentModel:
    """假模型：bind_tools 返回自身，ainvoke 恒吐空 AIMessage（不 report 任何东西）。"""

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        return AIMessage(content="")


async def _setup_case_with_n_messages(db_session, n: int):
    user = User(name="毒消息自愈测试用户")
    db_session.add(user)
    await db_session.flush()
    case = ServiceCase(
        case_no=f"TEST-QRTN-{uuid4().hex[:6].upper()}",
        user_id=user.id,
        group_id=f"qrtn-{uuid4().hex[:8]}",
        questionnaire_token=uuid4().hex,
        status=ServiceCaseStatus.IN_SERVICE,
        started_at=datetime.now(timezone.utc) - timedelta(days=6),
    )
    db_session.add(case)
    await db_session.flush()

    msgs = []
    for i in range(n):
        m = Message(
            service_case_id=case.id,
            group_id=case.group_id,
            direction=MessageDirection.INBOUND,
            sender_type=MessageSenderType.USER,
            message_type="text",
            content=f"第{i}条消息",
            purpose=MessagePurpose.CHAT,
            status=MessageStatus.RECEIVED,
        )
        db_session.add(m)
        msgs.append(m)
    await db_session.flush()

    progress = ArchivistProgress(service_case_id=case.id, last_archived_message_id=0)
    db_session.add(progress)
    await db_session.flush()
    return case, msgs, progress


@pytest.mark.asyncio
async def test_batch_cap_limits_delta_to_oldest(db_session):
    """batch_cap=1：只处理最老 1 条，水位线只推进过这一条（不越过后续消息）。"""
    case, msgs, progress = await _setup_case_with_n_messages(db_session, 3)

    result = await run_archivist(
        db_session,
        service_case=case,
        progress=progress,
        chat_model=_SilentModel(),
        batch_cap=1,
    )
    await db_session.flush()

    assert result.delta_count == 1
    assert result.watermark_to == msgs[0].id
    assert progress.last_archived_message_id == msgs[0].id  # 后两条仍未归档


@pytest.mark.asyncio
async def test_quarantine_skips_oldest_and_resets(db_session, caplog):
    """隔离：推进水位线跳过最老那条、清零连续失败计数、留痕含 QUARANTINED 与 message_id。"""
    case, msgs, progress = await _setup_case_with_n_messages(db_session, 3)
    progress.consecutive_failures = 6
    progress.last_failure_reason = "boom: some write error"
    await db_session.flush()
    now = datetime.now(timezone.utc)

    with caplog.at_level(logging.ERROR):
        quarantined = await _quarantine_oldest_message(
            db_session, service_case_id=case.id, progress=progress, now=now
        )
    await db_session.flush()

    assert quarantined is True
    assert progress.last_archived_message_id == msgs[0].id  # 恰好跳过最老那一条
    assert progress.consecutive_failures == 0
    assert progress.last_failure_reason.startswith(f"QUARANTINED msg={msgs[0].id}")
    assert any("ARCHIVIST_QUARANTINE" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_quarantine_noop_when_no_delta(db_session):
    """无未归档增量可跳过时不动水位线，只清零失败计数、返回 False。"""
    case, msgs, progress = await _setup_case_with_n_messages(db_session, 1)
    progress.last_archived_message_id = msgs[0].id  # 已全部归档
    progress.consecutive_failures = 6
    await db_session.flush()

    quarantined = await _quarantine_oldest_message(
        db_session, service_case_id=case.id, progress=progress, now=datetime.now(timezone.utc)
    )
    await db_session.flush()

    assert quarantined is False
    assert progress.last_archived_message_id == msgs[0].id
    assert progress.consecutive_failures == 0
