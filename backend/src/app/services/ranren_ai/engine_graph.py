"""消息编排的 LangGraph 表达（结构迁移，行为与 RanrenAIEngine.handle_message /
reprocess_message 逐段等价）。

设计见 plan：单图 + entry_mode 承接两个入口，共享 risk→handoff→reply 尾部；
节点只做「胶水 + 调用引擎现有 helper」，不重写业务逻辑；每个「守卫即 return」映射为
一条到 END 的条件边。异常传播语义保持不变——节点不额外吞异常，异常照旧冒出 ainvoke，
与旧代码里异常冒出 handle_message 一致（供聚合层据此保留 inbound_event PENDING 等）。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.models.message import (
    Message,
    MessageDirection,
    MessagePurpose,
    MessageSenderType,
    MessageStatus,
)
from app.models.inbound_event import InboundEvent
from app.models.service_case import ServiceCase
from app.models.user import User
from app.services.agent.checkpoint import (
    ainvoke_resumable,
    build_checkpoint_thread_id,
    checkpoint_config,
    get_agent_checkpointer,
)
from app.services.agent_observability import (
    COMPLETED as AGENT_RUN_COMPLETED,
    FAILED as AGENT_RUN_FAILED,
    complete_agent_run,
    detect_product_terms,
    ensure_agent_run,
)
from app.services.channels.base import ChannelMessage
from app.services.qiwe_service.member_names import (
    parse_member_ids,
    resolve_member_join_text,
)
from app.services.qiwe_service.non_text import (
    CARD_MEMBER_JOIN,
    CARD_MEMBER_QUIT,
    CARD_ROOM_RENAMED,
)
# 通过 engine 模块引用 SessionLocal / analyze_message_insight / generate_agent_reply /
# get_ai_reply_enabled / AgentDataUnavailableError：测试把补丁打在 engine 模块上
# （conftest 全局 + 个别用例 per-test），经此引用才能命中，行为与旧 handle_message 一致。
from app.services.ranren_ai import engine as engine_mod
from app.services.ranren_ai.engine import (
    _bot_user_id_from_message,
    _build_agent_info_gap,
    _current_query_speaker,
    _explicit_bot_query_text,
    _group_bot_control_command,
    _message_already_processed_or_claimed,
    _OUTBOUND_REQUEST_STARTED_STATUSES,
    _REPLY_STATUS_COMPLETED,
    _REPLY_STATUS_PROCESSING,
    _REPLY_STATUS_RETRYABLE_FAILURE,
    _with_reply_processing_status,
    _message_mentions_bot,
    _reply_is_no_reply,
    _requires_health_handoff,
    _sanitize_ai_reply,
    _segments_from_meta,
)
from app.services.ranren_ai.commands import (
    is_language_switch_command,
    is_questionnaire_command,
)
from app.services.service_case_queries import select_active_service_cases

if TYPE_CHECKING:
    from app.services.ranren_ai.engine import RanrenAIEngine

logger = logging.getLogger("uvicorn.error")

_INSIGHT_RECENT_MESSAGE_LIMIT = 10


class EngineState(TypedDict, total=False):
    # 输入
    message: ChannelMessage
    db_message_id: int  # 仅 reprocess 入口
    entry_mode: str  # "fresh" | "reprocess" | "reply_only"
    started_at: float
    checkpoint_thread_id: str
    recovery_started_at: datetime | None
    # 计算态（把原 handle_message 的局部量摊平；节点覆盖式更新）
    should_send_questionnaire: bool
    group_control_command: str | None
    explicit_bot_query_text: str | None
    is_staff: bool
    sender_type: Any
    service_case_id: int | None
    service_case_language: str
    service_case_introduced: bool
    should_send_introduction: bool
    service_case_silenced: bool
    resume_takeover_receipt: bool
    analysis: dict | None
    inbound_id: int | None
    trigger_id: int | None  # = inbound_id；原生挂载的 reply 子图按键名读取，桥接给它
    recent_user_messages: list
    image_analysis_content: str | None
    file_processed: bool
    non_text_stored_only: bool
    case_risk_level: str | None
    purpose: Any
    needs_handoff: bool
    reply: str
    outbound_analysis: dict | None
    should_send: bool
    agent_run_id: int | None
    _halt: bool  # reprocess_head 守卫失败 → 直接结束
    _completion_status: str
    _completion_reason: str
    _skip_finalization: bool


def _knowledge_query_from_analysis(analysis: dict | None) -> str | None:
    """Preserve knowledge-query tri-state: text=search, empty=skip, None=fallback."""
    if not isinstance(analysis, dict) or "knowledge_query" not in analysis:
        return None
    value = analysis.get("knowledge_query")
    if value is None:
        return ""
    if not isinstance(value, str):
        return None
    clean = value.strip()
    return clean or None


def _insight_recent_messages(recent_messages: list[str]) -> list[str]:
    """Keep intent/rewrite context local without shrinking the reply Agent history."""
    return list(recent_messages[-_INSIGHT_RECENT_MESSAGE_LIMIT:])


def _reply_run_metadata(agent_metadata: dict[str, Any]) -> dict[str, Any]:
    """回复轮次落进 agent_runs.metadata_json 的观测字段。"""
    metadata: dict[str, Any] = {
        "data_tool_failed": bool(
            agent_metadata.get("data_tool_failed")
            or agent_metadata.get("data_unavailable")
        )
    }
    knowledge = agent_metadata.get("knowledge")
    if isinstance(knowledge, dict) and knowledge:
        metadata["knowledge"] = knowledge
    return metadata


def _knowledge_observation(
    *,
    knowledge_query: str | None,
    user_query: str,
    hit_count: int | None = None,
    hits: list[dict[str, Any]] | None = None,
    query_used: str | None = None,
    fallback_used: bool = False,
    primary_hit_count: int | None = None,
) -> dict[str, Any]:
    """把本轮参考问答检索的决策压成观测记录，写进 agent_runs.metadata_json。

    纯观测，不参与任何业务判断。三个决策对应 `_knowledge_query_from_analysis` 的三态：
    - searched: 意图识别给了独立查询，按该查询检索
    - skipped:  意图识别明确判定本轮不需要知识库，**完全没查库**
    - fallback: 字段缺失/非法，退回用本轮原问题检索

    `hit_count` 为 0 说明查了但库里没有匹配，跟 skipped 的"根本没查"是两种病，必须分开看。
    skipped 时额外记下用户消息里出现的产品名，供事后捞误判轮次（见 detect_product_terms 注释）。
    """
    if knowledge_query is None:
        decision = "fallback"
        query = (user_query or "").strip()
    elif not knowledge_query.strip():
        decision = "skipped"
        query = ""
    else:
        decision = "searched"
        query = knowledge_query.strip()

    observation: dict[str, Any] = {"decision": decision}
    original_query = (user_query or "").strip()
    if original_query:
        observation["original_query"] = original_query[:300]
    if query:
        observation["query"] = query[:300]
    if decision == "skipped":
        terms = detect_product_terms(user_query)
        if terms:
            observation["skipped_product_terms"] = terms
    elif hit_count is not None:
        observation["hit_count"] = int(hit_count)
        if primary_hit_count is not None:
            observation["primary_hit_count"] = int(primary_hit_count)
        if fallback_used:
            observation["rewrite_fallback_used"] = True
            used = (query_used or "").strip()
            if used:
                observation["effective_query"] = used[:300]
        if hits:
            observation["hits"] = list(hits)
    return observation


@dataclass(frozen=True)
class _EngineGraphExecutionContext:
    """Per-invocation bindings consumed by the process-shared compiled graph."""

    handler: "EngineGraph"


_ENGINE_GRAPH_CACHE: tuple[BaseCheckpointSaver | None, Any] | None = None


class EngineGraph:
    """One message's bindings backed by a process-shared compiled topology."""

    def __init__(
        self,
        engine: "RanrenAIEngine",
        *,
        checkpointer: BaseCheckpointSaver | None = None,
    ) -> None:
        self.engine = engine
        self.checkpointer = checkpointer
        self.graph = _get_engine_compiled_graph(checkpointer)

    async def run(
        self,
        state: EngineState,
        *,
        run_config: dict[str, Any] | None = None,
    ) -> None:
        execution_context = _EngineGraphExecutionContext(handler=self)
        if run_config is None:
            await self.graph.ainvoke(state, context=execution_context)
            return
        await ainvoke_resumable(
            self.graph,
            state,
            config=run_config,
            checkpointer=self.checkpointer,
            delete_thread_on_success=True,
            context=execution_context,
        )

    # ---------- 路由 ----------

    def _route_start(self, state: EngineState) -> str:
        if state.get("entry_mode") == "reprocess":
            return "reprocess_head"
        if state.get("entry_mode") == "reply_only":
            return "reply_prep"
        return "setup"

    def _route_after_setup(self, state: EngineState) -> str:
        if state.get("_halt"):
            return END
        if state.get("should_send_introduction"):
            return "group_introduction"
        return "group_command_reply" if state.get("group_control_command") else "attachments"

    def _route_after_attachments(self, state: EngineState) -> str:
        if not state.get("service_case_introduced"):
            return END
        if state.get("service_case_silenced"):
            return END
        if state.get("file_processed"):
            return "file_ack"
        if state.get("non_text_stored_only"):
            return END
        return "staff_gate"

    def _route_after_staff_gate(self, state: EngineState) -> str:
        is_staff = state.get("is_staff")
        should_q = state.get("should_send_questionnaire")
        if is_staff and not should_q and not state.get("explicit_bot_query_text"):
            return END
        if should_q:
            return "questionnaire"
        return "insight"

    def _route_after_reprocess_head(self, state: EngineState) -> str:
        if state.get("_halt"):
            return END
        if state.get("should_send_introduction"):
            return "group_introduction"
        if state.get("group_control_command"):
            return "group_command_reply"
        return "attachments"

    def _route_after_insight(self, state: EngineState) -> str:
        if state.get("needs_handoff"):
            return "handoff"
        if not state.get("service_case_id"):
            return END
        if state.get("purpose") != MessagePurpose.CHAT:
            return END
        return "reply_prep"

    def _route_after_reply_prep(self, state: EngineState) -> str:
        return "reply_generate" if not state.get("_halt") else END

    def _route_after_reply_generate(self, state: EngineState) -> str:
        return "reply_guards" if not state.get("_halt") else END

    def _route_after_reply_guards(self, state: EngineState) -> str:
        if state.get("_halt"):
            return END
        return "reply_send" if state.get("should_send") else END

    async def _outbound_exists_for_trigger(self, trigger_id: int | None) -> bool:
        if not trigger_id:
            return False
        async with engine_mod.SessionLocal() as session:
            if not hasattr(session, "scalar"):
                return False
            outbound_id = await session.scalar(
                select(Message.id)
                .where(
                    Message.direction == MessageDirection.OUTBOUND,
                    Message.trigger_id == trigger_id,
                    Message.status.in_(_OUTBOUND_REQUEST_STARTED_STATUSES),
                )
                .limit(1)
            )
        return outbound_id is not None

    async def _ensure_reply_run(self, state: EngineState) -> int | None:
        existing = state.get("agent_run_id")
        if existing is not None:
            return existing
        trigger_id = state.get("trigger_id") or state.get("inbound_id")
        if trigger_id is None:
            return None
        message = state.get("message")
        return await ensure_agent_run(
            run_key=f"reply:{trigger_id}",
            agent_type="reply",
            run_kind="interactive",
            service_case_id=state.get("service_case_id"),
            trigger_message_id=trigger_id,
            input_count=1,
            metadata={
                "channel": getattr(message, "channel", None),
                "entry_mode": state.get("entry_mode"),
            },
        )

    # ---------- fresh 节点 ----------

    async def _setup(self, state: EngineState) -> dict[str, Any]:
        """对应 handle_message 211–343：昵称/staff 判定/找建 case/静音-回来/自愈绑定/recent/入库。"""
        message: ChannelMessage = state["message"]
        started_at = state["started_at"]
        engine = self.engine
        should_send_questionnaire = state["should_send_questionnaire"]
        group_control_command = state["group_control_command"]
        explicit_bot_query_text = state["explicit_bot_query_text"]

        service_case_id: int | None = None
        service_case_language: str = "zh"
        service_case_introduced = False
        should_send_introduction = False
        service_case_silenced = False
        resume_takeover_receipt = False
        analysis: dict | None = None
        inbound_id: int | None = None
        recent_user_messages: list[str] = []
        is_staff = False
        sender_type = MessageSenderType.USER
        callback_sender_name = (message.user_name or "").strip()

        async with engine_mod.SessionLocal() as session:
            from app.services.content_policy import (
                get_qiwe_group_nickname,
                get_qiwei_staff_senders,
                get_qiwe_nickname,
                remember_qiwe_group_nicknames,
                remember_qiwe_nickname,
            )
            # A process can die after committing the inbound Message but before
            # LangGraph persists the setup-node checkpoint. Reuse that durable
            # row on resume instead of inserting the same callback twice.
            if message.message_id and hasattr(session, "scalar"):
                existing_inbound = await session.scalar(
                    select(Message)
                    .where(
                        Message.group_id == message.chat_id,
                        Message.wecom_message_id == message.message_id,
                        Message.direction == MessageDirection.INBOUND,
                    )
                    .order_by(Message.id.desc())
                    .limit(1)
                )
                if existing_inbound is not None:
                    restored = await self._restore_existing_inbound_state(
                        session,
                        message=message,
                        inbound=existing_inbound,
                        should_send_questionnaire=should_send_questionnaire,
                        group_control_command=group_control_command,
                        explicit_bot_query_text=explicit_bot_query_text,
                    )
                    if await self._link_inbound_events(
                        session,
                        message=message,
                        inbound_id=existing_inbound.id,
                    ):
                        await session.commit()
                    return restored

            if message.channel == "qiwe" and message.user_id:
                group_nickname = ""
                if message.chat_type == "group":
                    group_nickname = (message.user_name or "").strip()
                    if group_nickname:
                        await remember_qiwe_group_nicknames(
                            session,
                            message.chat_id,
                            {message.user_id: group_nickname},
                        )
                    else:
                        group_nickname = await get_qiwe_group_nickname(
                            session,
                            message.chat_id,
                            message.user_id,
                        )
                    if not group_nickname and hasattr(engine.channel, "get_room_member_nicknames"):
                        try:
                            room_names = await engine.channel.get_room_member_nicknames(message.chat_id)
                            if room_names:
                                await remember_qiwe_group_nicknames(session, message.chat_id, room_names)
                                group_nickname = room_names.get(str(message.user_id), "")
                        except Exception as e:
                            logger.error("Error fetching QiWe room member nicknames in engine: %s", e)
                if group_nickname:
                    message.user_name = group_nickname

                if not message.user_name:
                    cached = await get_qiwe_nickname(session, message.user_id)
                    if cached:
                        message.user_name = cached
                if not message.user_name and hasattr(engine.channel, "get_contact_nickname"):
                    try:
                        resolved = await engine.channel.get_contact_nickname(message.user_id)
                        if resolved:
                            message.user_name = resolved
                            await remember_qiwe_nickname(session, message.user_id, resolved)
                    except Exception as e:
                        logger.error("Error fetching contact nickname in engine: %s", e)

            rules = await get_qiwei_staff_senders(session)
            is_staff = engine._check_is_staff_by_rules(message.user_id, message.user_name, rules)
            if not is_staff and callback_sender_name != (message.user_name or "").strip():
                is_staff = engine._check_is_staff_by_rules(
                    message.user_id,
                    callback_sender_name,
                    rules,
                )
            sender_type = MessageSenderType.STAFF if is_staff else MessageSenderType.USER

            service_case = await engine._latest_active_case(session, message.chat_id)
            if service_case is None:
                # 上一位客户已退群、群位空着：原客户回来就接回原单，否则任何人说话即开新一期
                # ——和全新群同一条规则，客户离开之后的对话不会有一段没人认领。
                _emptied_mode, service_case = await engine._resolve_emptied_group(session, message)
            if not service_case and (message.chat_type == "group" or should_send_questionnaire):
                service_case = await engine._create_case_for_message(session, message, is_staff=is_staff)

            if service_case:
                service_case_id = service_case.id
                service_case_language = getattr(service_case, "language", "zh") or "zh"
                service_case_introduced = service_case.ai_introduced_at is not None
                should_send_introduction = bool(
                    not service_case_introduced
                    and message.chat_type == "group"
                    and message.message_type == "text"
                    and _message_mentions_bot(message)
                )

                # 未引荐时的第一次群成员 @ 只能做引荐，其后正文不执行任何指令。
                if service_case_introduced and group_control_command == "silence":
                    service_case.ai_auto_reply_enabled = False
                    session.add(service_case)
                    await session.flush()
                    logger.info(
                        "RanrenAI group command silenced bot chat_id=%s service_case_id=%s message_id=%s",
                        message.chat_id, service_case.id, message.message_id,
                    )
                elif service_case_introduced and group_control_command == "resume":
                    was_silenced = not bool(getattr(service_case, "ai_auto_reply_enabled", True))
                    service_case.ai_auto_reply_enabled = True
                    session.add(service_case)
                    await session.flush()
                    logger.info(
                        "RanrenAI group command resumed bot chat_id=%s service_case_id=%s message_id=%s was_silenced=%s",
                        message.chat_id, service_case.id, message.message_id, was_silenced,
                    )
                    resume_takeover_receipt = was_silenced

                switch_lang = is_language_switch_command(message.text) if message.message_type == "text" else None
                if switch_lang and _message_mentions_bot(message):
                    if getattr(service_case, "language", "zh") != switch_lang:
                        service_case.language = switch_lang
                        session.add(service_case)
                        await session.flush()
                        service_case_language = switch_lang
                        logger.info(
                            "RanrenAI group command switched language chat_id=%s service_case_id=%s language=%s",
                            message.chat_id, service_case.id, switch_lang,
                        )

                service_case_silenced = not bool(getattr(service_case, "ai_auto_reply_enabled", True))

                # 进退群通知也是「非员工发言」，但绝不能拿它认客户：生产上 #285 建单那一刻
                # 收到的第一条就是某人的退群通知，于是这张单被绑到了一个刚走的人身上，真客户
                # 从头到尾用另一个 id 说话，最后被成员名单对账误判成「客户已退群」，
                # 服务中的人被一刀切断（打卡提醒、追问、阶段报告全停）。只认真实发言。
                if (
                    not is_staff
                    and message.channel == "qiwe"
                    and message.user_id
                    and message.message_type != "system_notice"
                ):
                    case_user = None
                    if hasattr(session, "get"):
                        case_user = await session.get(User, service_case.user_id)
                    if case_user:
                        needs_binding = False
                        user_changed = False
                        if not case_user.wecom_user_id:
                            needs_binding = True
                        else:
                            is_bound_staff = engine._check_is_staff_by_rules(case_user.wecom_user_id, None, rules)
                            if is_bound_staff:
                                needs_binding = True
                        if needs_binding:
                            logger.info(
                                "Binding real customer WeCom User ID %s to case user_id=%s (previous was %s)",
                                message.user_id, case_user.id, case_user.wecom_user_id,
                            )
                            case_user.wecom_user_id = message.user_id
                            user_changed = True
                        if (
                            message.user_name
                            and case_user.name in engine_mod._PLACEHOLDER_USER_NAMES
                        ):
                            case_user.name = message.user_name
                            user_changed = True
                        if user_changed:
                            session.add(case_user)
                            await session.flush()

            if (
                service_case
                and service_case_introduced
                and not should_send_questionnaire
                and not is_staff
            ):
                recent_user_messages = await engine._recent_user_messages(session, service_case.id)

            purpose = MessagePurpose.CHAT

            content_text = message.text
            if (message.media_card or {}).get("kind") == CARD_MEMBER_JOIN:
                # 企微入群通知只给成员 id，姓名要从我们自己的聊天记录里换；
                # 换不到就保留「邀请 N 人加入群聊」，不显示一串 id。
                named = await resolve_member_join_text(
                    session,
                    group_id=message.chat_id,
                    member_ids=parse_member_ids(message.media_card),
                )
                if named:
                    content_text = named

            meta = {
                "sender_id": message.user_id,
                "sender_name": message.user_name,
                "sender_type": sender_type.value,
                "timestamp": int(message.timestamp.timestamp()),
            }
            if message.media_card:
                # 链接/小程序卡片的标题、描述、原链接：后台聊天记录据此还原成一张卡片。
                meta["media_card"] = message.media_card
            if message.voice_transcript:
                # 正文是语音转来的：后台聊天记录据此标注来源，回复方据此对数字保持警惕。
                meta["voice_transcript"] = True
            # 分段结构（每句正文 + 它自己的引用）必须原样落库：重启后补跑这条消息时
            # 要靠它还原"哪句回复了哪条"，摊平存会丢掉这层对应关系。
            quoted_segments = [
                {
                    "text": segment_text,
                    "quoted": [
                        {"message_id": quoted_id, "content": (quoted_text or "")[:500]}
                        for quoted_id, quoted_text in quoted_list
                        if quoted_id
                    ],
                }
                for segment_text, quoted_list in message.segments
            ]
            if any(segment["quoted"] for segment in quoted_segments):
                meta["segments"] = quoted_segments
            if explicit_bot_query_text:
                meta["explicit_bot_query_text"] = explicit_bot_query_text
            analysis = _with_reply_processing_status(
                {"_qiwe_meta": meta},
                status=_REPLY_STATUS_PROCESSING,
                reason="fresh_inbound",
            )
            if service_case and not service_case_introduced:
                analysis["_reply_gate"] = {
                    "state": (
                        "introduction_trigger"
                        if should_send_introduction
                        else "awaiting_staff_introduction"
                    )
                }
            if group_control_command:
                analysis["_group_control"] = {
                    "command": group_control_command,
                    "resume_receipt": resume_takeover_receipt,
                }
            if should_send_questionnaire:
                analysis["_system_command"] = "send_questionnaire"

            db_inbound = Message(
                service_case_id=service_case.id if service_case else None,
                group_id=message.chat_id,
                wecom_message_id=message.message_id,
                direction=MessageDirection.INBOUND,
                sender_type=sender_type,
                message_type=message.message_type,
                content=content_text,
                purpose=purpose,
                status=MessageStatus.RECEIVED,
                analysis_json=analysis,
            )
            session.add(db_inbound)
            await session.flush()
            inbound_id = db_inbound.id
            card_kind = (message.media_card or {}).get("kind")
            if service_case is not None and card_kind == CARD_MEMBER_QUIT:
                # 客户本人退群：这条通知先留在本期记录里，随后这张单让出群位。
                await engine._mark_member_left(session, service_case, message)
            elif service_case is not None and card_kind == CARD_MEMBER_JOIN:
                # 有人被拉进群：这正是复用老群的那一刻，当场核一下上一位客户是否已经走了，
                # 别等最长 6 小时的兜底对账（那期间新客户的话会记错档案）。
                await engine._recheck_membership_on_join(session, service_case, message)
            await self._link_inbound_events(
                session,
                message=message,
                inbound_id=inbound_id,
            )
            await session.commit()
            logger.info(
                "RanrenAI inbound stored channel=%s chat_id=%s sender_type=%s message_id=%s db_message_id=%s service_case_id=%s command=%s elapsed_ms=%.1f",
                message.channel, message.chat_id, sender_type, message.message_id,
                inbound_id, service_case_id, should_send_questionnaire,
                (time.monotonic() - started_at) * 1000,
            )
            if service_case_id is not None and card_kind == CARD_ROOM_RENAMED:
                # 群被改名：当场把新群名同步到这张单，别等 24 小时那道全量刷新。
                # 放在 commit 之后单独开事务：回查带重试要等待，不能占着入站事务的连接，
                # 失败也不能牵连已经入库的这条消息。改名通知不触发回复，等这几秒无人感知。
                await engine._refresh_group_name_on_rename(
                    session, service_case_id, message.chat_id
                )

        return {
            "is_staff": is_staff,
            "sender_type": sender_type,
            "service_case_id": service_case_id,
            "service_case_language": service_case_language,
            "service_case_introduced": service_case_introduced,
            "should_send_introduction": should_send_introduction,
            "service_case_silenced": service_case_silenced,
            "resume_takeover_receipt": resume_takeover_receipt,
            "analysis": analysis,
            "inbound_id": inbound_id,
            "trigger_id": inbound_id,
            "recent_user_messages": recent_user_messages,
            "purpose": purpose,
        }

    async def _link_inbound_events(
        self,
        session: Any,
        *,
        message: ChannelMessage,
        inbound_id: int,
    ) -> bool:
        """Atomically acknowledge the durable inbox at the business boundary."""
        event_ids = list(dict.fromkeys(message.inbound_event_ids))
        if not event_ids:
            return False
        events = list(
            await session.scalars(
                select(InboundEvent)
                .where(InboundEvent.id.in_(event_ids))
                .with_for_update()
            )
        )
        if not events:
            logger.warning(
                "RanrenAI inbound event link found no rows ids=%s db_message_id=%s",
                event_ids,
                inbound_id,
            )
            return False
        now = datetime.now(timezone.utc)
        for event in events:
            event.business_message_id = inbound_id
            event.status = InboundEvent.STATUS_PROCESSED
            event.processed_at = now
            session.add(event)
        logger.info(
            "RanrenAI linked inbox events ids=%s db_message_id=%s",
            [event.id for event in events],
            inbound_id,
        )
        return True

    async def _restore_existing_inbound_state(
        self,
        session: Any,
        *,
        message: ChannelMessage,
        inbound: Message,
        should_send_questionnaire: bool,
        group_control_command: str | None,
        explicit_bot_query_text: str | None,
    ) -> dict[str, Any]:
        """Rehydrate setup output after the DB-commit/checkpoint crash window."""
        analysis = dict(inbound.analysis_json) if isinstance(inbound.analysis_json, dict) else {}
        service_case = (
            await session.get(ServiceCase, inbound.service_case_id)
            if inbound.service_case_id
            else None
        )
        service_case_introduced = bool(
            service_case and service_case.ai_introduced_at is not None
        )
        reply_gate = analysis.get("_reply_gate") if isinstance(analysis.get("_reply_gate"), dict) else {}
        should_send_introduction = reply_gate.get("state") == "introduction_trigger"
        group_meta = (
            analysis.get("_group_control")
            if isinstance(analysis.get("_group_control"), dict)
            else {}
        )
        restored_command = str(group_meta.get("command") or group_control_command or "") or None
        resume_takeover_receipt = bool(group_meta.get("resume_receipt"))
        meta = analysis.get("_qiwe_meta") if isinstance(analysis.get("_qiwe_meta"), dict) else {}
        restored_explicit_query = (
            str(meta.get("explicit_bot_query_text") or explicit_bot_query_text or "").strip()
            or None
        )
        is_staff = inbound.sender_type == MessageSenderType.STAFF
        recent_user_messages = (
            await self.engine._recent_user_messages(
                session,
                inbound.service_case_id,
                exclude_message_id=inbound.id,
            )
            if inbound.service_case_id and service_case_introduced and not is_staff
            else []
        )
        outbound_exists = await session.scalar(
            select(Message.id)
            .where(
                Message.direction == MessageDirection.OUTBOUND,
                Message.trigger_id == inbound.id,
                Message.status.in_(_OUTBOUND_REQUEST_STARTED_STATUSES),
            )
            .limit(1)
        )
        already_processed = bool(outbound_exists) or _message_already_processed_or_claimed(
            analysis
        )
        logger.info(
            "RanrenAI reused durable inbound chat_id=%s message_id=%s db_message_id=%s checkpoint_resume=%s",
            message.chat_id,
            message.message_id,
            inbound.id,
            not already_processed,
        )
        return {
            "is_staff": is_staff,
            "sender_type": inbound.sender_type,
            "service_case_id": inbound.service_case_id,
            "service_case_introduced": service_case_introduced,
            "should_send_introduction": should_send_introduction,
            "service_case_silenced": bool(
                service_case and not service_case.ai_auto_reply_enabled
            ),
            "resume_takeover_receipt": resume_takeover_receipt,
            "analysis": analysis,
            "inbound_id": inbound.id,
            "trigger_id": inbound.id,
            "recent_user_messages": recent_user_messages,
            "purpose": inbound.purpose,
            "group_control_command": restored_command,
            "explicit_bot_query_text": restored_explicit_query,
            "should_send_questionnaire": should_send_questionnaire,
            "_halt": already_processed,
            "_skip_finalization": already_processed,
        }

    async def _group_introduction(self, state: EngineState) -> dict[str, Any]:
        """首次群成员 @ 是确定性引荐事件：只发自我介绍，不处理同条正文。"""
        message: ChannelMessage = state["message"]
        service_case_id = state.get("service_case_id")
        inbound_id = state.get("inbound_id")
        if not service_case_id or not inbound_id:
            logger.error(
                "RanrenAI cannot introduce without persisted case/message chat_id=%s",
                message.chat_id,
            )
            return {}
        await self.engine._reply_group_introduction(
            message,
            service_case_id=service_case_id,
            trigger_id=inbound_id,
        )
        return {}

    async def _group_command_reply(self, state: EngineState) -> dict[str, Any]:
        """对应 345–371：resume 回执（静默解除时），command 一律留痕不回复后结束。"""
        message: ChannelMessage = state["message"]
        started_at = state["started_at"]
        service_case_id = state.get("service_case_id")
        inbound_id = state.get("inbound_id")
        group_control_command = state.get("group_control_command")
        if (
            state.get("resume_takeover_receipt")
            and not await self._outbound_exists_for_trigger(inbound_id)
        ):
            receipt = "那我继续来跟进哈~"
            try:
                await self.engine._send_and_record(
                    message=message,
                    service_case_id=service_case_id,
                    content=receipt,
                    trigger_id=inbound_id,
                    send=lambda: self.engine.channel.send_text(
                        message.chat_id, receipt,
                        reply_to=message.message_id, reply_context=message.raw_payload,
                    ),
                )
            except Exception as e:
                logger.error("Failed to send resume command receipt: %s", e)
        logger.info(
            "RanrenAI group control command stored without reply chat_id=%s command=%s trigger_id=%s total_elapsed_ms=%.1f",
            message.chat_id, group_control_command, inbound_id,
            (time.monotonic() - started_at) * 1000,
        )
        return {}

    async def _attachments(self, state: EngineState) -> dict[str, Any]:
        """对应 373–378：解析入站图片/文件。"""
        message: ChannelMessage = state["message"]
        inbound_id = state.get("inbound_id")
        if inbound_id:
            async with engine_mod.SessionLocal() as session:
                persisted = (
                    await session.get(Message, inbound_id)
                    if hasattr(session, "get")
                    else None
                )
            persisted_analysis = (
                dict(persisted.analysis_json)
                if persisted and isinstance(persisted.analysis_json, dict)
                else {}
            )
            if message.message_type == "file" and isinstance(
                persisted_analysis.get("file_info"),
                dict,
            ):
                message.text = persisted.content
                return {
                    "analysis": persisted_analysis,
                    "image_analysis_content": None,
                    "file_processed": True,
                    "non_text_stored_only": False,
                }
            if isinstance(persisted_analysis.get("image_prompt"), dict):
                image_analysis_content = (
                    ""
                    if message.message_type == "image"
                    else engine_mod._strip_image_placeholders(message.text)
                )
                message.text = image_analysis_content
                return {
                    "analysis": persisted_analysis,
                    "image_analysis_content": image_analysis_content,
                    "file_processed": False,
                    "non_text_stored_only": False,
                }
        analysis, image_analysis_content, file_processed, non_text_stored_only = (
            await self.engine._process_inbound_attachments(
                message=message,
                inbound_id=inbound_id,
                service_case_id=state.get("service_case_id"),
                analysis=state.get("analysis"),
            )
        )
        return {
            "analysis": analysis,
            "image_analysis_content": image_analysis_content,
            "file_processed": file_processed,
            "non_text_stored_only": non_text_stored_only,
        }

    async def _file_ack(self, state: EngineState) -> dict[str, Any]:
        """对应 390–411：用户上传文件回执一句；班主任静默。"""
        if (
            not state.get("is_staff")
            and not await self._outbound_exists_for_trigger(state.get("inbound_id"))
        ):
            message: ChannelMessage = state["message"]
            ack = "文件收到啦，需要我帮你看什么？"
            try:
                await self.engine._send_and_record(
                    message=message,
                    service_case_id=state.get("service_case_id"),
                    content=ack,
                    trigger_id=state.get("inbound_id"),
                    send=lambda: self.engine.channel.send_text(
                        message.chat_id, ack,
                        reply_to=message.message_id, reply_context=message.raw_payload,
                    ),
                )
            except Exception:
                logger.exception("Failed to send file receipt ack message_id=%s", state.get("inbound_id"))
        return {}

    async def _staff_gate(self, state: EngineState) -> dict[str, Any]:
        """对应 416–448：班主任 @机器人无正文时回溯问题；覆盖 message.text。"""
        message: ChannelMessage = state["message"]
        is_staff = state.get("is_staff")
        should_q = state.get("should_send_questionnaire")
        explicit_bot_query_text = state.get("explicit_bot_query_text")
        service_case_id = state.get("service_case_id")
        inbound_id = state.get("inbound_id")

        if (
            is_staff
            and not should_q
            and not explicit_bot_query_text
            and message.message_type == "text"
            and _message_mentions_bot(message)
        ):
            recovered_query = await self.engine._staff_query_from_context(
                service_case_id=service_case_id,
                before_message_id=inbound_id,
            )
            if recovered_query:
                explicit_bot_query_text = recovered_query
                logger.info(
                    "RanrenAI recovered staff query from context chat_id=%s trigger_id=%s recovered_len=%d",
                    message.chat_id, inbound_id, len(recovered_query),
                )

        if explicit_bot_query_text:
            message.text = explicit_bot_query_text

        if is_staff and not should_q and not explicit_bot_query_text:
            logger.info(
                "RanrenAI staff message ignored from triggering AI reply channel=%s chat_id=%s message_id=%s",
                message.channel, message.chat_id, message.message_id,
            )
        return {"explicit_bot_query_text": explicit_bot_query_text}

    async def _questionnaire(self, state: EngineState) -> dict[str, Any]:
        """对应 450–460：发送入营问卷短链。"""
        message: ChannelMessage = state["message"]
        started_at = state["started_at"]
        inbound_id = state.get("inbound_id")
        if not await self._outbound_exists_for_trigger(inbound_id):
            service_case = None
            service_case_id = state.get("service_case_id")
            if service_case_id:
                async with engine_mod.SessionLocal() as session:
                    # 取这个群当前在场的那一期：上一位客户已退群的旧单查不到，
                    # 不会把问卷发成别人填过的那一份。
                    service_case = await self.engine._latest_active_case(
                        session,
                        message.chat_id,
                    )
            await self.engine._reply_questionnaire_link(
                message,
                service_case,
                trigger_id=inbound_id,
            )
        logger.info(
            "RanrenAI command handled channel=%s chat_id=%s message_id=%s trigger_id=%s total_elapsed_ms=%.1f",
            message.channel, message.chat_id, message.message_id, inbound_id,
            (time.monotonic() - started_at) * 1000,
        )
        return {}

    # ---------- reprocess 入口 ----------

    async def _reprocess_head(self, state: EngineState) -> dict[str, Any]:
        """对应 reprocess_message 530–618：锁/claim/校验/重建 ChannelMessage。任一守卫失败 → _halt。"""
        inbound_id = state["db_message_id"]

        async with engine_mod.SessionLocal() as session:
            stmt = select(Message).where(Message.id == inbound_id).with_for_update()
            msg_row = await session.scalar(stmt)
            if not msg_row:
                logger.warning("reprocess_message: Message %s not found in lock", inbound_id)
                return {"_halt": True}
            service_case_id = msg_row.service_case_id
            if not service_case_id:
                logger.warning("reprocess_message: message %s has no service_case_id", inbound_id)
                return {"_halt": True}

            analysis_json = msg_row.analysis_json if isinstance(msg_row.analysis_json, dict) else {}
            if _message_already_processed_or_claimed(
                analysis_json,
                claim_stale_before=state.get("recovery_started_at"),
            ):
                logger.info("reprocess_message: Skip message %s because it is already processed or claimed", inbound_id)
                return {"_halt": True, "_skip_finalization": True}

            reply_exists = await session.scalar(
                select(Message.id).where(
                    Message.direction == MessageDirection.OUTBOUND,
                    Message.trigger_id == inbound_id,
                    Message.status.in_(_OUTBOUND_REQUEST_STARTED_STATUSES),
                ).limit(1)
            )
            if reply_exists:
                logger.info("reprocess_message: Skip message %s because outbound reply already exists in lock", inbound_id)
                return {"_halt": True}

            newer_msg_exists = await session.scalar(
                select(Message.id).where(
                    Message.service_case_id == service_case_id,
                    Message.id > inbound_id,
                    Message.direction == MessageDirection.INBOUND,
                ).limit(1)
            )
            if newer_msg_exists:
                logger.info(
                    "reprocess_message: Skip message %s because newer inbound message %s exists",
                    inbound_id, newer_msg_exists,
                )
                await self.engine._retire_pending_outbound(
                    trigger_id=inbound_id,
                    reason="recovered reply suppressed because a newer inbound message exists",
                )
                return {"_halt": True}

            service_case = await session.scalar(select_active_service_cases(ServiceCase.id == service_case_id))
            reply_gate = (
                analysis_json.get("_reply_gate")
                if isinstance(analysis_json.get("_reply_gate"), dict)
                else {}
            )
            should_send_introduction = (
                reply_gate.get("state") == "introduction_trigger"
            )
            group_meta = (
                analysis_json.get("_group_control")
                if isinstance(analysis_json.get("_group_control"), dict)
                else {}
            )
            group_control_command = (
                str(group_meta.get("command") or "").strip() or None
            )
            if (
                not service_case
                or (
                    not service_case.ai_auto_reply_enabled
                    and group_control_command != "resume"
                )
                or (
                    service_case.ai_introduced_at is None
                    and not should_send_introduction
                )
            ):
                logger.info(
                    "reprocess_message: Skip message %s because case is inactive/silenced/not introduced",
                    inbound_id,
                )
                await self.engine._retire_pending_outbound(
                    trigger_id=inbound_id,
                    reason="recovered reply suppressed because the service case cannot auto-reply",
                )
                return {"_halt": True}

            claimed = dict(analysis_json)
            claimed["_recovery_claim"] = {
                "claimed_at": datetime.now(timezone.utc).isoformat(),
                "reason": "startup_recovery",
            }
            claimed = _with_reply_processing_status(
                claimed,
                status=_REPLY_STATUS_PROCESSING,
                reason="startup_recovery",
            )
            msg_row.analysis_json = claimed
            group_id = msg_row.group_id
            content = msg_row.content
            wecom_message_id = msg_row.wecom_message_id
            message_type = msg_row.message_type
            created_at = msg_row.created_at
            purpose = msg_row.purpose
            raw_payload = await session.scalar(
                select(InboundEvent.raw_payload)
                .where(
                    InboundEvent.channel == self.engine.channel.name,
                    InboundEvent.message_id == wecom_message_id,
                )
                .order_by(InboundEvent.id.desc())
                .limit(1)
            )
            await session.commit()

        meta = analysis_json.get("_qiwe_meta", {}) if analysis_json else {}
        explicit_bot_query_text = (
            str(meta.get("explicit_bot_query_text") or "").strip() or None
        )
        should_send_questionnaire = (
            analysis_json.get("_system_command") == "send_questionnaire"
        )
        is_staff = msg_row.sender_type == MessageSenderType.STAFF
        channel_msg = ChannelMessage(
            channel=self.engine.channel.name,
            chat_id=group_id,
            chat_type="group",
            text=content,
            message_id=wecom_message_id,
            user_id=meta.get("sender_id"),
            user_name=meta.get("sender_name"),
            message_type=message_type,
            raw_payload=raw_payload,
            timestamp=created_at,
            # 重启补跑时把引用一起带回来，否则恢复的这轮又只剩一句"收到"。
            segments=_segments_from_meta(meta),
            # 语音转写标记同样要还原：模型上下文据此对数字保持警惕，与实时路径一致。
            voice_transcript=bool(meta.get("voice_transcript")),
        )

        async with engine_mod.SessionLocal() as session:
            recent_user_messages = (
                await self.engine._recent_user_messages(
                    session, service_case_id, exclude_message_id=inbound_id
                )
                if service_case.ai_introduced_at is not None and not is_staff
                else []
            )

        return {
            "message": channel_msg,
            "service_case_id": service_case_id,
            "inbound_id": inbound_id,
            "trigger_id": inbound_id,
            "recent_user_messages": recent_user_messages,
            "purpose": purpose,
            "is_staff": is_staff,
            "sender_type": msg_row.sender_type,
            "analysis": analysis_json,
            "service_case_introduced": service_case.ai_introduced_at is not None,
            "should_send_introduction": should_send_introduction,
            "service_case_silenced": not service_case.ai_auto_reply_enabled,
            "resume_takeover_receipt": bool(group_meta.get("resume_receipt")),
            "group_control_command": group_control_command,
            "explicit_bot_query_text": explicit_bot_query_text,
            "should_send_questionnaire": should_send_questionnaire,
        }

    # ---------- 共享尾部 ----------

    async def _insight(self, state: EngineState) -> dict[str, Any]:
        """对应 462–515 / 620–638：消息多信号（风险/意图）分析 + 转人工决策（两入口汇流）。"""
        message: ChannelMessage = state["message"]
        service_case_id = state.get("service_case_id")
        inbound_id = state.get("inbound_id")
        is_staff = state.get("is_staff")
        analysis = state.get("analysis")
        image_analysis_content = state.get("image_analysis_content")
        recent_user_messages = state.get("recent_user_messages") or []
        agent_run_id = await self._ensure_reply_run(state)

        needs_handoff = False
        case_risk_level = state.get("case_risk_level")

        if service_case_id and inbound_id and not is_staff:
            # If the process died after committing intervention analysis but before
            # this node's checkpoint, reuse the durable result instead of paying
            # for and potentially diverging on a second model call.
            async with engine_mod.SessionLocal() as session:
                if hasattr(session, "get") and hasattr(session, "scalar"):
                    persisted_inbound = await session.get(Message, inbound_id)
                    persisted_analysis = (
                        dict(persisted_inbound.analysis_json)
                        if persisted_inbound
                        and isinstance(persisted_inbound.analysis_json, dict)
                        else {}
                    )
                    persisted_case = await session.scalar(
                        select_active_service_cases(
                            ServiceCase.id == service_case_id
                        )
                    )
                    persisted_risk_level = (
                        persisted_case.risk_level if persisted_case else None
                    )
                else:
                    persisted_analysis = {}
                    persisted_risk_level = None
            if "requires_human_intervention" in persisted_analysis:
                analysis = persisted_analysis
                case_risk_level = persisted_risk_level
                health_handoff = _requires_health_handoff(
                    analysis,
                    case_risk_level,
                )
                needs_handoff = bool(
                    analysis.get("requires_human_intervention")
                ) or health_handoff
                return {
                    "analysis": analysis,
                    "case_risk_level": case_risk_level,
                    "needs_handoff": needs_handoff,
                    "agent_run_id": agent_run_id,
                }
            if image_analysis_content is not None and not image_analysis_content:
                logger.info(
                    "RanrenAI skip intervention/outcome analysis for image-only message chat_id=%s trigger_id=%s service_case_id=%s",
                    message.chat_id, inbound_id, service_case_id,
                )
            else:
                analysis_input = image_analysis_content or message.text
                image_prompt = analysis.get("image_prompt") if isinstance(analysis, dict) else None
                analysis = await engine_mod.analyze_message_insight(
                    source="chat_message",
                    content=analysis_input,
                    context={
                        "service_case_id": service_case_id,
                        "message_id": inbound_id,
                        "group_id": message.chat_id,
                        "current_message": analysis_input,
                        "recent_user_messages": _insight_recent_messages(
                            recent_user_messages
                        ),
                    },
                )
                if image_prompt:
                    analysis["image_prompt"] = image_prompt
                case_risk_level = await self.engine._store_message_insight(
                    service_case_id=service_case_id,
                    inbound_id=inbound_id,
                    analysis=analysis,
                )
                health_handoff = _requires_health_handoff(analysis, case_risk_level)
                needs_handoff = bool(analysis.get("requires_human_intervention")) or health_handoff

        result: dict[str, Any] = {
            "analysis": analysis,
            "case_risk_level": case_risk_level,
            "needs_handoff": needs_handoff,
            "agent_run_id": agent_run_id,
        }
        if not service_case_id:
            result["_completion_reason"] = "no_active_case"
        elif state.get("purpose") != MessagePurpose.CHAT:
            result["_completion_reason"] = "non_chat_message"
        return result

    async def _handoff(self, state: EngineState) -> dict[str, Any]:
        """对应 496–515：30min 未 @ 过则 @班主任转人工；否则跳过刷屏。两分支均结束。"""
        message: ChannelMessage = state["message"]
        started_at = state["started_at"]
        service_case_id = state.get("service_case_id")
        inbound_id = state.get("inbound_id")
        agent_run_id = await self._ensure_reply_run(state)
        if await self._outbound_exists_for_trigger(inbound_id):
            logger.info(
                "RanrenAI skip resumed handoff because outbound already exists trigger_id=%s service_case_id=%s",
                inbound_id,
                service_case_id,
            )
            return {
                "agent_run_id": agent_run_id,
                "_completion_reason": "handoff_already_recorded",
            }
        if not await self.engine._recently_handed_off(service_case_id):
            await self.engine._reply_human_handoff(message, service_case_id=service_case_id, trigger_id=inbound_id)
            await self.engine._mark_handoff(service_case_id)
            logger.info(
                "RanrenAI handed off to staff chat_id=%s trigger_id=%s service_case_id=%s total_elapsed_ms=%.1f",
                message.chat_id, inbound_id, service_case_id,
                (time.monotonic() - started_at) * 1000,
            )
            outcome = "human_handoff"
        else:
            logger.info(
                "RanrenAI skip duplicate handoff within 30min chat_id=%s trigger_id=%s service_case_id=%s",
                message.chat_id, inbound_id, service_case_id,
            )
            outcome = "handoff_throttled"
        return {
            "agent_run_id": agent_run_id,
            "_completion_reason": outcome,
        }

    # ---------- 回复尾部（直接平铺在 EngineGraph） ----------

    async def _reply_prep(self, state: EngineState) -> dict[str, Any]:
        """对应 401–438 + 494–501：开关/存活检查 + recent；任一早退 → _halt。"""
        message: ChannelMessage = state["message"]
        service_case_id = state.get("service_case_id")
        trigger_id = state.get("trigger_id")
        agent_run_id = await self._ensure_reply_run(state)
        service_case: ServiceCase | None = None
        async with engine_mod.SessionLocal() as session:
            if service_case_id:
                stmt = select_active_service_cases(ServiceCase.id == service_case_id).options(
                    selectinload(ServiceCase.user)
                )
                service_case = await session.scalar(stmt)
                if service_case is None:
                    logger.info(
                        "RanrenAI skip reply because service case is inactive service_case_id=%s",
                        service_case_id,
                    )
                    await self.engine._retire_pending_outbound(
                        trigger_id=trigger_id,
                        reason="recovered reply suppressed because the service case is inactive",
                    )
                    return {
                        "_halt": True,
                        "agent_run_id": agent_run_id,
                        "_completion_reason": "inactive_case",
                    }
            else:
                service_case = None

            if not await engine_mod.get_ai_reply_enabled(session):
                logger.info(
                    "RanrenAI skip AI reply because globally disabled chat_id=%s trigger_id=%s service_case_id=%s",
                    message.chat_id, trigger_id, service_case_id,
                )
                await self.engine._retire_pending_outbound(
                    trigger_id=trigger_id,
                    reason="recovered reply suppressed because global AI reply is disabled",
                )
                return {
                    "_halt": True,
                    "agent_run_id": agent_run_id,
                    "_completion_reason": "ai_disabled",
                }
            if service_case and not service_case.ai_auto_reply_enabled:
                logger.info(
                    "RanrenAI skip AI reply because case auto-reply disabled chat_id=%s trigger_id=%s service_case_id=%s",
                    message.chat_id, trigger_id, service_case_id,
                )
                await self.engine._retire_pending_outbound(
                    trigger_id=trigger_id,
                    reason="recovered reply suppressed because case auto-reply is disabled",
                )
                return {
                    "_halt": True,
                    "agent_run_id": agent_run_id,
                    "_completion_reason": "case_auto_reply_disabled",
                }

            recent_user_messages = (
                await self.engine._recent_user_messages(
                    session, service_case.id, exclude_message_id=trigger_id
                )
                if service_case
                else []
            )

        if not (service_case_id and service_case):
            logger.info(
                "RanrenAI skip Agent reply because no active service case chat_id=%s trigger_id=%s service_case_id=%s",
                message.chat_id, trigger_id, service_case_id,
            )
            return {
                "_halt": True,
                "recent_user_messages": recent_user_messages,
                "agent_run_id": agent_run_id,
                "_completion_reason": "no_active_case",
            }

        if await self.engine._staff_replied_after(
            service_case_id=service_case_id,
            trigger_id=trigger_id,
        ):
            logger.info(
                "RanrenAI skip Agent generation because staff already replied chat_id=%s trigger_id=%s service_case_id=%s",
                message.chat_id,
                trigger_id,
                service_case_id,
            )
            await self.engine._retire_pending_outbound(
                trigger_id=trigger_id,
                reason="recovered reply suppressed because staff already replied",
            )
            return {
                "_halt": True,
                "agent_run_id": agent_run_id,
                "_completion_reason": "staff_preempted",
            }
        if await self.engine._has_newer_inbound_message(
            service_case_id=service_case_id,
            trigger_id=trigger_id,
        ):
            logger.info(
                "RanrenAI skip stale Agent generation chat_id=%s trigger_id=%s service_case_id=%s",
                message.chat_id,
                trigger_id,
                service_case_id,
            )
            await self.engine._retire_pending_outbound(
                trigger_id=trigger_id,
                reason="recovered reply suppressed because a newer user message exists",
            )
            return {
                "_halt": True,
                "agent_run_id": agent_run_id,
                "_completion_reason": "stale_reply_suppressed",
            }
        if (
            self.engine.has_pending_newer_message
            and await self.engine.has_pending_newer_message(message)
        ):
            logger.info(
                "RanrenAI skip Agent generation because newer raw messages are queued chat_id=%s trigger_id=%s service_case_id=%s",
                message.chat_id,
                trigger_id,
                service_case_id,
            )
            await self.engine._retire_pending_outbound(
                trigger_id=trigger_id,
                reason="recovered reply suppressed because a newer raw message is queued",
            )
            return {
                "_halt": True,
                "agent_run_id": agent_run_id,
                "_completion_reason": "newer_message_queued",
            }

        return {
            "recent_user_messages": recent_user_messages,
            "agent_run_id": agent_run_id,
        }

    async def _reply_generate(self, state: EngineState) -> dict[str, Any]:
        """对应 440–493：generate_agent_reply + 异常处理。通用异常 → _halt(丢弃)。"""
        message: ChannelMessage = state["message"]
        service_case_id = state.get("service_case_id")
        trigger_id = state.get("trigger_id")
        analysis = state.get("analysis")
        recent_user_messages = state.get("recent_user_messages") or []
        settings = get_settings()
        user_query = await engine_mod._build_current_query(analysis, message)
        knowledge_query = _knowledge_query_from_analysis(analysis)
        generation_started_at = time.monotonic()
        try:
            agent_kwargs: dict[str, Any] = {
                "service_case_id": service_case_id,
                "trigger_id": trigger_id,
                "user_query": user_query,
                "knowledge_query": knowledge_query,
                "query_speaker": _current_query_speaker(analysis),
                "recent_messages": recent_user_messages,
                "analysis": analysis,
                "channel": message.channel,
                "group_id": message.chat_id if message.chat_type == "group" else None,
                "bot_user_id": _bot_user_id_from_message(message),
                "settings": settings,
                "agent_run_id": state.get("agent_run_id"),
            }
            case_lang = state.get("service_case_language") or getattr(state.get("service_case"), "language", None)
            if case_lang and case_lang != "zh":
                agent_kwargs["language"] = case_lang
            if self.checkpointer is not None and state.get("checkpoint_thread_id"):
                agent_kwargs["checkpoint_thread_id"] = state["checkpoint_thread_id"]
            agent_result = await engine_mod.generate_agent_reply(
                **agent_kwargs,
            )
            reply = agent_result.reply
            agent_meta = {
                "enabled": True,
                "context_plan": analysis.get("agent_context_plan") if isinstance(analysis, dict) else None,
                "tool_trace": agent_result.tool_trace,
                "data_tool_failed": agent_result.data_tool_failed,
                "knowledge": _knowledge_observation(
                    knowledge_query=knowledge_query,
                    user_query=user_query,
                    hit_count=agent_result.knowledge_hit_count,
                    hits=agent_result.knowledge_hits,
                    query_used=agent_result.knowledge_query_used,
                    fallback_used=agent_result.knowledge_fallback_used,
                    primary_hit_count=agent_result.knowledge_primary_hit_count,
                ),
            }
            info_gap = _build_agent_info_gap(
                query=user_query,
                tool_trace=agent_result.tool_trace,
                data_unavailable=agent_result.data_tool_failed,
            )
            if info_gap is not None:
                agent_meta["info_gap"] = info_gap
            outbound_analysis = {"agent": agent_meta}
        except engine_mod.AgentDataUnavailableError as exc:
            logger.warning(
                "Agent data unavailable; using safe fixed reply service_case_id=%s trigger_id=%s",
                service_case_id, trigger_id,
            )
            reply = str(exc)
            outbound_analysis = {
                "agent": {
                    "enabled": True,
                    "data_unavailable": True,
                    "info_gap": _build_agent_info_gap(
                        query=user_query, tool_trace=None, data_unavailable=True
                    ),
                    # 走安全兜底也要留下检索决策：召回条数拿不到（agent 结果被异常替掉），决策本身仍有效。
                    "knowledge": _knowledge_observation(
                        knowledge_query=knowledge_query,
                        user_query=user_query,
                    ),
                }
            }
        except Exception as exc:
            logger.exception(
                "Agent reply failed; discarding without sending service_case_id=%s trigger_id=%s",
                service_case_id, trigger_id,
            )
            try:
                await self.engine._record_agent_reply_failure(
                    trigger_id=trigger_id,
                    error=exc,
                    elapsed_ms=int(
                        (time.monotonic() - generation_started_at) * 1000
                    ),
                    timeout_seconds=(
                        float(settings.agent_timeout_seconds)
                        if type(exc).__name__ == "TimeoutError"
                        else None
                    ),
                )
            except Exception:
                logger.exception(
                    "Failed to persist Agent reply failure service_case_id=%s trigger_id=%s",
                    service_case_id,
                    trigger_id,
                )
            return {
                "_halt": True,
                "_completion_status": _REPLY_STATUS_RETRYABLE_FAILURE,
                "_completion_reason": "agent_generation_failed",
            }
        return {"reply": reply, "outbound_analysis": outbound_analysis}

    async def _reply_guards(self, state: EngineState) -> dict[str, Any]:
        """对应 503–526 + 572–578：no_reply/净化/不拆台/should_send;should_send False → 丢弃日志。"""
        message: ChannelMessage = state["message"]
        service_case_id = state.get("service_case_id")
        trigger_id = state.get("trigger_id")
        analysis = state.get("analysis")
        reply = state.get("reply") or ""

        if reply and _reply_is_no_reply(reply):
            logger.info(
                "RanrenAI skip AI reply because model chose no-reply chat_id=%s trigger_id=%s service_case_id=%s",
                message.chat_id, trigger_id, service_case_id,
            )
            await self.engine._retire_pending_outbound(
                trigger_id=trigger_id,
                reason="recovered reply superseded because the model chose no-reply",
            )
            return {
                "_halt": True,
                "_completion_reason": "intentional_no_reply",
            }

        should_send = False
        if reply:
            reply = _sanitize_ai_reply(reply, self.engine.settings)
            if await self.engine._staff_replied_after(service_case_id=service_case_id, trigger_id=trigger_id):
                logger.info(
                    "RanrenAI skip AI reply because staff already replied chat_id=%s trigger_id=%s service_case_id=%s",
                    message.chat_id, trigger_id, service_case_id,
                )
                await self.engine._retire_pending_outbound(
                    trigger_id=trigger_id,
                    reason="recovered reply suppressed because staff already replied",
                )
                return {
                    "_halt": True,
                    "_completion_reason": "staff_preempted",
                }
            async with engine_mod.SessionLocal() as session:
                service_case = (
                    await session.scalar(
                        select_active_service_cases(
                            ServiceCase.id == service_case_id
                        )
                    )
                    if service_case_id
                    else None
                )
                should_send = await self.engine._should_send_ai_reply(session, service_case, analysis, message)

        if not should_send:
            logger.info(
                "RanrenAI discard AI reply because should_send is false chat_id=%s trigger_id=%s service_case_id=%s",
                message.chat_id, trigger_id, service_case_id,
            )
            await self.engine._retire_pending_outbound(
                trigger_id=trigger_id,
                reason="recovered reply suppressed by the final send policy",
            )
        return {
            "reply": reply,
            "should_send": should_send,
            "_halt": not should_send,
            **(
                {"_completion_reason": "send_policy_suppressed"}
                if not should_send
                else {}
            ),
        }

    async def _reply_send(self, state: EngineState) -> dict[str, Any]:
        """对应 528–570：新消息/队列守卫 + record-first 发送。"""
        message: ChannelMessage = state["message"]
        service_case_id = state.get("service_case_id")
        trigger_id = state.get("trigger_id")
        reply = state.get("reply")
        outbound_analysis = state.get("outbound_analysis")

        async with engine_mod.SessionLocal() as session:
            globally_enabled = await engine_mod.get_ai_reply_enabled(session)
            service_case = (
                await session.scalar(
                    select_active_service_cases(
                        ServiceCase.id == service_case_id
                    )
                )
                if service_case_id
                else None
            )
        if not globally_enabled:
            await self.engine._retire_pending_outbound(
                trigger_id=trigger_id,
                reason="recovered reply suppressed because global AI reply is disabled",
            )
            return {
                "_completion_reason": "ai_disabled_before_send",
            }
        if service_case_id and (
            service_case is None or not service_case.ai_auto_reply_enabled
        ):
            await self.engine._retire_pending_outbound(
                trigger_id=trigger_id,
                reason="recovered reply suppressed because the service case cannot auto-reply",
            )
            return {
                "_completion_reason": "case_disabled_before_send",
            }
        if await self.engine._staff_replied_after(
            service_case_id=service_case_id,
            trigger_id=trigger_id,
        ):
            await self.engine._retire_pending_outbound(
                trigger_id=trigger_id,
                reason="recovered reply suppressed because staff already replied",
            )
            return {
                "_completion_reason": "staff_preempted",
            }
        if await self._outbound_exists_for_trigger(trigger_id):
            logger.info(
                "RanrenAI skip resumed AI reply because outbound already exists trigger_id=%s service_case_id=%s",
                trigger_id,
                service_case_id,
            )
            return {
                "_completion_reason": "reply_already_recorded",
            }
        if await self.engine._has_newer_inbound_message(service_case_id=service_case_id, trigger_id=trigger_id):
            logger.info(
                "RanrenAI skip stale AI reply chat_id=%s trigger_id=%s service_case_id=%s",
                message.chat_id, trigger_id, service_case_id,
            )
            await self.engine._retire_pending_outbound(
                trigger_id=trigger_id,
                reason="recovered reply suppressed because a newer user message exists",
            )
            return {
                "_completion_reason": "stale_reply_suppressed",
            }
        if self.engine.has_pending_newer_message and await self.engine.has_pending_newer_message(message):
            logger.info(
                "RanrenAI skip AI reply because newer raw messages are queued chat_id=%s trigger_id=%s service_case_id=%s",
                message.chat_id, trigger_id, service_case_id,
            )
            await self.engine._retire_pending_outbound(
                trigger_id=trigger_id,
                reason="recovered reply suppressed because a newer raw message is queued",
            )
            return {
                "_completion_reason": "newer_message_queued",
            }
        send_started_at = time.monotonic()
        try:
            result = await self.engine._send_and_record(
                message=message,
                service_case_id=service_case_id,
                content=reply,
                trigger_id=trigger_id,
                analysis_json=outbound_analysis,
                send=lambda: self.engine.channel.send_text(
                    message.chat_id, reply,
                    reply_to=message.message_id, reply_context=message.raw_payload,
                ),
            )
        except Exception as exc:
            delivery_outcome = await self._reply_delivery_outcome(trigger_id)
            await complete_agent_run(
                agent_run_id=state.get("agent_run_id"),
                status=AGENT_RUN_FAILED,
                outcome=delivery_outcome,
                latency_ms=int(
                    (time.monotonic() - state.get("started_at", send_started_at))
                    * 1000
                ),
                error_type=type(exc).__name__,
            )
            raise
        send_elapsed_ms = (time.monotonic() - send_started_at) * 1000
        logger.info(
            "RanrenAI AI reply sent chat_id=%s trigger_id=%s success=%s channel_message_id=%s send_elapsed_ms=%.1f",
            message.chat_id, trigger_id, result.success, result.message_id, send_elapsed_ms,
        )
        if result and result.success:
            outcome = "reply_sent"
        elif result and getattr(result, "delivery_uncertain", False):
            outcome = "send_unknown"
        else:
            outcome = "send_failed"
        return {
            "_completion_reason": outcome,
        }

    async def _reply_delivery_outcome(self, trigger_id: int | None) -> str:
        if trigger_id is None:
            return "send_failed"
        async with engine_mod.SessionLocal() as session:
            status = await session.scalar(
                select(Message.status)
                .where(
                    Message.direction == MessageDirection.OUTBOUND,
                    Message.trigger_id == trigger_id,
                )
                .order_by(Message.id.desc())
                .limit(1)
            )
        return "send_unknown" if status == MessageStatus.UNKNOWN else "send_failed"

    async def _finalize(self, state: EngineState) -> dict[str, Any]:
        """Persist one explicit business terminal after every successful graph path.

        A process interruption never reaches this node, so its inbound row stays
        processing/retryable and startup recovery may safely wake the same thread.
        """
        if state.get("_skip_finalization"):
            return {}
        inbound_id = (
            state.get("inbound_id")
            or state.get("trigger_id")
            or state.get("db_message_id")
        )
        if not inbound_id:
            return {}
        status = state.get("_completion_status") or _REPLY_STATUS_COMPLETED
        reason = state.get("_completion_reason") or (
            "no_reply" if state.get("_halt") else "workflow_completed"
        )
        failure_metadata: dict[str, Any] = {}
        async with engine_mod.SessionLocal() as session:
            # 少量纯单元测试使用只实现 add/flush 的轻量 session；真实
            # SQLAlchemy AsyncSession 始终具备 get()。
            if not hasattr(session, "get"):
                return {}
            inbound = await session.get(Message, inbound_id)
            if inbound is None or inbound.direction != MessageDirection.INBOUND:
                return {}
            updated_analysis = _with_reply_processing_status(
                inbound.analysis_json,
                status=status,
                reason=reason,
            )
            persisted_failure = updated_analysis.get("agent_reply_failure")
            if isinstance(persisted_failure, dict):
                failure_metadata = dict(persisted_failure)
            inbound.analysis_json = updated_analysis
            await session.commit()
        agent_run_id = state.get("agent_run_id")
        if agent_run_id is not None:
            outbound_analysis = state.get("outbound_analysis") or {}
            agent_metadata = (
                outbound_analysis.get("agent")
                if isinstance(outbound_analysis, dict)
                else {}
            )
            agent_metadata = (
                agent_metadata if isinstance(agent_metadata, dict) else {}
            )
            failed_outcomes = {
                "agent_generation_failed",
                "send_failed",
                "send_unknown",
            }
            run_metadata = _reply_run_metadata(agent_metadata)
            if failure_metadata:
                run_metadata["failure"] = failure_metadata
            await complete_agent_run(
                agent_run_id=agent_run_id,
                status=(
                    AGENT_RUN_FAILED
                    if status == _REPLY_STATUS_RETRYABLE_FAILURE
                    or reason in failed_outcomes
                    else AGENT_RUN_COMPLETED
                ),
                outcome=reason,
                latency_ms=int(
                    (time.monotonic() - state.get("started_at", time.monotonic()))
                    * 1000
                ),
                output_count=int(
                    reason
                    in {
                        "reply_sent",
                        "human_handoff",
                        "handoff_already_recorded",
                        "reply_already_recorded",
                    }
                ),
                error_type=(
                    str(failure_metadata.get("error_type") or "AgentGenerationError")
                    if reason == "agent_generation_failed"
                    else None
                ),
                metadata=run_metadata,
            )
        logger.info(
            "RanrenAI reply workflow finalized trigger_id=%s status=%s reason=%s",
            inbound_id,
            status,
            reason,
        )
        return {}


