from __future__ import annotations

import secrets

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import func, select

from app.models.agent_run import AgentRun
from app.models.agent_tool_call import AgentToolCall
from app.models.ai_usage import AIUsageLog
from app.services.agent_observability import (
    COMPLETED,
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
    response = await create_case(
        {
            "group_id": f"agent-observability-{secrets.token_hex(6)}",
            "user": {"name": "Agent 统计测试"},
        }
    )
    assert response.status_code == 201, response.text
    service_case_id = response.json()["id"]

    reply_run_id = await ensure_agent_run(
        run_key=f"reply:test:{secrets.token_hex(8)}",
        agent_type="reply",
        run_kind="interactive",
        service_case_id=service_case_id,
        input_count=1,
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

    api_response = await admin_client.get("/agent-operations?days=30")
    assert api_response.status_code == 200, api_response.text
    payload = api_response.json()
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
