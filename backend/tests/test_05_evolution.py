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
    from app.services.archivist.case_synthesis import maybe_synthesize_case
    from tests.conftest import seed_outcome_event
    from types import SimpleNamespace

    service = KnowledgeEvolutionService(ai=mock_ai)
    qa_count, _case_scan = await service.scan_messages(
        db_session, hours=720, limit=200,
    )
    # 优秀案例改由"成效稳态变化"驱动：先落一条正向成效事件，再触发档案官合成。
    await seed_outcome_event(db_session, case["id"], event_type="improved")
    await maybe_synthesize_case(
        service_case_id=case["id"],
        run_result=SimpleNamespace(outcome_written=1),
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
    case_drafts = [d for d in drafts if d.draft_type == "case"]
    assert len(qa_drafts) > 0, (
        f"应产生QA草稿。当前: qa={len(qa_drafts)} case={len(case_drafts)}"
    )
    assert len(case_drafts) > 0, (
        f"打卡数据应产生案例草稿。当前: qa={len(qa_drafts)} case={len(case_drafts)}"
    )


@pytest.mark.asyncio
async def test_outcome_driven_case_synthesis_uses_full_history_and_existing_case_context(
    admin_client,
    create_case,
    db_session,
    mock_ai,
):
    """优秀案例改由成效稳态变化驱动：合成时喂全历史 + 成效时间线，并带上既有案例摘要去重。"""
    from types import SimpleNamespace
    from app.services.archivist.case_synthesis import maybe_synthesize_case
    from tests.conftest import seed_outcome_event

    suffix = secrets.token_hex(6)
    group_id = f"test-evo-history-{suffix}"
    resp = await create_case(
        {"group_id": group_id, "user": {"name": "案例上下文测试"}},
    )
    assert resp.status_code == 201
    case = resp.json()

    await seed_chat_messages(db_session, case["id"], group_id)
    sc = await db_session.get(ServiceCase, case["id"])
    sc.status = ServiceCaseStatus.IN_SERVICE
    sc.started_at = datetime.now(timezone.utc) - timedelta(days=28)
    await db_session.flush()

    existing = KnowledgeDraft(
        draft_type="case",
        status="approved",
        title="已沉淀旧案例",
        question="旧案例背景",
        answer="旧案例结果",
        category="坚持执行",
        evidence="旧证据",
        content_hash=f"existing-{suffix}",
        content_json={"service_case_id": case["id"], "stage": "day3"},
    )
    db_session.add(existing)
    await seed_outcome_event(db_session, case["id"], event_type="improved")
    await db_session.flush()

    await maybe_synthesize_case(
        service_case_id=case["id"],
        run_result=SimpleNamespace(outcome_written=1),
    )

    drafts = list(
        await db_session.scalars(
            select(KnowledgeDraft)
            .where(
                KnowledgeDraft.draft_type == "case",
                KnowledgeDraft.status == "pending",
            )
            .order_by(KnowledgeDraft.id.desc())
        )
    )

    latest = next(
        (
            draft
            for draft in drafts
            if isinstance(draft.content_json, dict)
            and draft.content_json.get("service_case_id") == case["id"]
            and draft.content_json.get("scan_type") == "archivist_outcome_stable"
        ),
        None,
    )
    assert latest is not None
    assert latest.content_json.get("service_case_id") == case["id"]
    # 成效时间线被喂进合成上下文
    assert latest.content_json.get("outcome_timeline"), "应把成效时间线传给合成"
    existing_summaries = latest.content_json.get("existing_case_summaries") or []
    assert existing_summaries, "应把既有案例摘要传给合成以去重"
    assert existing_summaries[0]["title"] == "已沉淀旧案例"
