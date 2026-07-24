"""档案官重启恢复：事务内 outbox、失败退避与安全错误分类。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import select

from app.models.archivist_post_commit_task import ArchivistPostCommitTask
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
from app.services.archivist.post_commit_tasks import (
    enqueue_archivist_post_commit_tasks,
    run_archivist_post_commit_tasks,
)
from app.services.archivist.trigger import _record_failure


class _CheckinModel:
    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "report_checkin",
                    "args": {"sleep": "昨晚睡了7小时"},
                    "id": "checkin-1",
                }
            ],
        )


async def _setup_case(db_session):
    connection = await db_session.connection()
    await connection.run_sync(
        lambda conn: ArchivistPostCommitTask.__table__.create(conn, checkfirst=True)
    )
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
    message = Message(
        service_case_id=case.id,
        group_id=case.group_id,
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.USER,
        message_type="text",
        content="这周睡眠改善了，今天也完成了打卡。",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
    )
    db_session.add(message)
    await db_session.flush()
    return case, message


@pytest.mark.asyncio
async def test_post_tasks_are_durable_and_deduplicated(db_session):
    case, message = await _setup_case(db_session)
    result = SimpleNamespace(
        outcome_written=1,
        profile_written=1,
        personal_corrections_written=0,
        upserts=[{"filled": ["sleep"]}],
        faq_candidates=[{"question": "怎么改善睡眠？", "answer": "先固定起床时间。"}],
    )

    created = await enqueue_archivist_post_commit_tasks(
        db_session,
        service_case_id=case.id,
        watermark_to=message.id,
        run_result=result,
    )
    duplicate = await enqueue_archivist_post_commit_tasks(
        db_session,
        service_case_id=case.id,
        watermark_to=message.id,
        run_result=result,
    )
    await db_session.flush()

    tasks = list(
        await db_session.scalars(
            select(ArchivistPostCommitTask).where(
                ArchivistPostCommitTask.service_case_id == case.id
            )
        )
    )
    assert created == 3
    assert duplicate == 0
    assert {task.task_type for task in tasks} == {
        ArchivistPostCommitTask.TYPE_CASE_MEMORY,
        ArchivistPostCommitTask.TYPE_FAQ_DRAFTS,
        ArchivistPostCommitTask.TYPE_CASE_SYNTHESIS,
    }
    assert all(task.status == ArchivistPostCommitTask.STATUS_PENDING for task in tasks)


@pytest.mark.asyncio
async def test_archivist_commit_writes_watermark_and_post_task_together(db_session):
    case, message = await _setup_case(db_session)
    progress = ArchivistProgress(service_case_id=case.id, last_archived_message_id=0)
    db_session.add(progress)
    await db_session.flush()

    result = await run_archivist(
        db_session,
        service_case=case,
        progress=progress,
        chat_model=_CheckinModel(),
    )
    await db_session.flush()

    task = await db_session.scalar(
        select(ArchivistPostCommitTask).where(
            ArchivistPostCommitTask.service_case_id == case.id,
            ArchivistPostCommitTask.watermark_to == message.id,
            ArchivistPostCommitTask.task_type
            == ArchivistPostCommitTask.TYPE_CASE_MEMORY,
        )
    )
    assert result.watermark_to == message.id
    assert progress.last_archived_message_id == message.id
    assert task is not None
    assert task.status == ArchivistPostCommitTask.STATUS_PENDING


@pytest.mark.asyncio
async def test_post_task_failure_backs_off_and_retries(db_session, monkeypatch):
    case, message = await _setup_case(db_session)
    task = ArchivistPostCommitTask(
        service_case_id=case.id,
        watermark_to=message.id,
        task_type=ArchivistPostCommitTask.TYPE_CASE_MEMORY,
        payload_json={"outcome_written": 1, "upserts": []},
        status=ArchivistPostCommitTask.STATUS_PENDING,
        attempt_count=0,
    )
    db_session.add(task)
    await db_session.flush()
    now = datetime.now(timezone.utc)

    async def _fail(_task):
        raise TimeoutError("temporary model outage")

    monkeypatch.setattr(
        "app.services.archivist.post_commit_tasks._execute_post_commit_task",
        _fail,
    )
    first = await run_archivist_post_commit_tasks(now=now)
    await db_session.refresh(task)
    assert first == {"claimed": 1, "succeeded": 0, "failed": 1}
    assert task.status == ArchivistPostCommitTask.STATUS_FAILED
    assert task.attempt_count == 1
    assert task.next_attempt_at is not None

    too_early = await run_archivist_post_commit_tasks(now=now)
    assert too_early["claimed"] == 0

    async def _succeed(_task):
        return None

    monkeypatch.setattr(
        "app.services.archivist.post_commit_tasks._execute_post_commit_task",
        _succeed,
    )
    retried = await run_archivist_post_commit_tasks(
        now=task.next_attempt_at + timedelta(seconds=1)
    )
    await db_session.refresh(task)
    assert retried == {"claimed": 1, "succeeded": 1, "failed": 0}
    assert task.status == ArchivistPostCommitTask.STATUS_SUCCEEDED
    assert task.attempt_count == 2


@pytest.mark.asyncio
async def test_transient_failure_never_becomes_quarantine_candidate(db_session):
    case, _ = await _setup_case(db_session)
    progress = ArchivistProgress(service_case_id=case.id, last_archived_message_id=0)
    db_session.add(progress)
    await db_session.flush()

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
    case, _ = await _setup_case(db_session)
    progress = ArchivistProgress(service_case_id=case.id, last_archived_message_id=0)
    db_session.add(progress)
    await db_session.flush()

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
