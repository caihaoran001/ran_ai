from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.checkin_checklist_version import CheckinChecklistVersion
from app.models.message import (
    Message,
    MessageDirection,
    MessagePurpose,
    MessageSenderType,
    MessageStatus,
)
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user_document import DocumentStatus, DocumentType, UserDocument
from app.models.user_profile import UserProfile
from app.services.agent.state import AgentRunContext
from app.services.agent.tools.checkin_checklist_tools import (
    build_checkin_checklist_tools,
)
from app.services.archivist.followup import compose_document_checklist_followup
from app.services.checkin_checklists import (
    PRODUCT_CHECKIN_QUESTION,
    get_effective_daily_checklist,
    normalize_manual_followup_items,
    normalize_manual_checklist_items,
)
from app.services.checkin_presentation import load_personalized_checkin_labels


EDITABLE_QUESTIONS = [
    "今天蔬菜吃够两拳了吗？",
    "今天完成了计划内运动吗？",
    "昨晚几点入睡？",
    "今天的精神状态打几分？",
]
QUESTIONS = [PRODUCT_CHECKIN_QUESTION, *EDITABLE_QUESTIONS]


async def _case(db_session) -> ServiceCase:
    token = uuid4().hex
    service_case = ServiceCase(
        case_no=f"CHECKLIST-{token[:8]}",
        group_id=f"group-{token[:8]}",
        questionnaire_token=token,
        status=ServiceCaseStatus.IN_SERVICE,
        service_plan_start_date=date.today(),
        started_at=datetime.now(timezone.utc),
    )
    db_session.add(service_case)
    await db_session.flush()
    return service_case


async def _source(
    db_session,
    service_case: ServiceCase,
    *,
    sender_type: MessageSenderType = MessageSenderType.STAFF,
    explicit: bool = True,
    query: str | None = None,
) -> Message:
    query = query or "设置第2到5项追问：" + "；".join(EDITABLE_QUESTIONS)
    meta = {
        "sender_id": "staff-1",
        "sender_name": "班主任小王",
        "sender_type": sender_type.value,
    }
    if explicit:
        meta["explicit_bot_query_text"] = query
    message = Message(
        service_case_id=service_case.id,
        group_id=service_case.group_id,
        direction=MessageDirection.INBOUND,
        sender_type=sender_type,
        message_type="text",
        content=f"@燃人AI {query}",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
        analysis_json={"_qiwe_meta": meta},
    )
    db_session.add(message)
    await db_session.flush()
    return message


def _tools(context: AgentRunContext):
    return {tool.name: tool for tool in build_checkin_checklist_tools(context=context)}


@pytest.mark.asyncio
async def test_staff_explicit_mention_sets_items_two_to_five_and_locks_product(db_session):
    service_case = await _case(db_session)
    source = await _source(db_session, service_case)
    tool = _tools(
        AgentRunContext(service_case_id=service_case.id, trigger_id=source.id)
    )["set_checkin_checklist"]

    result = await tool.ainvoke({"questions": EDITABLE_QUESTIONS})
    retry_result = await tool.ainvoke({"questions": EDITABLE_QUESTIONS})

    assert result == {
        "ok": True,
        "updated": True,
        "source_type": "staff_override",
        "version_no": 1,
        "questions": QUESTIONS,
    }
    assert retry_result == result
    versions = list(await db_session.scalars(select(CheckinChecklistVersion)))
    assert len(versions) == 1
    version = versions[0]
    assert version is not None
    assert version.source_message_id == source.id
    assert version.changed_by_id == "staff-1"
    assert version.changed_by_name == "班主任小王"
    assert [item["text"] for item in version.items_json] == QUESTIONS


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sender_type", "explicit"),
    [
        (MessageSenderType.USER, True),
        (MessageSenderType.STAFF, False),
    ],
)
async def test_set_rejects_user_or_message_without_explicit_mention(
    db_session, sender_type, explicit
):
    service_case = await _case(db_session)
    source = await _source(
        db_session,
        service_case,
        sender_type=sender_type,
        explicit=explicit,
    )
    tool = _tools(
        AgentRunContext(service_case_id=service_case.id, trigger_id=source.id)
    )["set_checkin_checklist"]

    result = await tool.ainvoke({"questions": EDITABLE_QUESTIONS})

    assert result["ok"] is False
    assert await db_session.scalar(select(CheckinChecklistVersion)) is None


@pytest.mark.asyncio
async def test_set_rejects_questions_not_written_in_same_message(db_session):
    service_case = await _case(db_session)
    source = await _source(
        db_session,
        service_case,
        query="请设置我下面写的五项：早餐、蔬菜、运动、睡眠、精神状态",
    )
    tool = _tools(
        AgentRunContext(service_case_id=service_case.id, trigger_id=source.id)
    )["set_checkin_checklist"]

    result = await tool.ainvoke({"questions": EDITABLE_QUESTIONS})

    assert result["ok"] is False
    assert "不能补写或改写" in result["error"]
    assert await db_session.scalar(select(CheckinChecklistVersion)) is None


def test_manual_checklist_requires_five_nonempty_unique_questions():
    with pytest.raises(ValueError, match="正好提供 5 项"):
        normalize_manual_checklist_items(QUESTIONS[:4])
    with pytest.raises(ValueError, match="空内容"):
        normalize_manual_checklist_items([*QUESTIONS[:4], ""])
    with pytest.raises(ValueError, match="重复内容"):
        normalize_manual_checklist_items([*QUESTIONS[:4], QUESTIONS[0]])
    with pytest.raises(ValueError, match="正好提供 4 项"):
        normalize_manual_followup_items(EDITABLE_QUESTIONS[:3])


