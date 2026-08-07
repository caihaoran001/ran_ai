"""优秀案例由同一轮档案官判断并在主事务确定性落库。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import select

from app.models.archivist_progress import ArchivistProgress
from app.models.knowledge_draft import KnowledgeDraft
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


class _CaseModel:
    def __init__(self, tool_calls):
        self.tool_calls = tool_calls
        self.calls = 0

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        self.calls += 1
        return AIMessage(content="", tool_calls=self.tool_calls)


async def _setup(db_session):
    user = User(name="案例触发测试")
    db_session.add(user)
    await db_session.flush()
    case = ServiceCase(
        case_no=f"TEST-SYN-{uuid4().hex[:6].upper()}",
        user_id=user.id,
        group_id=f"syn-{uuid4().hex[:8]}",
        questionnaire_token=uuid4().hex,
        status=ServiceCaseStatus.IN_SERVICE,
        started_at=datetime.now(timezone.utc) - timedelta(days=20),
    )
    db_session.add(case)
    await db_session.flush()
    messages = []
    for content in ("之前总是坚持不下来", "最近连续执行，体重已经下降2斤"):
        message = Message(
            service_case_id=case.id,
            group_id=case.group_id,
            direction=MessageDirection.INBOUND,
            sender_type=MessageSenderType.USER,
            message_type="text",
            content=content,
            purpose=MessagePurpose.CHAT,
            status=MessageStatus.RECEIVED,
        )
        db_session.add(message)
        messages.append(message)
    await db_session.flush()
    progress = ArchivistProgress(service_case_id=case.id, last_archived_message_id=0)
    db_session.add(progress)
    await db_session.flush()
    return case, messages, progress


def _case_candidate(message_ids: list[int]) -> dict:
    return {
        "name": "report_case_candidate",
        "args": {
            "title": "从难坚持到稳定执行",
            "question": "用户此前执行容易中断。",
            "answer": "用户逐步建立稳定执行节奏，并取得持续的阶段改善。",
            "category": "坚持执行",
            "tags": ["稳定执行", "阶段改善"],
            "reason": "起点、行动和持续结果完整，具有复用价值。",
            "evidence": "从执行易中断转为连续执行，体重累计下降2斤。",
            "source_message_ids": message_ids,
        },
        "id": "case-1",
    }


@pytest.mark.asyncio
async def test_case_candidate_without_new_outcome_is_not_written(db_session):
    case, messages, progress = await _setup(db_session)
    model = _CaseModel([_case_candidate([item.id for item in messages])])

    result = await run_archivist(
        db_session,
        service_case=case,
        progress=progress,
        chat_model=model,
    )
    await db_session.flush()

    assert model.calls == 1
    assert result.outcome_written == 0
    assert result.case_written == 0
    drafts = list(
        await db_session.scalars(
            select(KnowledgeDraft).where(
                KnowledgeDraft.draft_type == "case",
            )
        )
    )
    assert [
        draft
        for draft in drafts
        if (draft.content_json or {}).get("service_case_id") == case.id
    ] == []


@pytest.mark.asyncio
async def test_new_outcome_and_case_are_written_by_same_call(db_session):
    case, messages, progress = await _setup(db_session)
    model = _CaseModel(
        [
            {
                "name": "report_outcome",
                "args": {
                    "field_key": "weight_change",
                    "event_type": "improved",
                    "value": "体重累计下降2斤",
                    "evidence_text": "体重已经下降2斤",
                    "confidence": 0.95,
                },
                "id": "outcome-1",
            },
            {
                "name": "report_memory",
                "args": {
                    "user_summary": "用户近期连续执行，体重累计下降2斤。",
                    "case_material_summary": (
                        "起点是执行容易中断；近期建立连续执行节奏，"
                        "体重累计下降2斤，已形成初步稳定改善。"
                    ),
                },
                "id": "memory-1",
            },
            _case_candidate([item.id for item in messages]),
        ]
    )

    result = await run_archivist(
        db_session,
        service_case=case,
        progress=progress,
        chat_model=model,
    )
    await db_session.flush()

    assert model.calls == 1
    assert result.outcome_written == 1
    assert result.memory_written == 1
    assert result.case_written == 1
    assert {
        item["kind"] for item in result.change_details
    } >= {"outcome", "user_summary", "case_material", "case_draft"}
    draft = await db_session.scalar(
        select(KnowledgeDraft).where(
            KnowledgeDraft.draft_type == "case",
            KnowledgeDraft.status == "pending",
        )
    )
    assert draft is not None
    assert draft.title == "从难坚持到稳定执行"
    assert draft.source_message_ids == [item.id for item in messages]
    assert (draft.content_json or {}).get("scan_type") == "archivist_outcome_stable"
    assert "起点是执行容易中断" in (
        (draft.content_json or {}).get("case_material_summary") or ""
    )


@pytest.mark.asyncio
async def test_negative_only_outcome_does_not_trigger_case_draft(db_session):
    """成效表收全方向后的回归闸门：一批纯负向成效（睡得更差）虽然写进了账本，
    也不能触发优秀案例草稿——否则用户吐槽一通反而被当成优秀案例素材。"""
    case, messages, progress = await _setup(db_session)
    model = _CaseModel(
        [
            {
                "name": "report_outcome",
                "args": {
                    "field_key": "sleep_change",
                    "event_type": "worsened",
                    "value": "睡眠比之前更差，夜醒3次",
                    "evidence_text": "这几天睡得更差了，一晚上醒三次",
                    "confidence": 0.95,
                },
                "id": "outcome-neg-1",
            },
            _case_candidate([item.id for item in messages]),
        ]
    )

    result = await run_archivist(
        db_session,
        service_case=case,
        progress=progress,
        chat_model=model,
    )
    await db_session.flush()

    # 负向照样进账本（这是本次改动的目的），但方向为正的条数是 0
    assert result.outcome_written == 1
    assert result.positive_outcome_written == 0
    assert result.case_written == 0
    drafts = list(
        await db_session.scalars(
            select(KnowledgeDraft).where(KnowledgeDraft.draft_type == "case")
        )
    )
    assert [
        draft
        for draft in drafts
        if (draft.content_json or {}).get("service_case_id") == case.id
    ] == []
