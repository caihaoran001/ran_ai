from __future__ import annotations

import secrets

import pytest
from sqlalchemy import select

from app.api.routes import diagnostics as diagnostics_mod
from app.models.ai_usage import AIUsageLog
from app.models.service_case import ServiceCase
from app.services.channels.base import ChannelMessage
from app.services.content_policy import set_ai_reply_delivery_mode
from app.services.ranren_ai.engine import RanrenAIEngine, _requires_fixed_safety_reply


@pytest.mark.asyncio
async def test_dashboard_cost_excludes_diagnostic_usage(admin_client, db_session):
    before = (await admin_client.get("/dashboard/summary")).json()["ai_cost_summary"]
    db_session.add_all(
        [
            AIUsageLog(
                scenario="ai_reply",
                model="test-model",
                total_tokens=100,
                prompt_tokens=60,
                completion_tokens=40,
                estimated_cost_yuan=0.1,
                success=True,
                is_diagnostic=False,
            ),
            AIUsageLog(
                scenario="diagnostics_e2e_quality_judge",
                model="test-model",
                total_tokens=900,
                prompt_tokens=500,
                completion_tokens=400,
                estimated_cost_yuan=0.9,
                success=True,
                is_diagnostic=True,
                diagnostic_run_id="test-run",
            ),
        ]
    )
    await db_session.flush()

    response = await admin_client.get("/dashboard/summary")
    assert response.status_code == 200, response.text
    summary = response.json()["ai_cost_summary"]
    assert summary["total_calls"] == before["total_calls"] + 1
    assert summary["total_tokens"] == before["total_tokens"] + 100


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


def test_current_physical_risk_uses_fixed_safety_reply():
    service_case = ServiceCase(
        case_no="TEST-SAFETY-REPLY",
        group_id="test-safety-reply",
        questionnaire_token="test-safety-reply-token",
        risk_level="medium",
        needs_human_intervention=True,
        risk_flags=[{"code": "palpitation", "level": "medium"}],
    )

    assert _requires_fixed_safety_reply(service_case, {"risk_flags": []}) is False
    assert _requires_fixed_safety_reply(
        service_case,
        {"risk_flags": [{"code": "palpitation", "level": "medium"}]},
    ) is True
    assert _requires_fixed_safety_reply(
        service_case,
        {
            "requires_immediate_safety_reply": False,
            "risk_flags": [{"code": "palpitation", "level": "medium"}],
        },
    ) is False
    assert _requires_fixed_safety_reply(
        None,
        {"requires_immediate_safety_reply": True, "risk_flags": []},
    ) is True


def test_diagnostics_suites_keep_real_e2e_last():
    ids = [suite["id"] for suite in diagnostics_mod.TEST_SUITES]

    assert ids[:8] == ["01", "02", "03", "04", "05", "06", "07", "08"]
    assert ids[-1] == "e2e_01"


def test_diagnostics_extracts_e2e_progress_from_console_logs():
    progress = diagnostics_mod._extract_e2e_progress(
        {
            "e2e_01": [
                "🧪 [E2E-01] 启动人设旅程: 林晴 / sunny_cooperator，消息 12 条，打卡 7 天",
                "   [E2E-01] 林晴 推进到 Day 7: messages=1, checkin=yes",
                "   [内容质量综合评审] 林晴: dialogue_quality={'understanding': 5}",
                "tests/test_e2e_01_real_llm.py::test_real_llm_persona_journey[sunny_cooperator] PASSED",
            ]
        }
    )

    assert progress["total"] == 10
    assert progress["passed"] == 1
    assert progress["failed"] == 0
    assert progress["current_day"] == 7
    assert progress["completed"][0]["key"] == "sunny_cooperator"
    assert progress["completed"][0]["name"] == "林晴"
    assert progress["completed"][0]["scores_text"] == "dialogue_quality={'understanding': 5}"


def test_diagnostics_stale_health_run_marks_all_running_failed(monkeypatch):
    monkeypatch.setattr(diagnostics_mod.time, "time", lambda: 10_000)
    history = {
        "active_run": {"mode": "health", "suite_id": "03", "started_at": 1},
        "statuses": {"01": "running", "02": "passed", "03": "running", "04": "running"},
        "console_logs": {},
    }

    changed = diagnostics_mod._clear_stale_active_run(history)

    assert changed is True
    assert history["active_run"] is None
    assert history["statuses"] == {"01": "failed", "02": "passed", "03": "failed", "04": "failed"}
    assert any("已自动重置 active_run" in line for line in history["console_logs"]["all"])


@pytest.mark.asyncio
async def test_diagnostics_reset_active_run_preserves_history(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics_mod, "_backend_dir", lambda: tmp_path)
    diagnostics_mod._save_history(
        {
            "active_run": {"mode": "real_e2e", "suite_id": "e2e_01", "started_at": 10},
            "statuses": {"e2e_01": "running", "01": "passed"},
            "console_logs": {"e2e_01": ["old log"]},
        }
    )

    result = await diagnostics_mod.reset_active_run()
    history = diagnostics_mod._load_history()

    assert result == {"status": "reset"}
    assert history["active_run"] is None
    assert history["statuses"]["e2e_01"] == "failed"
    assert history["statuses"]["01"] == "passed"
    assert history["console_logs"]["e2e_01"][0] == "old log"


def test_fixed_safety_reply_distinguishes_health_risk_from_service_handoff():
    service_case = ServiceCase(
        case_no="TEST-SERVICE-HANDOFF",
        group_id="test-service-handoff",
        questionnaire_token="test-service-handoff-token",
        risk_level="normal",
        needs_human_intervention=True,
        risk_flags=None,
        refund_intent_flags=[{"detected": True, "evidence": "我要退款"}],
    )

    assert _requires_fixed_safety_reply(service_case, {"risk_flags": []}) is False
    assert _requires_fixed_safety_reply(
        None,
        {"risk_flags": [{"code": "medical_boundary", "level": "high"}]},
    ) is True

    service_case.risk_level = "high"
    assert _requires_fixed_safety_reply(service_case, {"risk_flags": []}) is False


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
