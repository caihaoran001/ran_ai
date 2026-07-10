"""档案官 case_memory 健康自测：滚动小结 + 近期话题 + Chat Agent 记忆注入。

验证：
- 有新沉淀时,refresh_case_memory 用(注入的)模型刷新 summary,并确定性维护 recent_topics;
- 无新沉淀时,summary 不刷(保持原值),recent_topics 仍更新;
- load_profile_memory_context 能把 case_memory 注入 Chat Agent 上下文(历程小结/最近话题)。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import select

from app.models.archivist_case_memory import ArchivistCaseMemory
from app.models.message import (
    Message,
    MessageDirection,
    MessagePurpose,
    MessageSenderType,
    MessageStatus,
)
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user import User
from app.services.archivist.case_memory_service import refresh_case_memory
from app.services.profile_memory_service import load_profile_memory_context


class _FakeSummarizer:
    def __init__(self, text: str):
        self.text = text
        self.calls = 0

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        self.calls += 1
        return AIMessage(content=self.text)


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
    return case, msgs


@pytest.mark.asyncio
async def test_case_memory_refresh_with_precipitation(db_session):
    case, msgs = await _setup(db_session)
    watermark = msgs[-1].id
    fake = _FakeSummarizer("这位用户本周体重下降2斤，睡眠改善，情绪稳定，执行力较强。")
    run_result = SimpleNamespace(
        outcome_written=1,
        profile_written=0,
        personal_corrections_written=0,
        qa_drafts_created=0,
        upserts=[{"filled": ["weight"]}],
    )

    await refresh_case_memory(
        service_case_id=case.id,
        run_result=run_result,
        watermark_to=watermark,
        chat_model=fake,
    )

    memory = await db_session.scalar(
        select(ArchivistCaseMemory).where(
            ArchivistCaseMemory.service_case_id == case.id
        )
    )
    assert memory is not None
    assert fake.calls == 1  # 有新沉淀 → 触发一次摘要 LLM
    assert memory.summary and "睡眠改善" in memory.summary
    assert memory.summary_watermark_message_id == watermark
    # 近期话题确定性维护：包含最近的用户发言短摘要（最新在前）。
    assert memory.recent_topics
    assert any("168斤" in t for t in memory.recent_topics)

    # 注入 Chat Agent：历程小结 + 最近话题出现在渲染结果里。
    ctx = await load_profile_memory_context(db_session, service_case_id=case.id)
    rendered = ctx.render_for_agent(max_lines=10)
    assert "历程小结" in rendered
    assert "最近话题" in rendered


@pytest.mark.asyncio
async def test_case_memory_no_precipitation_skips_summary(db_session):
    case, msgs = await _setup(db_session)
    watermark = msgs[-1].id
    fake = _FakeSummarizer("不应被调用")
    run_result = SimpleNamespace(
        outcome_written=0,
        profile_written=0,
        personal_corrections_written=0,
        qa_drafts_created=0,
        upserts=[],
    )

    await refresh_case_memory(
        service_case_id=case.id,
        run_result=run_result,
        watermark_to=watermark,
        chat_model=fake,
    )

    memory = await db_session.scalar(
        select(ArchivistCaseMemory).where(
            ArchivistCaseMemory.service_case_id == case.id
        )
    )
    assert memory is not None
    assert fake.calls == 0  # 无新沉淀 → 不烧摘要 LLM
    assert memory.summary is None
    assert memory.recent_topics  # 近期话题仍更新
