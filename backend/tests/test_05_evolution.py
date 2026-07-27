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
