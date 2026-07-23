from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.core.config import Settings, get_settings

logger = logging.getLogger("uvicorn.error")

_CHECKPOINT_POOL_MIN_SIZE = 1
_CHECKPOINT_POOL_MAX_SIZE = 5
_CHECKPOINT_HEALTH_THREAD_ID = "__ran_ai_checkpoint_health__"


def checkpoint_database_url(database_url: str) -> str:
    """Convert the application's SQLAlchemy URL into a psycopg connection URL."""
    value = (database_url or "").strip()
    for prefix in ("postgresql+asyncpg://", "postgresql+psycopg://"):
        if value.startswith(prefix):
            return "postgresql://" + value.removeprefix(prefix)
    if value.startswith(("postgresql://", "postgres://")):
        return value
    raise ValueError("LangGraph PostgreSQL checkpoint requires a PostgreSQL DATABASE_URL")


def build_checkpoint_thread_id(
    *,
    workflow: str,
    channel: str,
    chat_id: str,
    message_id: str | None,
    fallback_key: str,
) -> str:
    """Build a stable, non-PII thread id for one independently replayable run."""
    identity = "\x1f".join(
        (
            workflow.strip() or "workflow",
            channel.strip() or "unknown",
            chat_id.strip(),
            (message_id or "").strip() or fallback_key,
        )
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"ran-ai:{workflow.strip() or 'workflow'}:{digest}"


def checkpoint_config(
    thread_id: str,
    *,
    checkpoint_ns: str = "",
    recursion_limit: int | None = None,
) -> dict[str, Any]:
    configurable = {"thread_id": thread_id}
    if checkpoint_ns:
        configurable["checkpoint_ns"] = checkpoint_ns
    config: dict[str, Any] = {"configurable": configurable}
    if recursion_limit is not None:
        config["recursion_limit"] = recursion_limit
    return config


def _checkpoint_serializer() -> JsonPlusSerializer:
    # EngineState contains ChannelMessage. Explicitly allow this one local dataclass
    # instead of enabling arbitrary module deserialization.
    return JsonPlusSerializer(
        allowed_msgpack_modules=[
            ("app.services.channels.base", "ChannelMessage"),
        ]
    )


class AgentCheckpointRuntime:
    """Process-level psycopg pool shared by LangGraph reply runs."""

    def __init__(self) -> None:
        self._pool: AsyncConnectionPool | None = None
        self._saver: AsyncPostgresSaver | None = None

    @property
    def saver(self) -> AsyncPostgresSaver | None:
        return self._saver

    async def start(self, settings: Settings | None = None) -> None:
        if self._saver is not None:
            return
        settings = settings or get_settings()
        pool = AsyncConnectionPool(
            conninfo=checkpoint_database_url(settings.database_url),
            min_size=_CHECKPOINT_POOL_MIN_SIZE,
            max_size=_CHECKPOINT_POOL_MAX_SIZE,
            open=False,
            kwargs={
                "autocommit": True,
                "prepare_threshold": 0,
                "row_factory": dict_row,
            },
            check=AsyncConnectionPool.check_connection,
            name="ran-ai-langgraph-checkpoint",
        )
        try:
            await pool.open(wait=True)
            saver = AsyncPostgresSaver(pool, serde=_checkpoint_serializer())
            # Read-only validation: setup is intentionally a deployment step, not
            # application-start DDL. Missing tables therefore fail fast here.
            await saver.aget_tuple(
                checkpoint_config(_CHECKPOINT_HEALTH_THREAD_ID)
            )
        except Exception:
            await pool.close()
            raise
        self._pool = pool
        self._saver = saver
        logger.info("LangGraph PostgreSQL checkpoint runtime started")

    async def stop(self) -> None:
        pool = self._pool
        self._saver = None
        self._pool = None
        if pool is not None:
            await pool.close()
            logger.info("LangGraph PostgreSQL checkpoint runtime stopped")


_runtime = AgentCheckpointRuntime()


async def start_agent_checkpoint_runtime(settings: Settings | None = None) -> None:
    await _runtime.start(settings)


async def stop_agent_checkpoint_runtime() -> None:
    await _runtime.stop()


def get_agent_checkpointer() -> AsyncPostgresSaver | None:
    return _runtime.saver


async def setup_checkpoint_database(settings: Settings | None = None) -> None:
    """Create or upgrade LangGraph's own checkpoint tables once per deployment."""
    settings = settings or get_settings()
    async with AsyncPostgresSaver.from_conn_string(
        checkpoint_database_url(settings.database_url),
        serde=_checkpoint_serializer(),
    ) as saver:
        await saver.setup()


async def ainvoke_resumable(
    graph: Any,
    initial_state: dict[str, Any],
    *,
    config: dict[str, Any],
    checkpointer: BaseCheckpointSaver | None,
    delete_thread_on_success: bool,
) -> dict[str, Any]:
    """Invoke a graph once, or resume its last unfinished node after interruption."""
    if checkpointer is None:
        return await graph.ainvoke(initial_state, config=config)

    existing = await checkpointer.aget_tuple(config)
    if existing is None:
        graph_input: dict[str, Any] | None = initial_state
    else:
        snapshot = await graph.aget_state(config)
        if not snapshot.next:
            result = dict(snapshot.values)
            if delete_thread_on_success:
                await _delete_thread_best_effort(checkpointer, config)
            return result
        graph_input = None

    result = await graph.ainvoke(graph_input, config=config)
    if delete_thread_on_success:
        snapshot = await graph.aget_state(config)
        if not snapshot.next:
            await _delete_thread_best_effort(checkpointer, config)
    return result


async def _delete_thread_best_effort(
    checkpointer: BaseCheckpointSaver,
    config: dict[str, Any],
) -> None:
    thread_id = str((config.get("configurable") or {}).get("thread_id") or "")
    if not thread_id:
        return
    try:
        await checkpointer.adelete_thread(thread_id)
    except Exception:
        # Execution has already completed. Leaving an old checkpoint is safer
        # than turning successful message handling into a retry.
        logger.exception(
            "Failed to clean completed LangGraph checkpoint thread_id=%s",
            thread_id,
        )


def main() -> None:
    asyncio.run(setup_checkpoint_database())


if __name__ == "__main__":
    main()
