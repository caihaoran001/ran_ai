"""P0.5 档案官成效/画像收编——影子对账健康自测。

验证：影子开时档案官走 Agent 形态额外抽成效/画像，**只对账不落库**——
打卡照常 live upsert、outcome_events/user_state 零新增、archivist_shadow_outcomes 落行且 diff 正确；
影子关时行为回到 P0（纯打卡抽取、不建影子表、不调 agent）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import select

from app.core.config import get_settings
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
from app.models.outcome import OutcomeEvent
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user import User
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
    user = User(name="影子对账测试用户")
    db_session.add(user)
    await db_session.flush()
    case = ServiceCase(
        case_no=f"TEST-SHADOW-{uuid4().hex[:6].upper()}",
        user_id=user.id,
        group_id=f"shadow-{uuid4().hex[:8]}",
        questionnaire_token=uuid4().hex,
        status=ServiceCaseStatus.IN_SERVICE,
        started_at=datetime.now(timezone.utc) - timedelta(days=6),
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

    # 进度行：水位线置 0，两条消息都算增量。
    progress = ArchivistProgress(service_case_id=case.id, last_archived_message_id=0)
    db_session.add(progress)
    await db_session.flush()
    return case, msgs, progress


def _hot_event(case_id, message_id, field_key, event_type):
    return OutcomeEvent(
        service_case_id=case_id,
        message_id=message_id,
        milestone="day7",
        field_key=field_key,
        event_type=event_type,
        value_text="x",
        evidence_text="用户原话",
        confidence=0.9,
        source="chat",
    )


@pytest.mark.asyncio
async def test_shadow_on_reconciles_without_writing_domain_tables(db_session, monkeypatch):
    monkeypatch.setattr(get_settings(), "archivist_outcome_shadow_enabled", True)
    case, msgs, progress = await _setup_case_with_messages(db_session)

    # 热路径已写 3 条成效事件（逐条写入的基准）。
    db_session.add_all([
        _hot_event(case.id, msgs[0].id, "weight_change", "improved"),
        _hot_event(case.id, msgs[1].id, "sleep_change", "improved"),
        _hot_event(case.id, msgs[1].id, "energy_change", "improved"),  # 影子不报 → hot_only
    ])
    await db_session.flush()

    # 假模型：一天一次 ainvoke（两条消息同一自然日 → 一次）。
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
                    "name": "report_outcome",
                    "args": {"field_key": "weight_change", "event_type": "improved", "value": "瘦了2斤", "evidence_text": "这周瘦了2斤", "confidence": 0.9},
                    "id": "o1",
                },
                {
                    "name": "report_outcome",
                    "args": {"field_key": "sleep_change", "event_type": "improved", "value": "睡眠变好", "evidence_text": "睡眠也变好了", "confidence": 0.9},
                    "id": "o2",
                },
                {
                    "name": "report_outcome",  # 影子多报 → shadow_only
                    "args": {"field_key": "mood_change", "event_type": "improved", "value": "心情好", "evidence_text": "变好了", "confidence": 0.5},
                    "id": "o3",
                },
                {
                    "name": "report_profile_fact",
                    "args": {"domain": "lifestyle_habits", "signal_type": "sleep_habit", "value": "作息在改善", "evidence_text": "睡眠也变好了", "confidence": 0.6},
                    "id": "p1",
                },
            ],
        )
    ])

    result = await run_archivist(
        db_session, service_case=case, progress=progress, chat_model=fake
    )
    await db_session.flush()

    # 1) 打卡照常 live upsert（agent 形态的 report_checkin 落库）。
    checkin = await db_session.scalar(
        select(Checkin).where(Checkin.service_case_id == case.id, Checkin.deleted_at.is_(None))
    )
    assert checkin is not None
    assert (checkin.parsed_json or {}).get("weight") == "168斤"

    # 2) 影子绝不写领域表：outcome_events 仍是热路径那 3 条，档案官没新增。
    events = list(await db_session.scalars(
        select(OutcomeEvent).where(OutcomeEvent.service_case_id == case.id)
    ))
    assert len(events) == 3

    # 3) 影子表落行，diff 计数正确：matched=2 / hot_only=1 / shadow_only=1。
    shadow = await db_session.scalar(
        select(ArchivistShadowOutcome).where(ArchivistShadowOutcome.service_case_id == case.id)
    )
    assert shadow is not None
    assert shadow.diff["counts"] == {"matched": 2, "hot_only": 1, "shadow_only": 1}
    assert len(shadow.shadow_signals) == 1
    assert result.shadow_recorded is True
    assert result.shadow_diff["counts"]["matched"] == 2


@pytest.mark.asyncio
async def test_shadow_off_keeps_p0_behavior(db_session, mock_ai, monkeypatch):
    monkeypatch.setattr(get_settings(), "archivist_outcome_shadow_enabled", False)
    case, msgs, progress = await _setup_case_with_messages(db_session)

    # 影子关：不注入 chat_model，走 P0 纯打卡抽取（mock_ai）。
    result = await run_archivist(db_session, service_case=case, progress=progress)
    await db_session.flush()

    # 不建影子表、不做对账。
    shadow = await db_session.scalar(
        select(ArchivistShadowOutcome).where(ArchivistShadowOutcome.service_case_id == case.id)
    )
    assert shadow is None
    assert result.shadow_recorded is False