def _engine_node_dispatch(
    method_name: str,
):
    async def dispatch(
        state: EngineState,
        runtime: Runtime[_EngineGraphExecutionContext],
    ) -> dict[str, Any]:
        method = getattr(runtime.context.handler, method_name)
        return await method(state)

    dispatch.__name__ = f"{method_name}_dispatch"
    return dispatch


def _engine_route_dispatch(
    method_name: str,
):
    def dispatch(
        state: EngineState,
        runtime: Runtime[_EngineGraphExecutionContext],
    ) -> str:
        method = getattr(runtime.context.handler, method_name)
        return method(state)

    dispatch.__name__ = f"{method_name}_dispatch"
    return dispatch


def _compile_engine_graph(checkpointer: BaseCheckpointSaver | None):
    builder = StateGraph(
        EngineState,
        context_schema=_EngineGraphExecutionContext,
    )
    for node_name in (
        "setup",
        "group_introduction",
        "group_command_reply",
        "attachments",
        "file_ack",
        "staff_gate",
        "questionnaire",
        "reprocess_head",
        "insight",
        "handoff",
        "reply_prep",
        "reply_generate",
        "reply_guards",
        "reply_send",
        "finalize",
    ):
        builder.add_node(
            node_name,
            _engine_node_dispatch(f"_{node_name}"),
        )

    builder.add_conditional_edges(
        START,
        _engine_route_dispatch("_route_start"),
        {
            "setup": "setup",
            "reprocess_head": "reprocess_head",
            "reply_prep": "reply_prep",
        },
    )
    builder.add_conditional_edges(
        "setup",
        _engine_route_dispatch("_route_after_setup"),
        {
            "group_introduction": "group_introduction",
            "group_command_reply": "group_command_reply",
            "attachments": "attachments",
            END: "finalize",
        },
    )
    builder.add_edge("group_introduction", "finalize")
    builder.add_edge("group_command_reply", "finalize")
    builder.add_conditional_edges(
        "attachments",
        _engine_route_dispatch("_route_after_attachments"),
        {"file_ack": "file_ack", "staff_gate": "staff_gate", END: "finalize"},
    )
    builder.add_edge("file_ack", "finalize")
    builder.add_conditional_edges(
        "staff_gate",
        _engine_route_dispatch("_route_after_staff_gate"),
        {"questionnaire": "questionnaire", "insight": "insight", END: "finalize"},
    )
    builder.add_edge("questionnaire", "finalize")
    builder.add_conditional_edges(
        "reprocess_head",
        _engine_route_dispatch("_route_after_reprocess_head"),
        {
            "group_introduction": "group_introduction",
            "group_command_reply": "group_command_reply",
            "attachments": "attachments",
            END: "finalize",
        },
    )
    builder.add_conditional_edges(
        "insight",
        _engine_route_dispatch("_route_after_insight"),
        {"handoff": "handoff", "reply_prep": "reply_prep", END: "finalize"},
    )
    builder.add_edge("handoff", "finalize")
    builder.add_conditional_edges(
        "reply_prep",
        _engine_route_dispatch("_route_after_reply_prep"),
        {"reply_generate": "reply_generate", END: "finalize"},
    )
    builder.add_conditional_edges(
        "reply_generate",
        _engine_route_dispatch("_route_after_reply_generate"),
        {"reply_guards": "reply_guards", END: "finalize"},
    )
    builder.add_conditional_edges(
        "reply_guards",
        _engine_route_dispatch("_route_after_reply_guards"),
        {"reply_send": "reply_send", END: "finalize"},
    )
    builder.add_edge("reply_send", "finalize")
    builder.add_edge("finalize", END)
    return builder.compile(checkpointer=checkpointer)


