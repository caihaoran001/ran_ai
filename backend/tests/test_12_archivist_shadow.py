"""档案官成效/画像收编——直写真表健康自测（决策 A：档案官独家抽取）。

验证：档案官 extract 用 Agent 形态一次性抽打卡+成效+画像；commit 里打卡照常 live upsert、
成效/画像经 apply_archived_outcome **直接写真表**——outcome_events(source=archivist、
message_id=anchor、当前里程碑)、user_state_profiles 出现对应 domain、outcome_stage_states
更新；影子对账已删除，archivist_shadow_outcomes 不再写入。无可上报内容时零写入。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import select

from app.models.archivist_progress import ArchivistProgress
from app.models.archivist_shadow_outcome import ArchivistShadowOutcome
from app.models.checkin import Checkin
from app.models.message import (
    Message,
    MessageDirection,
    MessagePurpose,
    MessageSenderType,
    MessageStatus,
)
from app.models.outcome import OutcomeEvent, OutcomeStageState
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user import User
from app.models.user_state_profile import UserStateProfile
from app.services.archivist.graph import run_archivist


class FakeToolCallingModel:
    """注入档案官 extract Agent 的假模型：bind_tools 返回自身，ainvoke 顺序吐预设 AIMessage。"""

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


async def _setup_case_with_messages(db_session):
    user = User(name="档案官直写测试用户")
    db_session.add(user)
    await db_session.flush()
    case = ServiceCase(
        case_no=f"TEST-ARCHV-{uuid4().hex[:6].upper()}",
        user_id=user.id,
        group_id=f"archv-{uuid4().hex[:8]}",
        questionnaire_token=uuid4().hex,
        status=ServiceCaseStatus.IN_SERVICE,
        started_at=datetime.now(timezone.utc) - timedelta(days=6),  # service_day 7 → 里程碑 day7
    )
    db_session.add(case)
    await db_session.flush()

    msgs = []
    for content in ("今天早C喝了，体重168斤", "这周瘦了2斤，睡眠也变好了"):
        m = Message(
            service_case_id=case.id,
            group_id=case.group_id,
            direction=MessageDirection.INBOUND,
            sender_type=MessageSenderType.USER,
            message_type="text",
            content=content,
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
async def test_archivist_writes_outcome_and_profile_to_real_tables(db_session):
    case, msgs, progress = await _setup_case_with_messages(db_session)

    # 假模型：两条消息同一自然日 → 一次 ainvoke，emit 打卡+成效+画像 report。
    fake = FakeToolCallingModel([
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "report_checkin",
                    "args": {"weight": "168斤", "product_schedule": {"morning_c": "已喝"}},
                    "id": "c1",
                },
                {
                    "name": "report_outcome",  # day7 白名单内
                    "args": {"field_key": "weight_change", "event_type": "improved", "value": "瘦了2斤", "evidence_text": "这周瘦了2斤", "confidence": 0.9},
                    "id": "o1",
                },
                {
                    "name": "report_outcome",  # day7 白名单内
                    "args": {"field_key": "sleep_change", "event_type": "improved", "value": "睡眠变好", "evidence_text": "睡眠也变好了", "confidence": 0.8},
                    "id": "o2",
                },
                {
                    "name": "report_outcome",  # 非 day7 白名单字段 → 被 _normalize_events 丢弃
                    "args": {"field_key": "mood_change", "event_type": "improved", "value": "心情好", "evidence_text": "变好了", "confidence": 0.9},
                    "id": "o3",
                },
                {
                    "name": "report_profile_fact",  # confidence>=0.65 才落
                    "args": {"domain": "lifestyle_habits", "signal_type": "sleep_habit", "value": "作息在改善", "evidence_text": "睡眠也变好了", "confidence": 0.7},
                    "id": "p1",
                },
            ],
        )
    ])

    result = await run_archivist(db_session, service_case=case, progress=progress, chat_model=fake)
    await db_session.flush()

    # 1) 打卡照常 live upsert（Agent report_checkin 落库）。
    checkin = await db_session.scalar(
        select(Checkin).where(Checkin.service_case_id == case.id, Checkin.deleted_at.is_(None))
    )
    assert checkin is not None
    assert (checkin.parsed_json or {}).get("weight") == "168斤"

    # 2) 成效直写真表：两条合法事件落库，source=archivist、message_id=anchor(最后一条)、里程碑 day7；
    #    非白名单 mood_change 被丢弃。
    events = list(await db_session.scalars(
        select(OutcomeEvent).where(OutcomeEvent.service_case_id == case.id)
    ))
    assert {e.field_key for e in events} == {"weight_change", "sleep_change"}
    assert all(e.source == "archivist" for e in events)
    assert all(e.milestone == "day7" for e in events)
    assert all(e.message_id == msgs[-1].id for e in events)
    assert result.outcome_written == 2

    # 3) 画像投影：user_state_profiles 出现成效 domain（weight_change→outcome_changes）+ 画像信号 domain。
    profile = await db_session.scalar(
        select(UserStateProfile).where(UserStateProfile.service_case_id == case.id)
    )
    assert profile is not None
    domains = profile.state_json.get("domains", {})
    assert domains.get("outcome_changes", {}).get("current")
    assert domains.get("lifestyle_habits", {}).get("current")
    assert result.profile_written == 1

    # 4) 阶段状态收集字段更新。
    state = await db_session.scalar(
        select(OutcomeStageState).where(
            OutcomeStageState.service_case_id == case.id,
            OutcomeStageState.milestone == "day7",
        )
    )
    assert state is not None
    assert "weight_change" in (state.collected_fields or {})

    # 5) 影子对账已删除：archivist_shadow_outcomes 不再写入。
    shadow = await db_session.scalar(
        select(ArchivistShadowOutcome).where(ArchivistShadowOutcome.service_case_id == case.id)
    )
    assert shadow is None


@pytest.mark.asyncio
async def test_archivist_no_reportable_writes_nothing(db_session):
    case, msgs, progress = await _setup_case_with_messages(db_session)

    # 假模型不 emit 任何工具调用（无可上报内容）。
    fake = FakeToolCallingModel([AIMessage(content="", tool_calls=[])])
    result = await run_archivist(db_session, service_case=case, progress=progress, chat_model=fake)
    await db_session.flush()

    assert result.outcome_written == 0
    assert result.profile_written == 0
    events = list(await db_session.scalars(
        select(OutcomeEvent).where(OutcomeEvent.service_case_id == case.id)
    ))
    assert events == []
    shadow = await db_session.scalar(
        select(ArchivistShadowOutcome).where(ArchivistShadowOutcome.service_case_id == case.id)
    )
    assert shadow is None
    # 水位线仍推进（避免下轮重复处理）。
    assert progress.last_archived_message_id == msgs[-1].id
