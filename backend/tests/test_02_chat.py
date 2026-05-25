from __future__ import annotations

import secrets
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.message import (
    Message,
    MessageDirection,
    MessagePurpose,
    MessageSenderType,
    MessageStatus,
)
from app.models.questionnaire import QuestionnaireSubmission
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user import User
from app.services.intervention_service import merge_case_insight


@pytest_asyncio.fixture(autouse=True)
async def _cleanup_engine():
    from app.db.session import engine
    yield
    await engine.dispose()


async def _setup_case_with_intake(db_session, cai_haoran_answers):
    group_id = f"test-chat-{secrets.token_hex(6)}"
    token = secrets.token_urlsafe(24)

    user = User(name="聊天测试用户")
    db_session.add(user)
    await db_session.flush()

    case = ServiceCase(
        case_no=f"TEST-CHAT-{secrets.token_hex(4).upper()}",
        user_id=user.id,
        group_id=group_id,
        questionnaire_token=token,
        status=ServiceCaseStatus.GENERATING,
        started_at=datetime.now(timezone.utc),
    )
    db_session.add(case)
    await db_session.flush()

    submission = QuestionnaireSubmission(
        service_case_id=case.id,
        template_key="intake",
        answers_json=cai_haoran_answers,
    )
    db_session.add(submission)
    await db_session.flush()

    return case.id, group_id


async def _ingest_and_analyze(db_session, case_id, group_id, content, mock_ai):
    insight = await mock_ai.extract_intervention_insight(
        source="chat_message",
        content=content,
    )
    case = await db_session.scalar(select(ServiceCase).where(ServiceCase.id == case_id))
    assert case is not None
    merge_case_insight(case, insight)

    db_session.add(Message(
        service_case_id=case_id,
        group_id=group_id,
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.USER,
        message_type="text",
        content=content,
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
        analysis_json=insight,
    ))
    await db_session.flush()
    return insight


@pytest.mark.asyncio
async def test_normal_message_no_risk(db_session, mock_ai, cai_haoran_answers):
    case_id, group_id = await _setup_case_with_intake(db_session, cai_haoran_answers)
    await _ingest_and_analyze(
        db_session, case_id, group_id,
        "老师好，我已经提交问卷了，接下来要做什么？",
        mock_ai,
    )
    case = await db_session.scalar(select(ServiceCase).where(ServiceCase.id == case_id))
    assert case.risk_level != "high"
    assert case.needs_human_intervention is False


@pytest.mark.asyncio
async def test_high_risk_chronic_disease(db_session, mock_ai, cai_haoran_answers):
    case_id, group_id = await _setup_case_with_intake(db_session, cai_haoran_answers)
    await _ingest_and_analyze(
        db_session, case_id, group_id,
        "我有高血压，一直在吃降压药，这个产品有影响吗",
        mock_ai,
    )
    case = await db_session.scalar(select(ServiceCase).where(ServiceCase.id == case_id))
    assert case.risk_level == "high"
    assert case.needs_human_intervention is True
    risk_codes = [rf["code"] for rf in (case.risk_flags or [])]
    assert "chronic_disease" in risk_codes


@pytest.mark.asyncio
async def test_emotional_crisis(db_session, mock_ai, cai_haoran_answers):
    case_id, group_id = await _setup_case_with_intake(db_session, cai_haoran_answers)
    await _ingest_and_analyze(
        db_session, case_id, group_id,
        "最近压力太大快崩溃了，什么都不想做",
        mock_ai,
    )
    case = await db_session.scalar(select(ServiceCase).where(ServiceCase.id == case_id))
    assert case.risk_level == "high"
    assert case.needs_human_intervention is True


@pytest.mark.asyncio
async def test_refund_intent(db_session, mock_ai, cai_haoran_answers):
    case_id, group_id = await _setup_case_with_intake(db_session, cai_haoran_answers)
    await _ingest_and_analyze(
        db_session, case_id, group_id,
        "我想退款，效果不好想退了",
        mock_ai,
    )
    case = await db_session.scalar(select(ServiceCase).where(ServiceCase.id == case_id))
    assert case.refund_intent_flags is not None
    assert len(case.refund_intent_flags) > 0


@pytest.mark.asyncio
async def test_renewal_intent(db_session, mock_ai, cai_haoran_answers):
    case_id, group_id = await _setup_case_with_intake(db_session, cai_haoran_answers)
    await _ingest_and_analyze(
        db_session, case_id, group_id,
        "28天快到了想继续续一期",
        mock_ai,
    )
    case = await db_session.scalar(select(ServiceCase).where(ServiceCase.id == case_id))
    assert case.renewal_intent_flags is not None
    assert len(case.renewal_intent_flags) > 0


@pytest.mark.asyncio
async def test_ai_reply_stored_as_outbound(db_session, mock_ai, cai_haoran_answers):
    case_id, group_id = await _setup_case_with_intake(db_session, cai_haoran_answers)
    await _ingest_and_analyze(
        db_session, case_id, group_id,
        "老师好，今天感觉不错",
        mock_ai,
    )

    db_session.add(Message(
        service_case_id=case_id,
        group_id=group_id,
        direction=MessageDirection.OUTBOUND,
        sender_type=MessageSenderType.BOT,
        message_type="markdown",
        content="收到您的反馈, 继续加油!",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.SENT,
    ))
    await db_session.flush()

    msgs_result = await db_session.scalars(
        select(Message)
        .where(
            Message.service_case_id == case_id,
            Message.direction == MessageDirection.OUTBOUND,
            Message.sender_type == MessageSenderType.BOT,
        )
    )
    outbound_msgs = list(msgs_result)
    assert len(outbound_msgs) > 0
    assert all(m.sender_type == MessageSenderType.BOT for m in outbound_msgs)
