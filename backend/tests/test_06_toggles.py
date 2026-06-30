from __future__ import annotations

import secrets
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.api.routes import diagnostics as diagnostics_mod
from app.models.ai_usage import AIUsageLog
from app.models.checkin import Checkin
from app.models.message import Message, MessageDirection, MessagePurpose, MessageSenderType, MessageStatus
from app.models.service_case import ServiceCase
from app.models.user_document import DocumentStatus, DocumentType, UserDocument
from app.services.channels.base import ChannelMessage
from app.services.content_policy import set_ai_reply_delivery_mode
from app.services.ranren_ai.engine import RanrenAIEngine, _requires_immediate_handoff


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
async def test_dashboard_emotional_alerts_show_trigger_messages(admin_client, db_session):
    china_tz = ZoneInfo("Asia/Shanghai")
    today = datetime.now(china_tz).date()
    today_start = datetime.combine(today, time.min, tzinfo=china_tz).astimezone(timezone.utc)

    create_resp = await admin_client.post(
        "/service-cases",
        json={"group_id": f"emotion-alert-{secrets.token_hex(6)}", "user": {"name": "情绪预警用户"}},
    )
    assert create_resp.status_code == 201, create_resp.text
    case = create_resp.json()
    case_id = case["id"]
    group_id = case["group_id"]

    db_session.add_all(
        [
            Message(
                service_case_id=case_id,
                group_id=group_id,
                direction=MessageDirection.INBOUND,
                sender_type=MessageSenderType.USER,
                message_type="text",
                content="今天真的很挫败，感觉快撑不住了",
                purpose=MessagePurpose.CHAT,
                status=MessageStatus.RECEIVED,
                analysis_json={
                    "emotion_signal": {
                        "detected": True,
                        "level": "high",
                        "reason": "明显负面情绪和执行挫败",
                        "evidence": "快撑不住了",
                    }
                },
                created_at=today_start + timedelta(hours=10),
            ),
            Message(
                service_case_id=case_id,
                group_id=group_id,
                direction=MessageDirection.INBOUND,
                sender_type=MessageSenderType.USER,
                message_type="text",
                content="今天正常打卡",
                purpose=MessagePurpose.CHAT,
                status=MessageStatus.RECEIVED,
                analysis_json={"emotion_signal": {"detected": False, "level": "low"}},
                created_at=today_start + timedelta(hours=11),
            ),
        ]
    )
    await db_session.flush()

    summary_resp = await admin_client.get("/dashboard/summary")
    assert summary_resp.status_code == 200, summary_resp.text
    assert summary_resp.json()["emotional_users_today"] >= 1

    detail_resp = await admin_client.get("/dashboard/emotional-alerts")
    assert detail_resp.status_code == 200, detail_resp.text
    cases = [item for item in detail_resp.json() if item["service_case_id"] == case_id]
    assert len(cases) == 1
    assert cases[0]["case_no"] == case["case_no"]
    assert cases[0]["user_name"] == "情绪预警用户"
    assert len(cases[0]["messages"]) == 1
    message = cases[0]["messages"][0]
    assert message["content"] == "今天真的很挫败，感觉快撑不住了"
    assert message["level"] == "high"
    assert message["reason"] == "明显负面情绪和执行挫败"
    assert message["evidence"] == "快撑不住了"


