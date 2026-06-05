from __future__ import annotations

import secrets
from datetime import datetime, timezone

import pytest
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
from app.models.user_document import DocumentStatus, DocumentType, UserDocument
from app.services.ranren_ai.engine import RanrenAIEngine, _requires_human_handoff
from app.services.intervention_service import merge_case_insight, normalize_insight


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
    insight = normalize_insight(insight, source="chat_message", content=content)
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
    assert case.needs_human_intervention is True
    assert any(item.get("code") == "refund_request" for item in (case.intervention_reasons or []))


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


def test_explicit_robot_stop_becomes_human_handoff_analysis():
    insight = normalize_insight(
        {
            "requires_human_intervention": True,
            "requires_immediate_safety_reply": False,
            "intervention_reasons": [
                {
                    "code": "robot_stop",
                    "level": "medium",
                    "reason": "用户明确要求停止机器人继续解释。",
                    "evidence": "我不接受机器人继续解释",
                    "source": "chat_message",
                }
            ],
        },
        source="chat_message",
        content="我不接受机器人继续解释，别说了",
    )

    assert insight["requires_human_intervention"] is True
    assert insight["requires_immediate_safety_reply"] is False
    assert _requires_human_handoff(insight) is True


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


@pytest.mark.asyncio
async def test_recent_chat_history_includes_document_delivery(db_session, mock_message_channel, cai_haoran_answers):
    case_id, group_id = await _setup_case_with_intake(db_session, cai_haoran_answers)
    db_session.add(
        Message(
            service_case_id=case_id,
            group_id=group_id,
            direction=MessageDirection.INBOUND,
            sender_type=MessageSenderType.USER,
            message_type="text",
            content="收到",
            purpose=MessagePurpose.CHAT,
            status=MessageStatus.RECEIVED,
        )
    )
    db_session.add(
        Message(
            service_case_id=case_id,
            group_id=group_id,
            direction=MessageDirection.OUTBOUND,
            sender_type=MessageSenderType.BOT,
            message_type="markdown",
            content="两份专属文件已生成，请查收：1.《个人能量重启处方单》 2.《28 天执行标准与打卡要求》",
            purpose=MessagePurpose.DOCUMENT_DELIVERY,
            status=MessageStatus.SENT,
        )
    )
    await db_session.flush()

    engine = RanrenAIEngine(mock_message_channel)
    lines = await engine._recent_user_messages(db_session, case_id)

    assert any("营养师: 两份专属文件已生成，请查收" in line for line in lines)
    assert any("用户: 收到" in line for line in lines)


@pytest.mark.asyncio
async def test_user_context_mentions_document_progress(db_session, mock_message_channel, cai_haoran_answers):
    case_id, group_id = await _setup_case_with_intake(db_session, cai_haoran_answers)
    case = await db_session.scalar(select(ServiceCase).where(ServiceCase.id == case_id))
    assert case is not None
    case.status = ServiceCaseStatus.IN_SERVICE
    db_session.add(
        UserDocument(
            service_case_id=case_id,
            document_type=DocumentType.ENERGY_RESTART_PRESCRIPTION,
            title="个人能量重启处方单",
            content_md="ok",
            content_json={},
            file_path="/tmp/a.docx",
            status=DocumentStatus.SENT,
        )
    )
    db_session.add(
        UserDocument(
            service_case_id=case_id,
            document_type=DocumentType.DAY28_EXECUTION_STANDARD,
            title="28天执行标准与打卡要求",
            content_md="ok",
            content_json={},
            file_path="/tmp/b.docx",
            status=DocumentStatus.REVIEWING,
        )
    )
    db_session.add(
        Message(
            service_case_id=case_id,
            group_id=group_id,
            direction=MessageDirection.OUTBOUND,
            sender_type=MessageSenderType.BOT,
            message_type="document",
            content="已发送文件：个人能量重启处方单.docx",
            purpose=MessagePurpose.DOCUMENT_DELIVERY,
            status=MessageStatus.SENT,
        )
    )
    await db_session.flush()

    engine = RanrenAIEngine(mock_message_channel)
    user_context = await engine._build_user_context(db_session, case)

    assert "当前服务单状态：in_service" in user_context
    assert "个人能量重启处方单：已发送" in user_context
    assert "28天执行标准与打卡要求：待审核" in user_context
    assert "最近一次交付动作：已发送文件：个人能量重启处方单.docx" in user_context