@pytest.mark.asyncio
async def test_safe_default_also_locks_first_item_to_product_schedule(db_session):
    service_case = await _case(db_session)

    checklist = await get_effective_daily_checklist(
        db_session, service_case_id=service_case.id
    )

    assert checklist.source_type == "safe_default"
    assert checklist.resolved_items()[0]["label"] == PRODUCT_CHECKIN_QUESTION
    assert checklist.resolved_items()[0]["tracking_kind"] == "product_schedule"


@pytest.mark.asyncio
async def test_legacy_manual_version_is_read_with_locked_product_item(db_session):
    service_case = await _case(db_session)
    db_session.add(
        CheckinChecklistVersion(
            service_case_id=service_case.id,
            version_no=1,
            items_json=[
                {"text": "历史可编辑第1项"},
                *[{"text": question} for question in EDITABLE_QUESTIONS],
            ],
            source_type="staff_override",
        )
    )
    await db_session.flush()

    checklist = await get_effective_daily_checklist(
        db_session, service_case_id=service_case.id
    )

    assert [item["label"] for item in checklist.resolved_items()] == QUESTIONS
    assert checklist.resolved_items()[0]["tracking_kind"] == "product_schedule"


@pytest.mark.asyncio
async def test_restore_default_creates_next_version_without_erasing_history(db_session):
    service_case = await _case(db_session)
    source = await _source(db_session, service_case)
    tools = _tools(
        AgentRunContext(service_case_id=service_case.id, trigger_id=source.id)
    )
    await tools["set_checkin_checklist"].ainvoke({"questions": EDITABLE_QUESTIONS})

    reset_source = await _source(
        db_session,
        service_case,
        query="恢复系统默认五项",
    )
    reset_tool = _tools(
        AgentRunContext(service_case_id=service_case.id, trigger_id=reset_source.id)
    )["restore_default_checkin_checklist"]
    result = await reset_tool.ainvoke({})

    assert result["ok"] is True
    assert result["restored"] is True
    assert result["source_type"] == "system_default"
    assert result["version_no"] == 2
    versions = list(
        await db_session.scalars(
            select(CheckinChecklistVersion).order_by(CheckinChecklistVersion.version_no)
        )
    )
    assert [version.source_type for version in versions] == [
        "staff_override",
        "system_default",
    ]


@pytest.mark.asyncio
async def test_manual_version_wins_over_unsent_document_and_generated_profile(
    db_session,
):
    service_case = await _case(db_session)
    db_session.add(
        UserDocument(
            service_case_id=service_case.id,
            document_type=DocumentType.DAY28_EXECUTION_STANDARD,
            title="未发送执行单",
            content_md="内部内容",
            content_json={
                "blocks": [
                    {
                        "key": "daily_checkin",
                        "items": [f"内部问题{i}" for i in range(1, 6)],
                    }
                ]
            },
            status=DocumentStatus.REVIEWING,
        )
    )
    db_session.add(
        UserProfile(
            service_case_id=service_case.id,
            profile_json={
                "personalized_checkin_fields": [
                    {"title": f"画像问题{i}"} for i in range(1, 6)
                ]
            },
            summary="测试画像",
        )
    )
    await db_session.flush()
    system_checklist = await get_effective_daily_checklist(
        db_session, service_case_id=service_case.id
    )
    assert system_checklist.source_type == "generated_profile"
    assert [item["label"] for item in system_checklist.resolved_items()] == [
        PRODUCT_CHECKIN_QUESTION,
        *[f"画像问题{i}" for i in range(2, 6)],
    ]
    source = await _source(db_session, service_case)
    await _tools(
        AgentRunContext(service_case_id=service_case.id, trigger_id=source.id)
    )["set_checkin_checklist"].ainvoke({"questions": EDITABLE_QUESTIONS})

    checklist = await get_effective_daily_checklist(
        db_session, service_case_id=service_case.id
    )

    assert checklist.source_type == "staff_override"
    assert [item["label"] for item in checklist.resolved_items()] == QUESTIONS


@pytest.mark.asyncio
async def test_manual_question_ids_restore_historical_labels(db_session):
    service_case = await _case(db_session)
    source = await _source(db_session, service_case)
    await _tools(
        AgentRunContext(service_case_id=service_case.id, trigger_id=source.id)
    )["set_checkin_checklist"].ainvoke({"questions": EDITABLE_QUESTIONS})
    version = await db_session.scalar(select(CheckinChecklistVersion))

    labels = await load_personalized_checkin_labels(
        db_session,
        service_case_id=service_case.id,
        item_ids=[f"checklist:{version.id}:2"],
    )

    assert labels[f"checklist:{version.id}:2"] == QUESTIONS[1]


@pytest.mark.asyncio
async def test_followup_uses_manual_questions_without_document(db_session):
    service_case = await _case(db_session)
    source = await _source(db_session, service_case)
    await _tools(
        AgentRunContext(service_case_id=service_case.id, trigger_id=source.id)
    )["set_checkin_checklist"].ainvoke({"questions": EDITABLE_QUESTIONS})

    followup = await compose_document_checklist_followup(
        db_session,
        service_case=service_case,
        target_date=date.today(),
    )

    assert followup is not None
    assert [item["label"] for item in followup.expected_items] == QUESTIONS
    assert all(item["id"].startswith("checklist:") for item in followup.expected_items)