def _get_engine_compiled_graph(checkpointer: BaseCheckpointSaver | None):
    global _ENGINE_GRAPH_CACHE
    if (
        _ENGINE_GRAPH_CACHE is None
        or _ENGINE_GRAPH_CACHE[0] is not checkpointer
    ):
        _ENGINE_GRAPH_CACHE = (
            checkpointer,
            _compile_engine_graph(checkpointer),
        )
    return _ENGINE_GRAPH_CACHE[1]


def start_engine_graph_runtime(
    checkpointer: BaseCheckpointSaver | None = None,
) -> None:
    """Compile the shared message-orchestration topology once at startup."""
    _get_engine_compiled_graph(checkpointer)
    logger.info("Shared LangGraph Engine topology compiled")


def stop_engine_graph_runtime() -> None:
    global _ENGINE_GRAPH_CACHE
    _ENGINE_GRAPH_CACHE = None


async def run_reply_with_ai(
    engine: "RanrenAIEngine",
    message: ChannelMessage,
    *,
    service_case_id: int | None,
    analysis: dict | None,
    trigger_id: int | None = None,
) -> None:
    """Direct reply-only entry using the same persisted EngineGraph."""
    checkpointer = get_agent_checkpointer()
    thread_id = _checkpoint_thread_id_for_message(
        message,
        workflow="reply",
        fallback_suffix=f"trigger:{trigger_id or 0}",
    )
    state: EngineState = {
        "message": message,
        "entry_mode": "reply_only",
        "started_at": time.monotonic(),
        "service_case_id": service_case_id,
        "analysis": analysis,
        "trigger_id": trigger_id,
        "checkpoint_thread_id": thread_id,
    }
    await EngineGraph(engine, checkpointer=checkpointer).run(
        state,
        run_config=checkpoint_config(thread_id) if checkpointer is not None else None,
    )


