"""Archivist duplicate-eval -- deterministic isolation tests (FakeToolCallingModel only).

Default pytest stays zero-cost: every test here runs with a fake tool-calling
model. The single REAL-LLM path (``chat_model=None``) is marked skipif and only
runs when ``REAL_ARCHIVIST_EVAL=1``.
"""

from __future__ import annotations

import importlib.util
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import select

from app.models.archivist_extract_trace import ArchivistExtractTrace
from app.models.archivist_progress import ArchivistProgress
from app.models.checkin import Checkin
from app.models.checkin_followup_cycle import CheckinFollowupCycle
from app.models.message import (
    Message,
    MessageDirection,
    MessagePurpose,
    MessageSenderType,
    MessageStatus,
)
from app.models.outcome import OutcomeEvent
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user import User
from app.services.archivist.graph import (
    BUSINESS_DAY_START_HOUR,
    _sh_date,
    get_or_create_progress,
    run_archivist,
)

REAL_EVAL = os.getenv("REAL_ARCHIVIST_EVAL") == "1"
SH = ZoneInfo("Asia/Shanghai")


class FakeToolCallingModel:
    """Same injection shape as test_12: bind_tools returns self, ainvoke pops presets."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.bound_tools = []

    def bind_tools(self, tools):
        self.bound_tools = list(tools)
        return self

    async def ainvoke(self, messages):
        self.calls.append(list(messages))
        if not self.responses:
            return AIMessage(content="")
        return self.responses.pop(0)


def _load_eval_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "run_archivist_dedup_eval.py"
    spec = importlib.util.spec_from_file_location("run_archivist_dedup_eval", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _setup_dedup_case(db_session):
    connection = await db_session.connection()
    await connection.run_sync(
        lambda sc: CheckinFollowupCycle.__table__.create(sc, checkfirst=True)
    )
    await connection.run_sync(
        lambda sc: ArchivistExtractTrace.__table__.create(sc, checkfirst=True)
    )
    user = User(name="去重评估测试用户")
    db_session.add(user)
    await db_session.flush()
    case = ServiceCase(
        case_no=f"TEST-DEDUP-{uuid4().hex[:6].upper()}",
        user_id=user.id,
        group_id=f"dedup-test-{uuid4().hex[:8]}",
        questionnaire_token=uuid4().hex,
        status=ServiceCaseStatus.IN_SERVICE,
        started_at=datetime.now(timezone.utc) - timedelta(days=6),
    )
    db_session.add(case)
    await db_session.flush()
    progress = await get_or_create_progress(db_session, service_case_id=case.id)
    progress.last_archived_message_id = 0
    await db_session.flush()
    return case, progress


async def _add_user_message(db_session, case, content, *, day_offset=0, hour=12, minute=0):
    base = datetime.now(SH).replace(hour=0, minute=0, second=0, microsecond=0)
    created_at = (base + timedelta(days=day_offset, hours=hour, minutes=minute)).astimezone(timezone.utc)
    msg = Message(
        service_case_id=case.id,
        group_id=case.group_id,
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.USER,
        message_type="text",
        content=content,
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
        created_at=created_at,
    )
    db_session.add(msg)
    await db_session.flush()
    return msg


def test_business_day_starts_at_three_am():
    assert BUSINESS_DAY_START_HOUR == 3
    assert _sh_date(datetime(2026, 7, 30, 1, 50, tzinfo=SH)) == _sh_date(
        datetime(2026, 7, 30, 2, 59, tzinfo=SH)
    )
    assert _sh_date(datetime(2026, 7, 30, 3, 0, tzinfo=SH)).isoformat() == "2026-07-30"


def test_eval_group_spec_validates_without_llm():
    """Script spec builder/validator import cleanly and need no DB/LLM."""
    mod = _load_eval_script()
    spec = mod.build_group_spec(0, 0)
    assert spec["group_id"].startswith("dedup-eval-")
    summary = mod.validate_group_spec(spec)
    assert summary["batches"] == 3
    assert summary["messages"] == 6
    assert summary["business_day_start_hour"] == 3
    other = mod.build_group_spec(0, 1)
    assert other["group_id"] != spec["group_id"]  # unique per group-run


@pytest.mark.asyncio
async def test_duplicate_same_day_batches_merge_into_one_checkin(db_session):
    """Same-business-day duplicate batches must merge (upsert), not duplicate rows."""
    case, progress = await _setup_dedup_case(db_session)
    target = datetime.now(SH).date().isoformat()

    def _checkin_msg(content):
        return {
            "name": "report_checkin",
            "args": {"target_date": target, "weight": content},
            "id": f"c-{uuid4().hex[:6]}",
        }

    m1 = await _add_user_message(db_session, case, "今天早C喝了，体重168斤")
    fake1 = FakeToolCallingModel([AIMessage(content="", tool_calls=[_checkin_msg("168斤")])])
    await run_archivist(db_session, service_case=case, progress=progress, chat_model=fake1)
    await db_session.flush()
    assert progress.last_archived_message_id == m1.id

    m2 = await _add_user_message(db_session, case, "今天早C喝了，体重168斤", minute=30)
    fake2 = FakeToolCallingModel([AIMessage(content="", tool_calls=[_checkin_msg("168斤")])])
    await run_archivist(db_session, service_case=case, progress=progress, chat_model=fake2)
    await db_session.flush()

    rows = list(
        await db_session.scalars(
            select(Checkin).where(
                Checkin.service_case_id == case.id, Checkin.deleted_at.is_(None)
            )
        )
    )
    same_day = [r for r in rows if r.checkin_date.isoformat() == target]
    assert len(same_day) == 1
    assert same_day[0].parsed_json.get("weight") == "168斤"
    assert progress.last_archived_message_id == m2.id


@pytest.mark.asyncio
async def test_watermark_advances_and_history_is_context_only(db_session):
    """Sequential batches advance the watermark; batch N sees batch N-1 as 历史 context."""
    case, progress = await _setup_dedup_case(db_session)
    olds = [
        await _add_user_message(db_session, case, f"背景消息{i}", minute=i)
        for i in range(3)
    ]
    fake1 = FakeToolCallingModel([AIMessage(content="", tool_calls=[])])
    await run_archivist(db_session, service_case=case, progress=progress, chat_model=fake1)
    await db_session.flush()
    assert progress.last_archived_message_id == olds[-1].id

    news = [
        await _add_user_message(db_session, case, "新消息A", minute=40),
        await _add_user_message(db_session, case, "新消息B", minute=41),
    ]
    fake2 = FakeToolCallingModel([AIMessage(content="", tool_calls=[])])
    await run_archivist(db_session, service_case=case, progress=progress, chat_model=fake2)
    await db_session.flush()

    assert progress.last_archived_message_id == news[-1].id
    assert len(fake2.calls) == 1
    rendered = fake2.calls[0][1].content
    assert "新消息A" in rendered and "新消息B" in rendered
    # Previous batch is visible only as 历史 context, never as new facts.
    assert f"[{olds[0].id}]" in rendered
    history_lines = [ln for ln in rendered.splitlines() if f"[{olds[0].id}]" in ln]
    assert history_lines and "历史" in history_lines[0]


@pytest.mark.skipif(not REAL_EVAL, reason="real archivist eval requires REAL_ARCHIVIST_EVAL=1")
@pytest.mark.asyncio
async def test_real_archivist_single_batch(db_session):
    """REAL path: run_archivist with chat_model=None (real LLM). Skipped by default."""
    case, progress = await _setup_dedup_case(db_session)
    msg = await _add_user_message(db_session, case, "今天早C喝了，体重168斤")
    result = await run_archivist(db_session, service_case=case, progress=progress, chat_model=None)
    await db_session.flush()
    assert progress.last_archived_message_id == msg.id
    assert result.delta_count >= 1
    events = list(
        await db_session.scalars(
            select(OutcomeEvent).where(OutcomeEvent.service_case_id == case.id)
        )
    )
    assert isinstance(events, list)
