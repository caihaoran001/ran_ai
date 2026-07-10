"""优秀案例成效稳态驱动的触发门控自测。

重点验证用户在意的点：没有成效信号 / 没有正向进展的单**不会触发**案例合成，
只有出现正向成效事件时才合成——安静或无进展的单不烧钱、也不产垃圾案例。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.knowledge_draft import KnowledgeDraft
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user import User
from app.services.archivist.case_synthesis import maybe_synthesize_case
from tests.conftest import seed_chat_messages, seed_outcome_event


async def _case(db_session):
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
    await seed_chat_messages(db_session, case.id, case.group_id)
    return case


async def _case_draft_count(db_session, service_case_id: int) -> int:
    drafts = list(
        await db_session.scalars(
            select(KnowledgeDraft).where(KnowledgeDraft.draft_type == "case")
        )
    )
    return sum(
        1
        for d in drafts
        if isinstance(d.content_json, dict)
        and d.content_json.get("service_case_id") == service_case_id
    )


@pytest.mark.asyncio
async def test_no_outcome_written_skips_synthesis(db_session, mock_ai):
    case = await _case(db_session)
    await seed_outcome_event(db_session, case.id, event_type="improved")
    # 本轮没写入新成效（outcome_written=0）→ 不评估、不合成。
    await maybe_synthesize_case(
        service_case_id=case.id, run_result=SimpleNamespace(outcome_written=0)
    )
    assert await _case_draft_count(db_session, case.id) == 0


@pytest.mark.asyncio
async def test_no_positive_outcome_skips_synthesis(db_session, mock_ai):
    case = await _case(db_session)
    # 有成效事件但全是负向/中性 → 没有可展示的成功故事，不合成。
    await seed_outcome_event(db_session, case.id, event_type="worsened")
    await maybe_synthesize_case(
        service_case_id=case.id, run_result=SimpleNamespace(outcome_written=1)
    )
    assert await _case_draft_count(db_session, case.id) == 0


@pytest.mark.asyncio
async def test_positive_stable_outcome_triggers_synthesis(db_session, mock_ai):
    case = await _case(db_session)
    await seed_outcome_event(db_session, case.id, event_type="improved")
    await maybe_synthesize_case(
        service_case_id=case.id, run_result=SimpleNamespace(outcome_written=1)
    )
    assert await _case_draft_count(db_session, case.id) >= 1