async def run_handle_message(engine: "RanrenAIEngine", message: ChannelMessage) -> None:
    """fresh 入口薄封装：构造初始 state 后跑图。"""
    checkpointer = get_agent_checkpointer()
    thread_id = _checkpoint_thread_id_for_message(message, workflow="reply")
    state: EngineState = {
        "message": message,
        "entry_mode": "fresh",
        "started_at": time.monotonic(),
        "checkpoint_thread_id": thread_id,
        "should_send_questionnaire": is_questionnaire_command(message.text),
        "group_control_command": _group_bot_control_command(message),
        "explicit_bot_query_text": _explicit_bot_query_text(message),
    }
    await EngineGraph(engine, checkpointer=checkpointer).run(
        state,
        run_config=checkpoint_config(thread_id) if checkpointer is not None else None,
    )


async def run_reprocess_message(
    engine: "RanrenAIEngine",
    db_message: Message,
    *,
    recovery_started_at: datetime | None = None,
) -> None:
    """reprocess 入口薄封装：构造初始 state 后跑图。"""
    checkpointer = get_agent_checkpointer()
    meta = (
        db_message.analysis_json.get("_qiwe_meta", {})
        if isinstance(db_message.analysis_json, dict)
        else {}
    )
    fallback = _checkpoint_fallback_key(
        user_id=str(meta.get("sender_id") or ""),
        timestamp=int(meta.get("timestamp") or int(db_message.created_at.timestamp())),
        message_type=db_message.message_type,
        text=db_message.content,
    )
    thread_id = build_checkpoint_thread_id(
        workflow="reply",
        channel=engine.channel.name,
        chat_id=db_message.group_id,
        message_id=db_message.wecom_message_id,
        fallback_key=fallback,
    )
    state: EngineState = {
        "db_message_id": db_message.id,
        "entry_mode": "reprocess",
        "started_at": time.monotonic(),
        "checkpoint_thread_id": thread_id,
        "recovery_started_at": recovery_started_at,
    }
    await EngineGraph(engine, checkpointer=checkpointer).run(
        state,
        run_config=checkpoint_config(thread_id) if checkpointer is not None else None,
    )


def _checkpoint_thread_id_for_message(
    message: ChannelMessage,
    *,
    workflow: str,
    fallback_suffix: str = "",
) -> str:
    fallback = _checkpoint_fallback_key(
        user_id=message.user_id or "",
        timestamp=int(message.timestamp.timestamp()),
        message_type=message.message_type,
        text=message.text,
    )
    if fallback_suffix:
        fallback = f"{fallback}\x1f{fallback_suffix}"
    return build_checkpoint_thread_id(
        workflow=workflow,
        channel=message.channel,
        chat_id=message.chat_id,
        message_id=message.message_id,
        fallback_key=fallback,
    )


def _checkpoint_fallback_key(
    *,
    user_id: str,
    timestamp: int,
    message_type: str,
    text: str,
) -> str:
    return "\x1f".join(
        (
            user_id,
            str(timestamp),
            message_type,
            text,
        )
    )