@pytest.mark.asyncio
async def test_dashboard_drilldown_endpoints_show_case_and_trigger_content(admin_client, db_session):
    create_resp = await admin_client.post(
        "/service-cases",
        json={"group_id": f"dashboard-drilldown-{secrets.token_hex(6)}", "user": {"name": "看板明细用户"}},
    )
    assert create_resp.status_code == 201, create_resp.text
    created_case = create_resp.json()
    case = await db_session.get(ServiceCase, created_case["id"])
    assert case is not None

    now = datetime.now(timezone.utc)
    trigger = Message(
        service_case_id=case.id,
        group_id=case.group_id,
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.USER,
        message_type="text",
        content="能不能@班主任，我不想听机器人解释了",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
        analysis_json={
            "intervention_reasons": [
                {
                    "code": "robot_stop",
                    "level": "medium",
                    "reason": "用户要求人工介入并停止机器人解释",
                    "evidence": "@班主任",
                }
            ]
        },
        created_at=now,
    )
    renewal = Message(
        service_case_id=case.id,
        group_id=case.group_id,
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.USER,
        message_type="text",
        content="这期结束我想续一期，怎么买？",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
        analysis_json={
            "renewal_intent": {
                "detected": True,
                "reason": "明确表达续一期并询问购买",
                "evidence": "想续一期，怎么买",
            }
        },
        created_at=now + timedelta(seconds=1),
    )
    refund = Message(
        service_case_id=case.id,
        group_id=case.group_id,
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.USER,
        message_type="text",
        content="我想退出服务，帮我退款吧",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
        analysis_json={
            "refund_intent": {
                "detected": True,
                "reason": "明确表达退出和退款",
                "evidence": "退出服务，帮我退款",
            }
        },
        created_at=now + timedelta(seconds=2),
    )
    pending_reply = Message(
        service_case_id=case.id,
        group_id=case.group_id,
        direction=MessageDirection.OUTBOUND,
        sender_type=MessageSenderType.BOT,
        message_type="text",
        content="我先帮你转给班主任确认。",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.PENDING,
        trigger=trigger,
        created_at=now + timedelta(seconds=3),
    )
    db_session.add_all([trigger, renewal, refund, pending_reply])
    await db_session.flush()

    case.renewal_intent_flags = [{"detected": True, "reason": "明确表达续一期并询问购买", "evidence": "想续一期，怎么买"}]
    case.refund_intent_flags = [{"detected": True, "reason": "明确表达退出和退款", "evidence": "退出服务，帮我退款"}]
    db_session.add(
        Checkin(
            service_case_id=case.id,
            group_id=case.group_id,
            user_id=case.user_id,
            checkin_date=date.today(),
            content="今日打卡：早C完成，饮水达标",
            parsed_json={"water": "达标", "product_schedule": {"morning_c": "完成"}},
        )
    )
    db_session.add(
        UserDocument(
            service_case_id=case.id,
            document_type=DocumentType.ENERGY_RESTART_PRESCRIPTION,
            title="个人能量重启处方单",
            content_md="待审核",
            content_json={"blocks": []},
            status=DocumentStatus.REVIEWING,
        )
    )
    await db_session.flush()

    attention_resp = await admin_client.get("/dashboard/attention-events")
    assert attention_resp.status_code == 200, attention_resp.text
    attention_case = next(item for item in attention_resp.json() if item["case"]["service_case_id"] == case.id)
    assert attention_case["case"]["user_name"] == "看板明细用户"
    assert attention_case["events"][0]["content"] == "能不能@班主任，我不想听机器人解释了"
    assert attention_case["events"][0]["ai_reply_content"] == "我先帮你转给班主任确认。"

    renewal_resp = await admin_client.get("/dashboard/intent-events/renewal")
    assert renewal_resp.status_code == 200, renewal_resp.text
    renewal_case = next(item for item in renewal_resp.json() if item["case"]["service_case_id"] == case.id)
    assert renewal_case["events"][0]["content"] == "这期结束我想续一期，怎么买？"

    refund_resp = await admin_client.get("/dashboard/intent-events/refund")
    assert refund_resp.status_code == 200, refund_resp.text
    refund_case = next(item for item in refund_resp.json() if item["case"]["service_case_id"] == case.id)
    assert refund_case["events"][0]["content"] == "我想退出服务，帮我退款吧"

    today_messages_resp = await admin_client.get("/dashboard/today-messages")
    assert today_messages_resp.status_code == 200, today_messages_resp.text
    message_case = next(item for item in today_messages_resp.json() if item["case"]["service_case_id"] == case.id)
    assert any(event["content"] == "这期结束我想续一期，怎么买？" for event in message_case["events"])

    checkins_resp = await admin_client.get("/dashboard/today-checkins")
    assert checkins_resp.status_code == 200, checkins_resp.text
    checkin_case = next(item for item in checkins_resp.json() if item["case"]["service_case_id"] == case.id)
    assert checkin_case["checkins"][0]["content"] == "今日打卡：早C完成，饮水达标"

    docs_resp = await admin_client.get("/dashboard/pending-documents")
    assert docs_resp.status_code == 200, docs_resp.text
    docs_case = next(item for item in docs_resp.json() if item["case"]["service_case_id"] == case.id)
    assert docs_case["documents"][0]["title"] == "个人能量重启处方单"


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
async def test_service_case_agent_toggle_defaults_off_and_updates(admin_client):
    group_id = f"test-toggle-agent-{secrets.token_hex(6)}"
    resp = await admin_client.post("/service-cases", json={
        "group_id": group_id,
        "user": {"name": "Agent开关测试"},
    })
    assert resp.status_code == 201, resp.text
    case_id = resp.json()["id"]
    assert resp.json()["ai_agent_enabled"] is False

    resp = await admin_client.patch(
        f"/service-cases/{case_id}",
        json={"ai_agent_enabled": True},
    )
    assert resp.status_code == 200
    assert resp.json()["ai_agent_enabled"] is True

    resp = await admin_client.get(f"/service-cases/{case_id}")
    assert resp.status_code == 200
    assert resp.json()["ai_agent_enabled"] is True


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


