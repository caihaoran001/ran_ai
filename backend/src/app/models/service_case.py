from __future__ import annotations

import enum
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, Index, JSON, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.agent_reminder_task import AgentReminderTask
    from app.models.ai_usage import AIUsageLog
    from app.models.checkin import Checkin
    from app.models.message import Message
    from app.models.outcome import OutcomeEvent, OutcomeSnapshot, OutcomeStageState
    from app.models.questionnaire import QuestionnaireSubmission
    from app.models.reminder import ReminderEvent
    from app.models.user import User
    from app.models.user_document import UserDocument
    from app.models.user_profile import UserProfile
    from app.models.user_state_profile import UserStateProfile


class ServiceCaseStatus(str, enum.Enum):
    CREATED = "created"
    QUESTIONNAIRE_SUBMITTED = "questionnaire_submitted"
    GENERATING = "generating"
    DOCUMENTS_GENERATED = "documents_generated"
    REVIEWING = "reviewing"
    APPROVED = "approved"
    IN_SERVICE = "in_service"
    COMPLETED = "completed"
    REFUNDED = "refunded"


class ServiceCase(TimestampMixin, Base):
    __tablename__ = "service_cases"
    __table_args__ = (
        Index(
            "ux_service_cases_group_id_active",
            "group_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    case_no: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    group_id: Mapped[str] = mapped_column(String(128), nullable=False)
    # QiWe 群名(/room/batchGetRoomDetail 的 roomName)：定时回填，用于按群名搜索服务单；空表示取不到(机器人已退/群解散)
    group_name: Mapped[str | None] = mapped_column(String(128))
    questionnaire_token: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    status: Mapped[ServiceCaseStatus] = mapped_column(
        Enum(ServiceCaseStatus, name="service_case_status"),
        default=ServiceCaseStatus.CREATED,
        nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 用户在问卷选的「计划开始日期」：服务计时起点(打卡/阶段报告/看板服务天数都跟它)；空则回退 started_at
    service_plan_start_date: Mapped[date | None] = mapped_column(Date)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    questionnaire_to_llm_output_ms: Mapped[int | None] = mapped_column()
    notes: Mapped[str | None] = mapped_column(String(2000))
    auto_send_documents: Mapped[bool] = mapped_column(Boolean, default=False)
    ai_auto_reply_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    ai_agent_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, server_default=text("false"))
    # 单服务单定时发送开关（默认开；实际发送还需对应全局开关也开启）：
    # 晚间打卡链接 / 无互动自动唤醒
    checkin_link_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, server_default=text("true"))
    inactive_wake_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, server_default=text("true"))
    risk_level: Mapped[str] = mapped_column(String(32), default="normal", index=True)
    risk_flags: Mapped[list[dict] | None] = mapped_column(JSON)
    needs_human_intervention: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    intervention_reasons: Mapped[list[dict] | None] = mapped_column(JSON)
    # 上次 @班主任转人工的时间(UTC)：持久化短期防刷屏节流，重启不丢，30 分钟内不重复@（替代旧内存字典）。
    last_handoff_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    renewal_intent_flags: Mapped[list[dict] | None] = mapped_column(JSON)
    refund_intent_flags: Mapped[list[dict] | None] = mapped_column(JSON)
    # 大模型识别到"疑似已完成退款"→置真进入后台"已退款待确认"列表；运营确认后才改 status=REFUNDED 停推送。
    refund_pending_confirmation: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default=text("false")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User | None] = relationship(back_populates="service_cases")
    questionnaire_submissions: Mapped[list[QuestionnaireSubmission]] = relationship(
        back_populates="service_case",
        cascade="all, delete-orphan",
    )
    user_profiles: Mapped[list[UserProfile]] = relationship(
        back_populates="service_case",
        cascade="all, delete-orphan",
    )
    user_documents: Mapped[list[UserDocument]] = relationship(
        back_populates="service_case",
        cascade="all, delete-orphan",
    )
    checkins: Mapped[list[Checkin]] = relationship(
        back_populates="service_case",
        cascade="all, delete-orphan",
    )
    messages: Mapped[list[Message]] = relationship(
        back_populates="service_case",
        cascade="all, delete-orphan",
    )
    reminder_events: Mapped[list[ReminderEvent]] = relationship(
        back_populates="service_case",
        cascade="all, delete-orphan",
    )
    agent_reminder_tasks: Mapped[list[AgentReminderTask]] = relationship(
        back_populates="service_case",
        cascade="all, delete-orphan",
    )
    outcome_events: Mapped[list[OutcomeEvent]] = relationship(
        back_populates="service_case",
        cascade="all, delete-orphan",
    )
    outcome_stage_states: Mapped[list[OutcomeStageState]] = relationship(
        back_populates="service_case",
        cascade="all, delete-orphan",
    )
    outcome_snapshots: Mapped[list[OutcomeSnapshot]] = relationship(
        back_populates="service_case",
        cascade="all, delete-orphan",
    )
    user_state_profile: Mapped[UserStateProfile | None] = relationship(
        back_populates="service_case",
        cascade="all, delete-orphan",
    )
    ai_usage_logs: Mapped[list[AIUsageLog]] = relationship(
        back_populates="service_case",
        cascade="all, delete-orphan",
    )
