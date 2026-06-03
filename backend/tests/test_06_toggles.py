from __future__ import annotations

import secrets

import pytest
from sqlalchemy import select

from app.models.service_case import ServiceCase
from app.services.channels.base import ChannelMessage
from app.services.content_policy import set_ai_reply_delivery_mode
from app.services.ranren_ai.engine import RanrenAIEngine


@pytest.mark.asyncio
async def test_content_delivery_policy_crud(admin_client):
    resp = await admin_client.get("/settings/content-delivery-policy")
    assert resp.status_code == 200
    data = resp.json()
    assert "mode" in data
    assert data["mode"] in ("manual_review", "auto_send")

    resp = await admin_client.post(
        "/settings/content-delivery-policy",
        json={"mode": "auto_send"},
    )
    assert resp.status_code == 200
    assert resp.json()["mode"] == "auto_send"

    resp = await admin_client.get("/settings/content-delivery-policy")
    assert resp.status_code == 200
    assert resp.json()["mode"] == "auto_send"


@pytest.mark.asyncio
async def test_ai_reply_policy_crud(admin_client):
    resp = await admin_client.get("/settings/ai-reply-delivery-policy")
    assert resp.status_code == 200
    data = resp.json()
    assert "mode" in data
    assert data["mode"] in ("manual_review", "auto_send")


@pytest.mark.asyncio
async def test_service_case_auto_send_toggle(admin_client):
    group_id = f"test-toggle-auto-{secrets.token_hex(6)}"
    resp = await admin_client.post("/service-cases", json={
        "group_id": group_id,
        "user": {"name": "AutoSend测试"},
    })
    assert resp.status_code == 201, resp.text
    case = resp.json()
    case_id = case["id"]

    resp = await admin_client.patch(
        f"/service-cases/{case_id}",
        json={"auto_send_documents": False},
    )
    assert resp.status_code == 200
    assert resp.json()["auto_send_documents"] is False

    resp = await admin_client.get(f"/service-cases/{case_id}")
    assert resp.status_code == 200
    assert resp.json()["auto_send_documents"] is False

    resp = await admin_client.patch(
        f"/service-cases/{case_id}",
        json={"auto_send_documents": True},
    )
    assert resp.status_code == 200
    assert resp.json()["auto_send_documents"] is True


@pytest.mark.asyncio
async def test_service_case_ai_reply_toggle(admin_client):
    group_id = f"test-toggle-ai-reply-{secrets.token_hex(6)}"
    resp = await admin_client.post("/service-cases", json={
        "group_id": group_id,
        "user": {"name": "AIReply测试"},
    })
    assert resp.status_code == 201, resp.text
    case_id = resp.json()["id"]

    resp = await admin_client.patch(
        f"/service-cases/{case_id}",
        json={"ai_auto_reply_enabled": False},
    )
    assert resp.status_code == 200
    assert resp.json()["ai_auto_reply_enabled"] is False

    resp = await admin_client.get(f"/service-cases/{case_id}")
    assert resp.status_code == 200
    assert resp.json()["ai_auto_reply_enabled"] is False


@pytest.mark.asyncio
async def test_qiwe_ai_reply_respects_global_manual_review_policy(admin_client, db_session):
    group_id = f"test-qiwe-ai-policy-{secrets.token_hex(6)}"
    resp = await admin_client.post("/service-cases", json={
        "group_id": group_id,
        "user": {"name": "QiWe审核策略测试"},
    })
    assert resp.status_code == 201, resp.text
    case_id = resp.json()["id"]
    service_case = await db_session.scalar(select(ServiceCase).where(ServiceCase.id == case_id))
    assert service_case is not None

    engine = RanrenAIEngine(channel=None)
    message = ChannelMessage(
        channel="qiwe",
        chat_id=group_id,
        chat_type="group",
        text="今天状态不错",
        message_id="qiwe-policy-1",
        user_id="user-1",
    )

    await set_ai_reply_delivery_mode("manual_review", db_session)
    try:
        should_send = await engine._should_send_ai_reply(db_session, service_case, {}, message)
        assert should_send is False

        await set_ai_reply_delivery_mode("auto_send", db_session)
        should_send = await engine._should_send_ai_reply(db_session, service_case, {}, message)
        assert should_send is True
    finally:
        await set_ai_reply_delivery_mode("manual_review", db_session)


@pytest.mark.asyncio
async def test_knowledge_evolution_settings(admin_client):
    resp = await admin_client.get("/settings/knowledge-evolution")
    assert resp.status_code == 200
    data = resp.json()
    assert "enabled" in data

    resp = await admin_client.post(
        "/settings/knowledge-evolution",
        json={"enabled": True},
    )
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True

    resp = await admin_client.get("/settings/knowledge-evolution")
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True


@pytest.mark.asyncio
async def test_content_delivery_policy_auto_send_behaviour(
    admin_client,
    mock_ai,
    cai_haoran_answers,
    wait_background,
):
    """设置 auto_send 策略后，文档生成后自动发送（status=SENT）"""
    from tests.test_01_intake import INTAKE_SYSTEM_PROMPT_PREFIX, _make_intake_mock_response

    mock_ai._responses = {
        INTAKE_SYSTEM_PROMPT_PREFIX: _make_intake_mock_response(),
    }

    await admin_client.post("/settings/content-delivery-policy", json={"mode": "auto_send"})
    try:
        group_id = f"test-autosend-policy-{secrets.token_hex(6)}"
        resp = await admin_client.post("/service-cases", json={
            "group_id": group_id,
            "user": {"name": "策略自动发送测试"},
        })
        assert resp.status_code == 201, resp.text
        sc = resp.json()
        token = sc["questionnaire_token"]
        case_id = sc["id"]

        resp = await admin_client.post(
            f"/questionnaires/{token}/submit?template=intake",
            json={"answers": cai_haoran_answers},
        )
        assert resp.status_code == 201, resp.text
        await wait_background()

        resp = await admin_client.get(f"/service-cases/{case_id}/documents")
        docs = resp.json()
        assert any(d["status"] == "sent" for d in docs), (
            f"auto_send策略开启后，文档应自动发送。当前状态: {[d['status'] for d in docs]}"
        )
    finally:
        await admin_client.post("/settings/content-delivery-policy", json={"mode": "manual_review"})


@pytest.mark.asyncio
async def test_soft_delete_service_case(admin_client, db_session):
    group_id = f"test-soft-delete-{secrets.token_hex(6)}"
    resp = await admin_client.post("/service-cases", json={
        "group_id": group_id,
        "user": {"name": "软删除测试"},
    })
    assert resp.status_code == 201, resp.text
    case_id = resp.json()["id"]

    resp = await admin_client.delete(f"/service-cases/{case_id}")
    assert resp.status_code == 200
    deleted = resp.json()
    assert deleted["deleted_at"] is not None

    resp = await admin_client.get(f"/service-cases/{case_id}")
    assert resp.status_code == 200
    assert resp.json()["deleted_at"] is not None

    found = await db_session.scalar(
        select(ServiceCase).where(ServiceCase.id == case_id)
    )
    assert found is not None
    assert found.deleted_at is not None
