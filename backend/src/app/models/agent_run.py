from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin


class AgentRun(TimestampMixin, Base):
    """一次可独立评价的 Agent 运行。

    回复 Agent 的一行对应一个对话轮次；档案官的一行对应一次实际归档尝试。
    本表由独立事务写入，业务事务即使回滚，也不会抹掉已经发生的模型费用和失败终态。
    """

    __tablename__ = "agent_runs"
    __table_args__ = (
        Index("ix_agent_runs_run_key", "run_key"),
        Index("ix_agent_runs_type_created", "agent_type", "created_at"),
        Index("ix_agent_runs_kind_created", "run_kind", "created_at"),
        Index("ix_agent_runs_status_created", "status", "created_at"),
        Index("ix_agent_runs_outcome_created", "outcome", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_key: Mapped[str] = mapped_column(String(180), unique=True, nullable=False)
    agent_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    run_kind: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    service_case_id: Mapped[int | None] = mapped_column(
        ForeignKey("service_cases.id"), nullable=True, index=True
    )
    trigger_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="running", index=True
    )
    outcome: Mapped[str | None] = mapped_column(String(64), index=True)
    trigger_reason: Mapped[str | None] = mapped_column(String(80))
    model: Mapped[str | None] = mapped_column(String(120))
    model_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tool_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    successful_tool_calls: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    estimated_cost_yuan: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )
    input_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dropped_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error_type: Mapped[str | None] = mapped_column(String(120))
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    is_diagnostic: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, index=True
    )
    diagnostic_run_id: Mapped[str | None] = mapped_column(String(100), index=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
