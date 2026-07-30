"""档案官单批次调用、事务恢复与安全错误分类。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from langchain_core.messages import AIMessage

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
from app.services.archivist.errors import ArchivistMessageProcessingError
from app.services.archivist.graph import run_archivist
from app.services.archivist.trigger import _record_failure


class _BatchModel:
    def __init__(self, tool_calls):
        self.tool_calls = tool_calls
        self.calls: list[list] = []

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        self.calls.append(list(messages))
        return AIMessage(content="", tool_calls=self.tool_calls)


async def _setup_case(db_session, *, days: int = 1):
    user = User(name="档案官恢复测试用户")
    db_session.add(user)
    await db_session.flush()
    case = ServiceCase(
        case_no=f"TEST-REC-{uuid4().hex[:6].upper()}",
        user_id=user.id,
        group_id=f"rec-{uuid4().hex[:8]}",
        questionnaire_token=uuid4().hex,
        status=ServiceCaseStatus.IN_SERVICE,
        started_at=datetime.now(timezone.utc) - timedelta(days=6),
    )
    db_session.add(case)
    await db_session.flush()

    messages = []
    for offset in reversed(range(days)):
        created_at = datetime.now(timezone.utc) - timedelta(days=offset)
        message = Message(
            service_case_id=case.id,
            group_id=case.group_id,
            direction=MessageDirection.INBOUND,
            sender_type=MessageSenderType.USER,
            message_type="text",
            content=f"{created_at.date().isoformat()} 睡眠和执行记录",
            purpose=MessagePurpose.CHAT,
            status=MessageStatus.RECEIVED,
            created_at=created_at,
        )
        db_session.add(message)
        messages.append(message)
    await db_session.flush()
    progress = ArchivistProgress(service_case_id=case.id, last_archived_message_id=0)
    db_session.add(progress)
    await db_session.flush()
    return case, messages, progress


@pytest.mark.asyncio
async def test_multi_day_delta_invokes_archivist_once(db_session):
    case, messages, progress = await _setup_case(db_session, days=2)
    dates = [
        item.created_at.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
        for item in messages
    ]
    model = _BatchModel([])

    result = await run_archivist(
        db_session,
        service_case=case,
        progress=progress,
        chat_model=model,
    )
    await db_session.flush()

    assert len(model.calls) == 1
    prompt = model.calls[0][-1].content
    assert f"【日期 {dates[0]}】" in prompt
    assert f"【日期 {dates[1]}】" in prompt
    assert result.delta_count == 2
    assert progress.last_archived_message_id == messages[-1].id


@pytest.mark.asyncio
async def test_unified_write_failure_does_not_advance_watermark(
    db_session, monkeypatch
):
    case, _messages, progress = await _setup_case(db_session)
    original_watermark = progress.last_archived_message_id

    async def _fail(*args, **kwargs):
        raise RuntimeError("simulated unified write failure")

    monkeypatch.setattr(
        "app.services.archivist.graph.create_qa_drafts_from_archivist",
        _fail,
    )
    model = _BatchModel([])

    with pytest.raises(RuntimeError, match="simulated unified write failure"):
        await run_archivist(
            db_session,
            service_case=case,
            progress=progress,
            chat_model=model,
        )

    assert len(model.calls) == 1
    assert progress.last_archived_message_id == original_watermark


@pytest.mark.asyncio
async def test_transient_failure_never_becomes_quarantine_candidate(db_session):
    case, _, progress = await _setup_case(db_session)

    for _ in range(5):
        await _record_failure(
            case.id,
            error=TimeoutError("model gateway timeout"),
            now=datetime.now(timezone.utc),
        )
    await db_session.refresh(progress)

    assert progress.consecutive_failures == 5
    assert progress.last_failure_kind == "transient"
    assert progress.quarantine_candidate_failures == 0
    assert progress.quarantine_candidate_fingerprint is None


@pytest.mark.asyncio
async def test_only_same_deterministic_message_error_counts_toward_quarantine(db_session):
    case, _, progress = await _setup_case(db_session)

    for _ in range(2):
        await _record_failure(
            case.id,
            error=ArchivistMessageProcessingError("stable bad payload"),
            now=datetime.now(timezone.utc),
        )
    await db_session.refresh(progress)
    assert progress.last_failure_kind == "message"
    assert progress.quarantine_candidate_failures == 2

    await _record_failure(
        case.id,
        error=ArchivistMessageProcessingError("different bad payload"),
        now=datetime.now(timezone.utc),
    )
    await db_session.refresh(progress)
    assert progress.quarantine_candidate_failures == 1
