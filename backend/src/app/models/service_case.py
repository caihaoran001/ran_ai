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
    # 28 天阶段完结后在原服务单上恢复续服的最近时间。状态为 IN_SERVICE 且此字段非空时，
    # 原 28 天截止不再自动结束；服务天数仍沿用原起点连续计算。
    service_continued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 轻量审计轨迹：记录群内设置开始日期、续服/结束服务动作的时间、来源消息和发送者。
    # 不建新服务周期，也不覆盖历史聊天/打卡/文档。
    service_lifecycle_events: Mapped[list[dict] | None] = mapped_column(JSON)
    content_generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 入营问卷提交后的"待生成"信号：提交只落库并置此字段，重活(风险分析→画像/文档→欢迎语)
    # 由 backend 的 reminder loop 周期消费后清空。让公开提交入口能在常驻 ingress 上只做
    # DB 写、绝不在请求路径跑 LLM/发消息；也把它与运营 PATCH 重生成(自带 backend 后台任务、
    # 不置此字段)区分开，避免被 loop 重复消费或误发欢迎语。
    intake_pending_generation_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    questionnaire_to_llm_output_ms: Mapped[int | None] = mapped_column()
    notes: Mapped[str | None] = mapped_column(String(2000))
    auto_send_documents: Mapped[bool] = mapped_column(Boolean, default=False)
    ai_auto_reply_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # 机器人被员工首次 @ 并成功发出自我介绍后才置值。
    # 该闸门只控制群聊参与，与 28 天服务起算日期独立。
    ai_introduced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ai_introduced_by_wecom_user_id: Mapped[str | None] = mapped_column(String(128))
    ai_introduction_trigger_message_id: Mapped[int | None] = mapped_column()
    risk_level: Mapped[str] = mapped_column(String(32), default="normal", index=True)
    risk_flags: Mapped[list[dict] | None] = mapped_column(JSON)
    health_profile: Mapped[dict | None] = mapped_column(JSON)
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
