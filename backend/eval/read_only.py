from __future__ import annotations

import re

from sqlalchemy import event
from sqlalchemy.engine import Connection


_WRITE_PATTERN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|CREATE|ALTER|DROP|TRUNCATE|GRANT|REVOKE|"
    r"COPY|VACUUM|CALL|REFRESH|REINDEX|CLUSTER|COMMENT)\b|\bFOR\s+UPDATE\b|"
    r"\bNEXTVAL\s*\(",
    re.IGNORECASE,
)
_INSTALLED_ENGINE_IDS: set[int] = set()


class EvaluationWriteBlocked(RuntimeError):
    pass


def install_read_only_guards(async_engine) -> None:
    """Enforce read-only twice: PostgreSQL session mode plus SQL inspection."""

    sync_engine = async_engine.sync_engine
    engine_id = id(sync_engine)
    if engine_id in _INSTALLED_ENGINE_IDS:
        return

    @event.listens_for(sync_engine, "connect")
    def _set_database_read_only(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("SET default_transaction_read_only = on")
        finally:
            cursor.close()

    @event.listens_for(sync_engine, "before_cursor_execute")
    def _block_writes(
        _connection: Connection,
        _cursor,
        statement: str,
        _parameters,
        _context,
        _executemany: bool,
    ) -> None:
        if _WRITE_PATTERN.search(statement):
            raise EvaluationWriteBlocked(
                "RAG evaluation attempted a database write; execution was stopped"
            )

    _INSTALLED_ENGINE_IDS.add(engine_id)
