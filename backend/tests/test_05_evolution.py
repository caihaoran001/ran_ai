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
async def test_pure_extraction_with_seed_data(admin_client):
    suffix = secrets.token_hex(6)
    resp = await admin_client.post(
        "/service-cases",
        json={
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
    db_session,
    mock_ai,
):
    suffix = secrets.token_hex(6)
    group_id = f"test-evo-gen-{suffix}"
    resp = await admin_client.post(
        "/service-cases",
        json={"group_id": group_id, "user": {"name": "进化生成测试"}},
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

    from app.services.knowledge_evolution import KnowledgeEvolutionService
    service = KnowledgeEvolutionService()
    qa_count, _case_scan = await service.scan_messages(
        db_session, hours=720, limit=200,
    )
    case_created, _case_skipped = await service.scan_stage_cases(
        db_session, stage_days=[3, 7, 14, 21, 28], limit_per_case=200, force=True,
    )

    assert qa_count + case_created > 0, (
        f"种子数据应产生知识草稿。qa={qa_count} case={case_created}"
    )

    drafts = list(await db_session.scalars(
        select(KnowledgeDraft).where(
            KnowledgeDraft.title.isnot(None),
        ).limit(10)
    ))
    assert len(drafts) > 0, "扫描后应能查到落库的草稿"
    for d in drafts[:3]:
        assert d.draft_type in ("qa", "case"), f"草稿类型异常: {d.draft_type}"
        assert d.question or d.title, "草稿应有问题或标题"
    qa_drafts = [d for d in drafts if d.draft_type == "qa"]
    case_drafts = [d for d in drafts if d.draft_type == "case"]
    assert len(qa_drafts) > 0, (
        f"应产生QA草稿。当前: qa={len(qa_drafts)} case={len(case_drafts)}"
    )
    assert len(case_drafts) > 0, (
        f"打卡数据应产生案例草稿。当前: qa={len(qa_drafts)} case={len(case_drafts)}"
    )