def test_acute_physical_risk_triggers_immediate_handoff():
    # 急性体感(心悸等)+需人工介入 → ①层直接@班主任(无论 requires_immediate_safety_reply)
    base = {"requires_human_intervention": True}
    assert _requires_immediate_handoff({**base, "risk_flags": []}) is False
    assert _requires_immediate_handoff(
        {**base, "risk_flags": [{"code": "palpitation", "level": "medium"}]},
    ) is True
    # 新设计:急性体感即使 safety_reply=false 也归①层(健康风险直接转人工)
    assert _requires_immediate_handoff(
        {**base, "requires_immediate_safety_reply": False, "risk_flags": [{"code": "palpitation", "level": "medium"}]},
    ) is True
    assert _requires_immediate_handoff(
        {**base, "requires_immediate_safety_reply": True, "risk_flags": []},
    ) is True
    # 不需人工介入 → 不@
    assert _requires_immediate_handoff(
        {"requires_human_intervention": False, "risk_flags": [{"code": "palpitation", "level": "medium"}]},
    ) is False


def test_diagnostics_suites_keep_real_e2e_last():
    ids = [suite["id"] for suite in diagnostics_mod.TEST_SUITES]

    assert ids == ["01", "02", "03", "04", "05", "06", "07", "08"]


def test_diagnostics_extracts_e2e_progress_from_console_logs():
    progress = diagnostics_mod._extract_e2e_progress(
        {
            "e2e_01": [
                "🧪 [E2E-01] 启动人设旅程: 林晴 / sunny_cooperator，消息 12 条，打卡 7 天",
                "   [E2E-01] 林晴 推进到 Day 7: messages=1, checkin=yes",
                "   [内容质量综合评审] 林晴: dialogue_quality={'understanding': 5}",
                " -> [28天用户旅程验证] 林晴: risk=normal, human=False, refund=False, renewal=True",
                "tests/test_e2e_01_real_llm.py::test_real_llm_persona_journey[sunny_cooperator] PASSED",
            ]
        }
    )

    assert progress["total"] == 10
    assert progress["passed"] == 1
    assert progress["failed"] == 0
    assert progress["reviewed"] == 0
    assert progress["current_day"] == 7
    assert progress["completed"][0]["key"] == "sunny_cooperator"
    assert progress["completed"][0]["name"] == "林晴"
    assert progress["completed"][0]["scores_text"] == "dialogue_quality={'understanding': 5}"
    assert progress["completed"][0]["summary_text"] == "risk=normal, human=False, refund=False, renewal=True"


