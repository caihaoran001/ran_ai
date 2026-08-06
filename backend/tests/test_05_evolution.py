from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_legacy_knowledge_evolution_settings_are_removed(admin_client):
    get_response = await admin_client.get("/settings/knowledge-evolution")
    post_response = await admin_client.post(
        "/settings/knowledge-evolution",
        json={"enabled": True},
    )

    assert get_response.status_code == 404
    assert post_response.status_code == 404


@pytest.mark.asyncio
async def test_knowledge_drafts_list(admin_client):
    response = await admin_client.get("/knowledge-drafts", params={"status": "pending"})

    assert response.status_code == 200
    assert isinstance(response.json(), list)


@pytest.mark.asyncio
async def test_knowledge_drafts_list_supports_type_filter_and_paging(admin_client, db_session):
    """已入库案例有数百条，后台按类型分页取；不分页会一次回传数百 KB 拖慢审核控制台。"""
    from app.models.knowledge_draft import KnowledgeDraft

    for index in range(3):
        db_session.add(
            KnowledgeDraft(
                draft_type="case",
                status="approved",
                title=f"案例{index}",
                question="平台期怎么坚持？",
                answer="连续记录饮食并保持晚间散步。",
                source_message_ids=[index],
                content_json={"service_case_id": 1},
                content_hash=f"paging-case-{index}",
            )
        )
    db_session.add(
        KnowledgeDraft(
            draft_type="qa",
            status="approved",
            title="不该被案例分页取到",
            question="晚A什么时候喝？",
            answer="睡前一到两小时。",
            source_message_ids=[99],
            content_json={},
            content_hash="paging-qa",
        )
    )
    await db_session.flush()

    count = await admin_client.get(
        "/knowledge-drafts/count",
        params={"status": "approved", "draft_type": "case"},
    )
    first_page = await admin_client.get(
        "/knowledge-drafts",
        params={"status": "approved", "draft_type": "case", "limit": 2, "offset": 0},
    )
    second_page = await admin_client.get(
        "/knowledge-drafts",
        params={"status": "approved", "draft_type": "case", "limit": 2, "offset": 2},
    )

    assert count.status_code == 200
    assert count.json()["total"] == 3
    assert len(first_page.json()) == 2
    assert len(second_page.json()) == 1
    returned_ids = {item["id"] for item in first_page.json() + second_page.json()}
    assert len(returned_ids) == 3
    assert all(item["draft_type"] == "case" for item in first_page.json() + second_page.json())
