from __future__ import annotations

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
