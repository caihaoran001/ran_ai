from __future__ import annotations

import secrets
from datetime import datetime, timezone, timedelta

import pytest
from sqlalchemy import select

from app.models.knowledge_draft import KnowledgeDraft
from app.models.service_case import ServiceCase, ServiceCaseStatus
from tests.conftest import seed_chat_messages, seed_checkins


@pytest.mark.asyncio
async def test_knowledge_evolution_config_defaults(admin_client):
    resp = await admin_client.get("/settings/knowledge-evolution")
    assert resp.status_code == 200
    data = resp.json()
    assert "enabled" in data
    assert "interval_seconds" in data
    assert "qa_at" in data
    assert "case_days" in data
    assert isinstance(data["enabled"], bool)
    assert isinstance(data["interval_seconds"], int)
    assert isinstance(data["case_days"], str)


@pytest.mark.asyncio
async def test_pure_extraction_with_seed_data(admin_client, create_case):
    suffix = secrets.token_hex(6)
    resp = await create_case(
        {
            "group_id": f"test-evo-group-{suffix}",
            "user": {"name": "进化测试用户", "phone": f"138{suffix[:8]}"},
        },
    )
    assert resp.status_code == 201
    case = resp.json()

    resp = await admin_client.get(
        f"/service-cases/{case['id']}/messages",
    )
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)

    assert callable(seed_chat_messages)


@pytest.mark.asyncio
async def test_knowledge_drafts_list(admin_client):
    resp = await admin_client.get("/knowledge-drafts", params={"status": "pending"})
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_evolution_generates_drafts_from_seeded_chat(
    admin_client,
    create_case,
    db_session,
    mock_ai,
):
    suffix = secrets.token_hex(6)
    group_id = f"test-evo-gen-{suffix}"
    resp = await create_case(
        {"group_id": group_id, "user": {"name": "进化生成测试"}},
    )
    assert resp.status_code == 201
    case = resp.json()

    await seed_chat_messages(db_session, case["id"], group_id)
    await seed_checkins(db_session, case["id"], group_id, case["user_id"], days=28)

    sc = await db_session.get(ServiceCase, case["id"])
    sc.status = ServiceCaseStatus.IN_SERVICE
    sc.started_at = datetime.now(timezone.utc) - timedelta(days=28)
    await db_session.flush()

    await admin_client.post("/settings/knowledge-evolution", json={"enabled": True})

    from sqlalchemy import update
    await db_session.execute(
        update(ServiceCase)
        .where(ServiceCase.id != case["id"])
        .values(status=ServiceCaseStatus.CREATED)
    )
    await db_session.flush()
    existing_draft_ids = set(await db_session.scalars(select(KnowledgeDraft.id)))

    from app.services.knowledge_evolution import KnowledgeEvolutionService
    service = KnowledgeEvolutionService(ai=mock_ai)
    qa_count, _case_scan = await service.scan_messages(
        db_session, hours=720, limit=200,
    )

    assert qa_count > 0, f"种子数据应产生 FAQ 草稿。qa={qa_count}"

    drafts = list(await db_session.scalars(
        select(KnowledgeDraft).where(
            KnowledgeDraft.id.not_in(existing_draft_ids),
            KnowledgeDraft.title.isnot(None),
        )
    ))
    assert len(drafts) > 0, "扫描后应能查到落库的草稿"
    for d in drafts[:3]:
        assert d.draft_type in ("qa", "case"), f"草稿类型异常: {d.draft_type}"
        assert d.question or d.title, "草稿应有问题或标题"
    qa_drafts = [d for d in drafts if d.draft_type == "qa"]
    assert len(qa_drafts) > 0, (
        f"应产生QA草稿。当前: qa={len(qa_drafts)}"
    )
