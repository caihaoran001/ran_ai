from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import TypedDict

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from app.services.agent.checkpoint import (
    ainvoke_resumable,
    build_checkpoint_thread_id,
    checkpoint_config,
    checkpoint_database_url,
)
from app.services.agent import graph as agent_graph_module
from app.services.channels.base import ChannelMessage
from app.services.ranren_ai.engine_graph import EngineGraph


def test_checkpoint_database_url_uses_psycopg_driver() -> None:
    assert (
        checkpoint_database_url(
            "postgresql+asyncpg://ran_ai:secret@rds.internal:5432/ran_ai"
        )
        == "postgresql://ran_ai:secret@rds.internal:5432/ran_ai"
    )
    assert (
        checkpoint_database_url(
            "postgresql+psycopg://ran_ai:secret@rds.internal:5432/ran_ai"
        )
        == "postgresql://ran_ai:secret@rds.internal:5432/ran_ai"
    )
    with pytest.raises(ValueError, match="requires a PostgreSQL"):
        checkpoint_database_url("sqlite+aiosqlite:///test.db")


def test_checkpoint_thread_id_is_stable_and_does_not_expose_chat_id() -> None:
    first = build_checkpoint_thread_id(
        workflow="reply",
        channel="qiwe",
        chat_id="真实群聊ID",
        message_id="callback-123",
        fallback_key="ignored-a",
    )
    second = build_checkpoint_thread_id(
        workflow="reply",
        channel="qiwe",
        chat_id="真实群聊ID",
        message_id="callback-123",
        fallback_key="ignored-b",
    )

    assert first == second
    assert first.startswith("ran-ai:reply:")
    assert "真实群聊ID" not in first
    assert "callback-123" not in first


def test_agent_graph_keeps_checkpointed_and_plain_variants_resident() -> None:
    saver = InMemorySaver()
    agent_graph_module.stop_agent_graph_runtime()
    try:
        agent_graph_module.start_agent_graph_runtime(saver)
        checkpointed = agent_graph_module._get_agent_compiled_graph(saver)
        agent_graph_module.start_agent_graph_runtime()
        plain = agent_graph_module._get_agent_compiled_graph(None)

        assert checkpointed is not plain
        assert agent_graph_module._get_agent_compiled_graph(saver) is checkpointed
        assert agent_graph_module._get_agent_compiled_graph(None) is plain
    finally:
        agent_graph_module.stop_agent_graph_runtime()


@pytest.mark.asyncio
async def test_resumable_invoke_continues_from_failed_node_without_replaying_upstream() -> None:
    class State(TypedDict, total=False):
        value: int

    calls: list[str] = []
    fragile_attempts = 0

    async def upstream(state: State) -> dict:
        calls.append("upstream")
        return {"value": int(state.get("value") or 0) + 1}

    async def fragile(state: State) -> dict:
        nonlocal fragile_attempts
        fragile_attempts += 1
        calls.append(f"fragile-{fragile_attempts}")
        if fragile_attempts == 1:
            raise RuntimeError("interrupted")
        return {"value": int(state["value"]) + 1}

    saver = InMemorySaver()
    builder = StateGraph(State)
    builder.add_node("upstream", upstream)
    builder.add_node("fragile", fragile)
    builder.add_edge(START, "upstream")
    builder.add_edge("upstream", "fragile")
    builder.add_edge("fragile", END)
    graph = builder.compile(checkpointer=saver)
    config = checkpoint_config("ran-ai:reply:test-resume")

    with pytest.raises(RuntimeError, match="interrupted"):
        await ainvoke_resumable(
            graph,
            {"value": 0},
            config=config,
            checkpointer=saver,
            delete_thread_on_success=True,
        )

    snapshot = await graph.aget_state(config)
    assert snapshot.next == ("fragile",)

    result = await ainvoke_resumable(
        graph,
        {"value": 999},
        config=config,
        checkpointer=saver,
        delete_thread_on_success=True,
    )

    assert result["value"] == 2
    assert calls == ["upstream", "fragile-1", "fragile-2"]
    assert await saver.aget_tuple(config) is None


@pytest.mark.asyncio
async def test_engine_graph_reuses_compiled_topology_and_isolates_concurrent_handlers(
    monkeypatch,
) -> None:
    arrivals: set[str] = set()
    both_arrived = asyncio.Event()
    observed: dict[str, str] = {}

    async def observe_reply(self, state):
        label = state["message"].text
        arrivals.add(label)
        if len(arrivals) == 2:
            both_arrived.set()
        await asyncio.wait_for(both_arrived.wait(), timeout=1)
        observed[label] = self.engine.identity
        return {"_halt": True}

    monkeypatch.setattr(EngineGraph, "_reply_prep", observe_reply)

    first = EngineGraph(SimpleNamespace(identity="case-a"))
    second = EngineGraph(SimpleNamespace(identity="case-b"))
    assert first.graph is second.graph

    def state(label: str) -> dict:
        return {
            "entry_mode": "reply_only",
            "started_at": 0.0,
            "message": ChannelMessage(
                channel="qiwe",
                chat_id=f"group-{label}",
                chat_type="group",
                text=label,
                message_id=f"message-{label}",
                timestamp=datetime.now(timezone.utc),
            ),
        }

    await asyncio.gather(
        first.run(state("message-a")),
        second.run(state("message-b")),
    )

    assert observed == {
        "message-a": "case-a",
        "message-b": "case-b",
    }
