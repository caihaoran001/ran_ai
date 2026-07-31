from __future__ import annotations

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin


class AIUsageLog(TimestampMixin, Base):
    __tablename__ = "ai_usage_logs"
    __table_args__ = (
        Index("ix_ai_usage_logs_service_case_created", "service_case_id", "created_at"),
        Index("ix_ai_usage_logs_scenario_created", "scenario", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_runs.id"),
        nullable=True,
        index=True,
    )
    service_case_id: Mapped[int | None] = mapped_column(
        ForeignKey("service_cases.id"),
        nullable=True,
        index=True,
    )
    message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id"), nullable=True, index=True)
    scenario: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    # DeepSeek 硬盘缓存命中/未命中输入 token 拆分 (prompt_tokens = 命中 + 未命中)。
    prompt_cache_hit_tokens: Mapped[int | None] = mapped_column(Integer)
    prompt_cache_miss_tokens: Mapped[int | None] = mapped_column(Integer)
    estimated_cost_yuan: Mapped[float | None] = mapped_column(Float)
    elapsed_ms: Mapped[int | None] = mapped_column(Integer)
    attempts: Mapped[int | None] = mapped_column(Integer)
    success: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    finish_reason: Mapped[str | None] = mapped_column(String(60))
    error_type: Mapped[str | None] = mapped_column(String(120))
    is_diagnostic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    diagnostic_run_id: Mapped[str | None] = mapped_column(String(100), index=True)

    service_case = relationship("ServiceCase", back_populates="ai_usage_logs")
    message = relationship("Message")
