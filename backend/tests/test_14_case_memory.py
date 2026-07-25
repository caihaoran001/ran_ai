"""档案官滚动小结健康自测。

验证：
- 用户历程小结与优秀案例素材小结由同一轮档案官输出并同事务写入；
- 下一轮只携带历史结构化事实和两类小结，不重复携带旧聊天原文；
- load_profile_memory_context 能把 case_memory 注入 Chat Agent 上下文(历程小结)。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4
import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import select

from app.models.archivist_case_memory import ArchivistCaseMemory
from app.models.archivist_progress import ArchivistProgress
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
from app.services.profile_memory_service import _memory_from_state, load_profile_memory_context


class _FakeArchivist:
    def __init__(self, response: AIMessage):
        self.response = response
        self.calls: list[list] = []
        self.bound_tools = []

    def bind_tools(self, tools):
        self.bound_tools = list(tools)
        return self

    async def ainvoke(self, messages):
        self.calls.append(list(messages))
        return self.response


async def _setup(db_session):
    user = User(name="记忆测试用户")
    db_session.add(user)
    await db_session.flush()
    case = ServiceCase(
        case_no=f"TEST-MEM-{uuid4().hex[:6].upper()}",
        user_id=user.id,
        group_id=f"mem-{uuid4().hex[:8]}",
        questionnaire_token=uuid4().hex,
        status=ServiceCaseStatus.IN_SERVICE,
        started_at=datetime.now(timezone.utc) - timedelta(days=6),
    )
    db_session.add(case)
    await db_session.flush()

    msgs = []
    for content, sender in (
        ("这周瘦了2斤，睡眠也变好了", MessageSenderType.USER),
        ("太棒了，继续保持！", MessageSenderType.BOT),
        ("今天早C喝了，体重168斤", MessageSenderType.USER),
    ):
        m = Message(
            service_case_id=case.id,
            group_id=case.group_id,
            direction=MessageDirection.INBOUND
            if sender == MessageSenderType.USER
            else MessageDirection.OUTBOUND,
            sender_type=sender,
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
async def test_same_archivist_call_writes_both_memory_summaries(db_session):
    case, msgs, progress = await _setup(db_session)
    watermark = msgs[-1].id
    fake = _FakeArchivist(
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "report_memory",
                    "args": {
                        "user_summary": "本周体重下降2斤，睡眠改善，执行稳定。",
                        "case_material_summary": (
                            "起点为睡眠不稳；持续执行早C并完成打卡，"
                            "目前体重下降2斤且睡眠改善，仍需后续记录确认稳定性。"
                        ),
                    },
                    "id": "memory-1",
                }
            ],
        )
    )

    result = await run_archivist(
        db_session,
        service_case=case,
        progress=progress,
        chat_model=fake,
    )
    await db_session.flush()

    memory = await db_session.scalar(
        select(ArchivistCaseMemory).where(
            ArchivistCaseMemory.service_case_id == case.id
        )
    )
    assert memory is not None
    assert len(fake.calls) == 1
    assert result.memory_written == 1
    assert memory.summary and "睡眠改善" in memory.summary
    assert memory.case_material_summary and "仍需后续记录" in memory.case_material_summary
    assert memory.summary_watermark_message_id == watermark
    assert {"report_memory", "report_case_candidate"} <= {
        tool.name for tool in fake.bound_tools
    }

    # 回复 Agent 只拿用户历程小结，不拿优秀案例素材小结。
    ctx = await load_profile_memory_context(db_session, service_case_id=case.id)
    rendered = ctx.render_for_agent()
    assert "历程小结" in rendered
    assert "仍需后续记录" not in rendered


@pytest.mark.asyncio
async def test_next_batch_uses_abstracted_history_without_old_raw_messages(db_session):
    case, msgs, progress = await _setup(db_session)
    old_raw = "这是绝不能再次发送给模型的旧聊天原文"
    msgs[-1].content = old_raw
    progress.last_archived_message_id = msgs[-1].id
    db_session.add(
        ArchivistCaseMemory(
            service_case_id=case.id,
            summary="用户此前执行逐渐稳定。",
            case_material_summary="起点执行不稳，目前已出现阶段改善。",
            summary_watermark_message_id=msgs[-1].id,
        )
    )
    db_session.add(
        OutcomeEvent(
            service_case_id=case.id,
            message_id=msgs[0].id,
            milestone="day7",
            field_key="weight_change",
            event_type="improved",
            value_text="体重累计下降2斤",
            evidence_text="原始成效原话不得进入下一轮提示词",
            confidence=0.9,
            source="archivist",
            occurred_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
    )
    current = Message(
        service_case_id=case.id,
        group_id=case.group_id,
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.USER,
        message_type="text",
        content="今天继续完成了早C。",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
    )
    db_session.add(current)
    await db_session.flush()
    fake = _FakeArchivist(AIMessage(content="", tool_calls=[]))

    await run_archivist(
        db_session,
        service_case=case,
        progress=progress,
        chat_model=fake,
    )
    prompt = fake.calls[0][-1].content
    assert "用户此前执行逐渐稳定" in prompt
    assert "起点执行不稳，目前已出现阶段改善" in prompt
    assert "体重累计下降2斤" in prompt
    assert str(msgs[0].id) in prompt
    assert "今天继续完成了早C" in prompt
    assert old_raw not in prompt
    assert "原始成效原话不得进入下一轮提示词" not in prompt


def test_profile_memory_reads_multi_facts_and_hides_resolved_state() -> None:
    preferences, _habits, current_state, _warnings = _memory_from_state(
        {
            "domains": {
                "personal_preferences": {
                    "current": {
                        "value": {
                            "food_preference": [
                                {"value": "不吃香菜", "status": "confirmed"},
                                {"value": "不喜欢芹菜", "status": "confirmed"},
                            ]
                        }
                    }
                },
                "service_state": {
                    "current": {
                        "value": {
                            "current_obstacle": {"value": "已解决的卡点", "status": "resolved"},
                            "execution_status": {"value": "晚 A 仍容易漏掉", "status": "active"},
                        }
                    }
                },
            }
        }
    )

    assert preferences == ["个人偏好：不吃香菜、不喜欢芹菜"]
    assert current_state == ["当前服务状态：晚 A 仍容易漏掉"]


def test_profile_memory_recovers_legacy_overwritten_preference_from_history() -> None:
    preferences, _habits, _current_state, _warnings = _memory_from_state(
        {
            "domains": {
                "personal_preferences": {
                    "current": {
                        "value": {
                            "food_preference": {
                                "value": "吃甜食身体不舒服",
                                "status": "confirmed",
                            }
                        },
                        "updated_at": "2026-07-17T07:32:26+00:00",
                    },
                    "history": [
                        {
                            "field_key": "food_preference",
                            "value": "不喜欢吃豆腐",
                            "status": "confirmed",
                            "occurred_at": "2026-07-17T07:31:00+00:00",
                        },
                        {
                            "field_key": "food_preference",
                            "value": "吃甜食身体不舒服",
                            "status": "confirmed",
                            "occurred_at": "2026-07-17T07:32:00+00:00",
                        },
                    ],
                }
            }
        }
    )

    assert preferences == ["个人偏好：不喜欢吃豆腐、吃甜食身体不舒服"]