def test_diagnostics_counts_reviewed_e2e_progress_before_pytest_status():
    progress = diagnostics_mod._extract_e2e_progress(
        {
            "e2e_01": [
                "🧪 [E2E-01] 启动人设旅程: 顾安 / medical_caution，消息 16 条，打卡 5 天",
                "   [E2E-01] 顾安 推进到 Day 28: messages=1, checkin=yes",
                "   [内容质量综合评审] 顾安: dialogue_quality={'understanding': 4}",
                " -> [28天用户旅程验证] 顾安: risk=normal, human=False, refund=False, renewal=False",
            ]
        }
    )

    assert progress["reviewed"] == 1
    assert progress["passed"] == 0
    assert progress["failed"] == 0
    assert progress["completed"][0]["status"] == "reviewed"
    assert progress["completed"][0]["name"] == "顾安"


def test_diagnostics_e2e_progress_ignores_unformatted_placeholders_and_reads_pytest_ids():
    progress = diagnostics_mod._extract_e2e_progress(
        {
            "e2e_01": [
                "🧪 [E2E-01] 启动人设旅程: {journey.user_name} / {journey.key}，消息 8 条，打卡 3 天",
                "tests/test_e2e_01_real_llm.py::test_real_llm_persona_journey[caffeine_sensitive] ",
                "   [E2E-01] 许敏 推进到 Day 7: messages=1, checkin=yes",
                "FAILED tests/test_e2e_01_real_llm.py::test_real_llm_persona_journey[caffeine_sensitive]",
            ]
        }
    )

    assert progress["failed"] == 1
    assert progress["current_day"] == 7
    assert progress["completed"][0]["key"] == "caffeine_sensitive"
    assert progress["completed"][0]["name"] == "许敏"


def test_diagnostics_e2e_progress_does_not_reopen_completed_persona_from_traceback():
    progress = diagnostics_mod._extract_e2e_progress(
        {
            "e2e_01": [
                "tests/test_e2e_01_real_llm.py::test_real_llm_persona_journey[sunny_cooperator] ",
                "   [内容质量综合评审] 林晴: dialogue_quality={'understanding': 5}",
                " -> [28天用户旅程验证] 林晴: risk=normal, human=False",
                "FAILED tests/test_e2e_01_real_llm.py::test_real_llm_persona_journey[refund_complaint]",
                "tests/test_e2e_01_real_llm.py::test_real_llm_persona_journey[sunny_cooperator]",
            ]
        }
    )

    assert progress["current_persona"] == ""
    assert progress["reviewed"] == 1
    assert progress["failed"] == 1
    assert len(progress["completed"]) == 2


def test_diagnostics_history_path_can_be_configured(tmp_path, monkeypatch):
    history_path = tmp_path / "diagnostics" / "history.json"
    monkeypatch.setenv("DIAGNOSTICS_HISTORY_PATH", str(history_path))

    diagnostics_mod._save_history(
        {
            "statuses": {"01": "passed"},
            "durations": {"01": 1.2},
            "last_run_time": 123,
            "console_logs": {"01": ["ok"]},
            "active_run": None,
        }
    )
    loaded = diagnostics_mod._load_history()

    assert history_path.exists()
    assert loaded["statuses"]["01"] == "passed"
    assert loaded["console_logs"]["01"] == ["ok"]


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


def test_high_risk_triggers_immediate_handoff():
    # 健康红线 level=high + 需人工介入 → ①层@班主任;只看本次 analysis,不看持久 risk_level
    base = {"requires_human_intervention": True}
    assert _requires_immediate_handoff({**base, "risk_flags": []}) is False
    assert _requires_immediate_handoff(
        {**base, "risk_flags": [{"code": "medical_boundary", "level": "high"}]},
    ) is True
    # 仅有持久高风险但本次 analysis 无风险信号 → 不强制@(避免每句都@)
    assert _requires_immediate_handoff({**base, "risk_flags": []}) is False


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
