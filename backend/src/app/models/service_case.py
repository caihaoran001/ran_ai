from __future__ import annotations

import enum
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    JSON,
    String,
    text,
)
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


class ServiceCaseGroupType(str, enum.Enum):
    NEW_GROUP = "new_group"
    LEGACY = "legacy"


class ServiceCaseLanguage(str, enum.Enum):
    ZH = "zh"
    EN = "en"


class ServiceCase(TimestampMixin, Base):
    __tablename__ = "service_cases"
    __table_args__ = (
        # 一个群同时只能有一张「客户在场」的服务单。客户退群后这张单让位，
        # 新客户进来可以在同一个群开新一期，旧单原样留在库里不必删。
        Index(
            "ux_service_cases_group_id_active",
            "group_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL AND member_left_at IS NULL"),
        ),
        CheckConstraint(
            "language IN ('zh', 'en')",
            name="ck_service_cases_language",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    case_no: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    group_id: Mapped[str] = mapped_column(String(128), nullable=False)
    # QiWe 群名(/room/batchGetRoomDetail 的 roomName)：定时回填，用于按群名搜索服务单；空表示取不到(机器人已退/群解散)
    group_name: Mapped[str | None] = mapped_column(String(128))
    # 群类型：new_group=走本系统新流程的客户；legacy=接入本系统之前就存在的老群。
    #
    # 2026-08-17 起建单时一律默认 new_group，不在建单路径上「识别」新老；老群是一个已经
    # 封存的历史集合（当时库里 179 张单，全部停在 CREATED、没交过入营问卷）。
    # 老群若日后确实又新建了服务单（机器人被拉进老群），由运营在后台手动改回 legacy。
    #
    # 2026-08-20 补回一条自动升级：**提交入营问卷时 legacy → new_group**（写在
    # questionnaires.py 的 intake 分支）。理由是运营复用老群接新客户时最容易漏改群类型，
    # 漏了这位客户就整个不进看板和群体画像。
    # 这段代码跑在常驻 ingress 上，**改它之后部署必须单独重建 ingress**：8/13–8/17 正是漏了
    # 这一步，线上跑着旧代码、不报错、测试全绿，四天里填过问卷的客户全被留在 legacy。
    group_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default="new_group",
        default=ServiceCaseGroupType.NEW_GROUP.value,
        index=True,
    )
    questionnaire_token: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    # 服务语言只允许中文/英文两种，由运营配置决定并贯穿问卷、文案、报告与提醒。
    language: Mapped[ServiceCaseLanguage] = mapped_column(
        String(10),
        nullable=False,
        server_default="zh",
        default=ServiceCaseLanguage.ZH,
    )
    status: Mapped[ServiceCaseStatus] = mapped_column(
        Enum(ServiceCaseStatus, name="service_case_status"),
        default=ServiceCaseStatus.CREATED,
        nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 用户在问卷选的「计划开始日期」：服务计时起点(打卡/阶段报告/看板服务天数都跟它)；空则回退 started_at
    service_plan_start_date: Mapped[date | None] = mapped_column(Date)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 客户本人退群的时间；非空 = 这张单的客户已经不在群里了（后台显示「已退群」标签）。
    #
    # 这是「一个群先后服务多位客户」的让位标记，与服务状态是两回事：人走了不代表退款或完结，
    # 该退款还是完结由运营判断。系统据此停掉这个群里的一切自动动作（打卡提醒、追问、
    # 阶段报告、视频推送），并把群位让给下一位客户；同一个人回到同一个群且尚未开新一期时，
    # 这个标记会被撤掉、原单接着服务。
    member_left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 28 天阶段完结后在原服务单上恢复续服的最近时间。状态为 IN_SERVICE 且此字段非空时，
    # 原 28 天截止不再自动结束；服务天数仍沿用原起点连续计算。
    service_continued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 轻量审计轨迹：记录群内设置开始日期、续服/结束服务动作的时间、来源消息和发送者。
    # 不建新服务周期，也不覆盖历史聊天/打卡/文档。
    service_lifecycle_events: Mapped[list[dict] | None] = mapped_column(JSON)
    # 标准档 / 高承诺档是执行承诺，不复用服务生命周期 status。
    # 这里保存当前档位、目标、用户确认原始证据和各节点复评留痕；每次更新都整体赋新 dict，
    # 避免 JSON 原地修改未被 SQLAlchemy 追踪。
    execution_commitment: Mapped[dict | None] = mapped_column(JSON)
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
    # 机器人被群成员首次 @ 并成功发出自我介绍后才置值。
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
    # 平台期检测结果（plateau_detector 周期扫描写入）：normal / plateau / insufficient_data。
    # plateau_evidence 记录判定依据（窗口、首尾体重、差值、用户自述原话），供运营详情页核对。
    plateau_status: Mapped[str] = mapped_column(
        String(20), default="normal", nullable=False, server_default="normal", index=True
    )
    plateau_evidence: Mapped[dict | None] = mapped_column(JSON)
    plateau_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 运营在审核控制台标记「已跟进」的时间。待办判据是它与 plateau_updated_at 的先后：
    # 结论之后又变过一次（比如离开平台期又回来、或从平台期转成反弹）就重新待跟进，
    # 结论一直没变则保持已处理，红点能归零。
    plateau_followed_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
