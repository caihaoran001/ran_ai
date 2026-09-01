from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.models.message import (
    Message,
    MessageDirection,
    MessagePurpose,
    MessageSenderType,
    MessageStatus,
)
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.services.agent.state import AgentRunContext
from app.services.agent.tools.execution_commitment_tools import (
    build_execution_commitment_tools,
)


def _case() -> ServiceCase:
    token = uuid4().hex
    return ServiceCase(
        case_no=f"COMMIT-{token[:10]}",
        group_id=f"commit-{token[:10]}",
        questionnaire_token=token,
        status=ServiceCaseStatus.IN_SERVICE,
        service_plan_start_date=date(2026, 9, 1),
        started_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )


async def _setup_choice_messages(db_session, *, reply_after_invitation: bool):
    service_case = _case()
    db_session.add(service_case)
    await db_session.flush()
    invitation_at = datetime(2026, 9, 1, 6, 0, tzinfo=timezone.utc)
    invitation = Message(
        service_case_id=service_case.id,
        group_id=service_case.group_id,
        direction=MessageDirection.OUTBOUND,
        sender_type=MessageSenderType.BOT,
        message_type="markdown",
        content="请选择高承诺档或标准档",
        purpose=MessagePurpose.DOCUMENT_DELIVERY,
        status=MessageStatus.SENT,
        created_at=invitation_at,
        analysis_json={"execution_commitment": {"kind": "invitation"}},
    )
    db_session.add(invitation)
    await db_session.flush()
    service_case.execution_commitment = {
        "version": 1,
        "high_commitment_candidate": True,
        "standard_target": "28天减重5-7斤",
        "high_commitment_target": "28天向8-10斤推进",
        "execution_path": "standard",
        "status": "pending",
        "reviews": {},
        "invitation": {"status": "sent", "message_id": invitation.id},
    }
    reply_at = invitation_at + (
        timedelta(minutes=1) if reply_after_invitation else -timedelta(minutes=1)
    )
    source = Message(
        service_case_id=service_case.id,
        group_id=service_case.group_id,
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.USER,
        message_type="text",
        content="选择高承诺档",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
        created_at=reply_at,
        analysis_json={"_qiwe_meta": {"sender_id": "user-1", "sender_name": "用户"}},
    )
    db_session.add(source)
    await db_session.flush()
    return service_case, source


def _tool(service_case_id: int, source_message_id: int):
    tools = build_execution_commitment_tools(
        context=AgentRunContext(
            service_case_id=service_case_id,
            trigger_id=source_message_id,
        )
    )
    return {tool.name: tool for tool in tools}["confirm_execution_commitment"]


@pytest.mark.asyncio
async def test_commitment_tool_rejects_reply_created_before_invitation(db_session):
    service_case, source = await _setup_choice_messages(
        db_session, reply_after_invitation=False
    )

    result = await _tool(service_case.id, source.id).ainvoke(
        {"choice": "high_commitment"}
    )

    assert result["ok"] is False
    assert "早于执行邀请" in result["error"]
    assert service_case.execution_commitment["status"] == "pending"


@pytest.mark.asyncio
async def test_commitment_tool_accepts_explicit_reply_after_invitation(db_session):
    service_case, source = await _setup_choice_messages(
        db_session, reply_after_invitation=True
    )

    result = await _tool(service_case.id, source.id).ainvoke(
        {"choice": "high_commitment"}
    )

    assert result["ok"] is True
    assert result["execution_path"] == "high_commitment"
    assert service_case.execution_commitment["confirmation"]["source_message_id"] == source.id
