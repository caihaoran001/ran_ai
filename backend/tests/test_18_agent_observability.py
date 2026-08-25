from __future__ import annotations

import secrets
from datetime import datetime, timezone

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import func, select

from app.models.agent_run import AgentRun
from app.models.agent_tool_call import AgentToolCall
from app.models.ai_usage import AIUsageLog
from app.models.message import (
    Message,
    MessageDirection,
    MessagePurpose,
    MessageSenderType,
    MessageStatus,
)
from app.services.agent_observability import (
    COMPLETED,
    FAILED,
    complete_agent_run,
    ensure_agent_run,
    record_model_usage,
    record_tool_calls,
)


@pytest.mark.asyncio
async def test_agent_ledger_tracks_model_tools_and_api(
    admin_client,
    create_case,
    db_session,
):
    group_id = f"agent-observability-{secrets.token_hex(6)}"
    response = await create_case(
        {
            "group_id": group_id,
            "user": {"name": "Agent 统计测试"},
        }
    )
    assert response.status_code == 201, response.text
    service_case_id = response.json()["id"]

    inbound = Message(
        service_case_id=service_case_id,
        group_id=group_id,
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.USER,
        message_type="text",
        content="是的，没有",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
    )
    db_session.add(inbound)
    await db_session.flush()
    await db_session.commit()

    reply_run_id = await ensure_agent_run(
        run_key=f"reply:test:{secrets.token_hex(8)}",
        agent_type="reply",
        run_kind="interactive",
        service_case_id=service_case_id,
        trigger_message_id=inbound.id,
        input_count=1,
        metadata={
            "knowledge": {
                "decision": "searched",
                "query": "午B超过时间能补吗",
                "hit_count": 1,
                "hits": [
                    {
                        "rank": 1,
                        "chunk_id": 42,
                        "chunk_uid": "faq:legacy",
                        "title": "旧历史命中",
                        "source_type": "faq",
                        "updated_at": None,
                        "content_excerpt": "旧历史没有四项观测字段。",
                    }
                ],
            }
        },
    )
    assert reply_run_id is not None
    model_response = AIMessage(
        content="收到",
        usage_metadata={
            "input_tokens": 120,
            "output_tokens": 30,
            "total_tokens": 150,
        },
        response_metadata={
            "model_name": "deepseek-test",
            "finish_reason": "stop",
        },
    )
    await record_model_usage(
        agent_run_id=reply_run_id,
        scenario="reply_agent",
        service_case_id=service_case_id,
        message_id=None,
        model="deepseek-test",
        elapsed_ms=125,
        response=model_response,
        success=True,
    )
    tool_traces = [
        {
            "tool_call_id": "tool-success",
            "name": "get_service_progress",
            "args": {"service_case_id": service_case_id},
            "ok": True,
            "elapsed_ms": 20,
        },
        {
            "tool_call_id": "tool-failed",
            "name": "get_weight_trend",
            "args": {},
            "ok": False,
            "error": "TimeoutError: slow",
            "elapsed_ms": 50,
        },
        {
            "tool_call_id": "tool-rejected",
            "name": "web_search_lifestyle",
            "args": {"query": "test"},
            "ok": False,
            "error": "TOOL_REJECTED: policy",
            "elapsed_ms": 0,
        },
    ]
    await record_tool_calls(agent_run_id=reply_run_id, traces=tool_traces)
    # LangGraph checkpoint replay may revisit the same tool node; IDs keep counts stable.
    await record_tool_calls(agent_run_id=reply_run_id, traces=tool_traces)
    await complete_agent_run(
        agent_run_id=reply_run_id,
        status=COMPLETED,
        outcome="reply_sent",
        latency_ms=420,
        output_count=1,
    )
    outbound = Message(
        service_case_id=service_case_id,
        group_id=group_id,
        direction=MessageDirection.OUTBOUND,
        sender_type=MessageSenderType.BOT,
        message_type="markdown",
        content="没有心慌就很好。继续半份观察满3天。",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.SENT,
        trigger_id=inbound.id,
        sent_at=datetime.now(timezone.utc),
    )
    db_session.add(outbound)
    await db_session.commit()

    archivist_run_id = await ensure_agent_run(
        run_key=f"archivist:test:{secrets.token_hex(8)}",
        agent_type="archivist",
        run_kind="scheduled_scan",
        service_case_id=service_case_id,
        trigger_reason="fast_key",
        input_count=4,
    )
    await complete_agent_run(
        agent_run_id=archivist_run_id,
        status=COMPLETED,
        outcome="archived_with_output",
        latency_ms=800,
        output_count=3,
        dropped_count=1,
        metadata={
            "output_breakdown": {
                "outcomes": 1,
                "profile_facts": 1,
                "memory_updates": 1,
            },
            "input_message_ids": [],
            "changes": [
                {
                    "kind": "user_summary",
                    "action": "updated",
                    "title": "用户历程小结",
                    "before": "此前执行不稳定",
                    "after": "本周已连续完成计划",
                }
            ],
        },
    )

    run = await db_session.get(AgentRun, reply_run_id)
    assert run is not None
    assert run.model_calls == 1
    assert run.total_tokens == 150
    assert run.tool_calls == 3
    assert run.successful_tool_calls == 1
    assert run.status == COMPLETED
    assert run.outcome == "reply_sent"
    assert (
        await db_session.scalar(
            select(func.count(AgentToolCall.id)).where(
                AgentToolCall.agent_run_id == reply_run_id
            )
        )
        == 3
    )
    usage = await db_session.scalar(
        select(AIUsageLog).where(AIUsageLog.agent_run_id == reply_run_id)
    )
    assert usage is not None
    assert usage.total_tokens == 150

    api_response = await admin_client.get("/agent-operations")
    assert api_response.status_code == 200, api_response.text
    payload = api_response.json()
    assert payload["days"] == 1
    assert payload["reply"]["runs"] >= 1
    assert any(
        item["key"] == "reply_sent" and item["count"] >= 1
        for item in payload["reply"]["outcomes"]
    )
    assert payload["reply"]["tool_calls"] >= 3
    assert payload["archivist"]["input_count"] >= 4
    assert payload["archivist"]["output_breakdown"]["outcomes"] >= 1
    recent_reply = next(
        item for item in payload["recent_runs"] if item["id"] == reply_run_id
    )
    assert recent_reply["prompt_tokens"] == 120
    assert recent_reply["completion_tokens"] == 30
    detail_response = await admin_client.get(
        f"/agent-operations/runs/{reply_run_id}"
    )
    assert detail_response.status_code == 200, detail_response.text
    detail = detail_response.json()
    assert detail["input_message"]["id"] == inbound.id
    assert detail["input_message"]["content"] == "是的，没有"
    assert detail["input_message"]["role"] == "用户"
    assert detail["output_message"]["id"] == outbound.id
    assert detail["output_message"]["content"] == "没有心慌就很好。继续半份观察满3天。"
    assert detail["output_message"]["role"] == "AI 助教"
    assert detail["output_message"]["status"] == "sent"
    knowledge = detail["knowledge"]
    assert knowledge is not None
    assert knowledge["decision"] == "searched"
    assert len(knowledge["hits"]) == 1
    legacy_hit = knowledge["hits"][0]
    assert legacy_hit["chunk_id"] == 42
    assert legacy_hit["vector_rank"] is None
    assert legacy_hit["vector_distance"] is None
    assert legacy_hit["keyword_rank"] is None
    assert legacy_hit["confirmation_state"] is None

    failed_inbound = Message(
        service_case_id=service_case_id,
        group_id=group_id,
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.USER,
        message_type="text",
        content="这条为什么没有回复？",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
        analysis_json={
            "agent_reply_failure": {
                "stage": "generation",
                "error_type": "TimeoutError",
                "timeout_seconds": 90.0,
                "elapsed_ms": 90123,
                "retryable": True,
                "failed_at": datetime.now(timezone.utc).isoformat(),
            },
            "_reply_processing": {
                "status": "retryable_failure",
                "reason": "agent_generation_failed",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        },
    )
    db_session.add(failed_inbound)
    await db_session.flush()
    await db_session.commit()
    failed_run_id = await ensure_agent_run(
        run_key=f"reply:failed:{secrets.token_hex(8)}",
        agent_type="reply",
        run_kind="interactive",
        service_case_id=service_case_id,
        trigger_message_id=failed_inbound.id,
        input_count=1,
    )
    await complete_agent_run(
        agent_run_id=failed_run_id,
        status=FAILED,
        outcome="agent_generation_failed",
        latency_ms=90123,
        error_type="TimeoutError",
    )

    outcome_response = await admin_client.get(
        "/agent-operations/runs",
        params={
            "outcome": "agent_generation_failed",
            "limit": 20,
            "offset": 0,
        },
    )
    assert outcome_response.status_code == 200, outcome_response.text
    outcome_page = outcome_response.json()
    assert outcome_page["total"] >= 1
    failed_detail = next(
        item for item in outcome_page["items"] if item["id"] == failed_run_id
    )
    assert failed_detail["case_no"]
    assert failed_detail["input_message"]["content"] == "这条为什么没有回复？"
    assert failed_detail["output_message"] is None
    assert failed_detail["failure"] == {
        "stage": "generation",
        "error_type": "TimeoutError",
        "summary": "回复生成超过 90 秒，系统停止了本次生成",
        "retryable": True,
        "timeout_seconds": 90.0,
        "elapsed_ms": 90123,
        "failed_at": failed_detail["failure"]["failed_at"],
    }
    empty_outcome_response = await admin_client.get(
        "/agent-operations/runs",
        params={"days": 30, "outcome": "test_missing_outcome"},
    )
    assert empty_outcome_response.status_code == 200
    assert empty_outcome_response.json()["items"] == []
    batch = next(
        item
        for item in payload["archivist_batches"]
        if item["id"] == archivist_run_id
    )
    assert batch["input_source"] == "exact"
    assert batch["changes_detail_available"] is True
    assert batch["changes"][0]["kind"] == "user_summary"
    assert batch["changes"][0]["after"] == "本周已连续完成计划"
    tool = next(
        item
        for item in payload["tools"]
        if item["name"] == "get_service_progress"
    )
    assert tool["calls"] >= 1
    assert tool["success_rate"] == 100.0
    assert any(
        item["scenario"] == "reply_agent"
        for item in payload["costs"]["scenarios"]
    )
    assert payload["costs"]["service_case_count"] >= 1
    case_cost = next(
        item
        for item in payload["costs"]["cases"]
        if item["service_case_id"] == service_case_id
    )
    assert case_cost["scenarios"][0]["scenario"] == "reply_agent"


@pytest.mark.asyncio
async def test_stale_running_runs_are_swept_to_interrupted(
    admin_client,
    create_case,
    db_session,
):
    """进程被杀后停在"运行中"的行必须被收尾成"进程中断"终态。

    台账不参与业务判断，续跑是新的一次尝试、新记一行，没有任何在线路径会回头收尾旧行；
    缺了这个扫描，"运行中"会变成永久假状态，真卡死时被历史噪音掩盖。
    """
    from datetime import timedelta

    from app.services.agent_observability import (
        INTERRUPTED,
        INTERRUPTED_OUTCOME,
        INTERRUPTED_STALE_AFTER_SECONDS,
        RUNNING,
        sweep_interrupted_agent_runs,
    )

    group_id = f"agent-sweep-{secrets.token_hex(6)}"
    response = await create_case(
        {"group_id": group_id, "user": {"name": "中断收尾测试"}}
    )
    assert response.status_code == 201, response.text
    service_case_id = response.json()["id"]

    now = datetime.now(timezone.utc)
    stale_id = await ensure_agent_run(
        run_key=f"sweep-stale-{secrets.token_hex(8)}",
        agent_type="archivist",
        run_kind="scheduled_scan",
        service_case_id=service_case_id,
    )
    fresh_id = await ensure_agent_run(
        run_key=f"sweep-fresh-{secrets.token_hex(8)}",
        agent_type="reply",
        run_kind="interactive",
        service_case_id=service_case_id,
    )
    assert stale_id is not None and fresh_id is not None

    # 复刻生产形态：一条早已被杀、一条刚开跑。花费按真实入账过一笔，扫描不得改动它。
    stale = await db_session.get(AgentRun, stale_id)
    stale.started_at = now - timedelta(seconds=INTERRUPTED_STALE_AFTER_SECONDS + 60)
    stale.created_at = stale.started_at
    stale.model_calls = 1
    stale.estimated_cost_yuan = 0.022239
    await db_session.commit()

    swept = await sweep_interrupted_agent_runs()
    assert swept >= 1

    await db_session.refresh(stale)
    assert stale.status == INTERRUPTED
    assert stale.outcome == INTERRUPTED_OUTCOME
    assert stale.error_type == "ProcessInterrupted"
    # 已入账的花费/调用数是准确值，不回填、不清零。
    assert stale.model_calls == 1
    assert stale.estimated_cost_yuan == pytest.approx(0.022239)
    # 真实结束时刻不可知，不得编造耗时。
    assert stale.finished_at is None
    assert stale.latency_ms is None

    fresh = await db_session.get(AgentRun, fresh_id)
    await db_session.refresh(fresh)
    assert fresh.status == RUNNING, "刚开跑的运行不能被误判成中断"

    # 已终态的行不被扫描碰到。
    await complete_agent_run(
        agent_run_id=fresh_id,
        status=COMPLETED,
        outcome="reply_sent",
        latency_ms=1234,
    )
    await db_session.refresh(fresh)
    fresh.started_at = now - timedelta(seconds=INTERRUPTED_STALE_AFTER_SECONDS + 60)
    await db_session.commit()
    await sweep_interrupted_agent_runs()
    await db_session.refresh(fresh)
    assert fresh.status == COMPLETED
    assert fresh.outcome == "reply_sent"
    assert fresh.latency_ms == 1234
