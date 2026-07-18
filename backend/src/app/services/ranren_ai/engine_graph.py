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
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, TypedDict

from langgraph.graph import END, START, StateGraph
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
from app.models.service_case import ServiceCase
from app.models.user import User
from app.services.channels.base import ChannelMessage
# 通过 engine 模块引用 SessionLocal / analyze_intervention / generate_agent_reply /
# get_ai_reply_enabled / AgentDataUnavailableError：测试把补丁打在 engine 模块上
# （conftest 全局 + 个别用例 per-test），经此引用才能命中，行为与旧 handle_message 一致。
from app.services.ranren_ai import engine as engine_mod
from app.services.ranren_ai.engine import (
    _bot_user_id_from_message,
    _build_agent_info_gap,
    _current_query_body,
    _current_query_speaker,
    _explicit_bot_query_text,
    _group_bot_control_command,
    _message_already_processed_or_claimed,
    _message_mentions_bot,
    _reply_is_no_reply,
    _requires_health_handoff,
    _sanitize_ai_reply,
)
from app.services.service_case_queries import select_active_service_cases

if TYPE_CHECKING:
    from app.services.ranren_ai.engine import RanrenAIEngine

logger = logging.getLogger("uvicorn.error")


class EngineState(TypedDict, total=False):
    # 输入
    message: ChannelMessage
    db_message: Message  # 仅 reprocess 入口
    entry_mode: str  # "fresh" | "reprocess"
    started_at: float
    # 计算态（把原 handle_message 的局部量摊平；节点覆盖式更新）
    should_send_questionnaire: bool
    group_control_command: str | None
    explicit_bot_query_text: str | None
    is_staff: bool
    sender_type: Any
    service_case_id: int | None
    service_case_for_command: Any
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
    _halt: bool  # reprocess_head 守卫失败 → 直接结束


class EngineGraph:
    """按 entry_mode 承接 handle_message(fresh) 与 reprocess_message(reprocess)。"""

    def __init__(self, engine: "RanrenAIEngine") -> None:
        self.engine = engine
        self.graph = self._compile()

    def _compile(self):
        b = StateGraph(EngineState)
        b.add_node("setup", self._setup)
        b.add_node("group_command_reply", self._group_command_reply)
        b.add_node("attachments", self._attachments)
        b.add_node("file_ack", self._file_ack)
        b.add_node("staff_gate", self._staff_gate)
        b.add_node("questionnaire", self._questionnaire)
        b.add_node("reprocess_head", self._reprocess_head)
        b.add_node("risk", self._risk)
        b.add_node("handoff", self._handoff)
        # reply 子图以「子图即节点」的原生形态挂载：LangGraph 按同名键在父/子 state 间通信
        # （message/service_case_id/analysis/trigger_id 均已在父 state 就位）。异常照旧冒出 ainvoke。
        b.add_node("reply", ReplyGraph(self.engine).graph)

        b.add_conditional_edges(
            START, self._route_start,
            {"setup": "setup", "reprocess_head": "reprocess_head"},
        )
        b.add_conditional_edges(
            "setup", self._route_after_setup,
            {"group_command_reply": "group_command_reply", "attachments": "attachments"},
        )
        b.add_edge("group_command_reply", END)
        b.add_conditional_edges(
            "attachments", self._route_after_attachments,
            {"file_ack": "file_ack", "staff_gate": "staff_gate", END: END},
        )
        b.add_edge("file_ack", END)
        b.add_conditional_edges(
            "staff_gate", self._route_after_staff_gate,
            {"questionnaire": "questionnaire", "risk": "risk", END: END},
        )
        b.add_edge("questionnaire", END)
        b.add_conditional_edges(
            "reprocess_head", self._route_after_reprocess_head,
            {"risk": "risk", END: END},
        )
        b.add_conditional_edges(
            "risk", self._route_after_risk,
            {"handoff": "handoff", "reply": "reply", END: END},
        )
        b.add_edge("handoff", END)
        b.add_edge("reply", END)
        return b.compile()

    async def run(self, state: EngineState) -> None:
        await self.graph.ainvoke(state)

    # ---------- 路由 ----------

    def _route_start(self, state: EngineState) -> str:
        return "reprocess_head" if state.get("entry_mode") == "reprocess" else "setup"

    def _route_after_setup(self, state: EngineState) -> str:
        return "group_command_reply" if state.get("group_control_command") else "attachments"

    def _route_after_attachments(self, state: EngineState) -> str:
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
        return "risk"

    def _route_after_reprocess_head(self, state: EngineState) -> str:
        return END if state.get("_halt") else "risk"

    def _route_after_risk(self, state: EngineState) -> str:
        if state.get("needs_handoff"):
            return "handoff"
        if not state.get("service_case_id"):
            return END
        if state.get("purpose") != MessagePurpose.CHAT:
            return END
        return "reply"

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
        service_case_for_command: ServiceCase | None = None
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
            if should_send_questionnaire and not service_case:
                service_case = await engine._create_case_for_message(session, message, is_staff=is_staff)

            if service_case:
                service_case_id = service_case.id
                service_case_for_command = service_case

                if group_control_command == "silence":
                    service_case.ai_auto_reply_enabled = False
                    session.add(service_case)
                    await session.flush()
                    logger.info(
                        "RanrenAI group command silenced bot chat_id=%s service_case_id=%s message_id=%s",
                        message.chat_id, service_case.id, message.message_id,
                    )
                elif group_control_command == "resume":
                    was_silenced = not bool(getattr(service_case, "ai_auto_reply_enabled", True))
                    service_case.ai_auto_reply_enabled = True
                    session.add(service_case)
                    await session.flush()
                    logger.info(
                        "RanrenAI group command resumed bot chat_id=%s service_case_id=%s message_id=%s was_silenced=%s",
                        message.chat_id, service_case.id, message.message_id, was_silenced,
                    )
                    resume_takeover_receipt = was_silenced
                service_case_silenced = not bool(getattr(service_case, "ai_auto_reply_enabled", True))

                if not is_staff and message.channel == "qiwe" and message.user_id:
                    case_user = None
                    if hasattr(session, "get"):
                        case_user = await session.get(User, service_case.user_id)
                    if case_user:
                        needs_binding = False
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
                            if message.user_name and case_user.name == "待填写问卷用户":
                                case_user.name = message.user_name
                            session.add(case_user)
                            await session.flush()

            if service_case and not should_send_questionnaire and not is_staff:
                recent_user_messages = await engine._recent_user_messages(session, service_case.id)

            purpose = MessagePurpose.CHAT

            meta = {
                "sender_id": message.user_id,
                "sender_name": message.user_name,
                "sender_type": sender_type.value,
                "timestamp": int(message.timestamp.timestamp()),
            }
            if explicit_bot_query_text:
                meta["explicit_bot_query_text"] = explicit_bot_query_text
            analysis = {"_qiwe_meta": meta}

            db_inbound = Message(
                service_case_id=service_case.id if service_case else None,
                group_id=message.chat_id,
                wecom_message_id=message.message_id,
                direction=MessageDirection.INBOUND,
                sender_type=sender_type,
                message_type=message.message_type,
                content=message.text,
                purpose=purpose,
                status=MessageStatus.RECEIVED,
                analysis_json=analysis,
            )
            session.add(db_inbound)
            await session.flush()
            inbound_id = db_inbound.id
            await session.commit()
            logger.info(
                "RanrenAI inbound stored channel=%s chat_id=%s sender_type=%s message_id=%s db_message_id=%s service_case_id=%s command=%s elapsed_ms=%.1f",
                message.channel, message.chat_id, sender_type, message.message_id,
                inbound_id, service_case_id, should_send_questionnaire,
                (time.monotonic() - started_at) * 1000,
            )

        return {
            "is_staff": is_staff,
            "sender_type": sender_type,
            "service_case_id": service_case_id,
            "service_case_for_command": service_case_for_command,
            "service_case_silenced": service_case_silenced,
            "resume_takeover_receipt": resume_takeover_receipt,
            "analysis": analysis,
            "inbound_id": inbound_id,
            "trigger_id": inbound_id,
            "recent_user_messages": recent_user_messages,
            "purpose": purpose,
        }

    async def _group_command_reply(self, state: EngineState) -> dict[str, Any]:
        """对应 345–371：resume 回执（静默解除时），command 一律留痕不回复后结束。"""
        message: ChannelMessage = state["message"]
        started_at = state["started_at"]
        service_case_id = state.get("service_case_id")
        inbound_id = state.get("inbound_id")
        group_control_command = state.get("group_control_command")
        if state.get("resume_takeover_receipt"):
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
        analysis, image_analysis_content, file_processed, non_text_stored_only = (
            await self.engine._process_inbound_attachments(
                message=message,
                inbound_id=state.get("inbound_id"),
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
        if not state.get("is_staff"):
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
        await self.engine._reply_questionnaire_link(
            message, state.get("service_case_for_command"), trigger_id=inbound_id
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
        db_message: Message = state["db_message"]
        inbound_id = db_message.id
        service_case_id = db_message.service_case_id
        if not service_case_id:
            logger.warning("reprocess_message: message %s has no service_case_id", inbound_id)
            return {"_halt": True}

        async with engine_mod.SessionLocal() as session:
            stmt = select(Message).where(Message.id == inbound_id).with_for_update()
            msg_row = await session.scalar(stmt)
            if not msg_row:
                logger.warning("reprocess_message: Message %s not found in lock", inbound_id)
                return {"_halt": True}

            analysis_json = msg_row.analysis_json if isinstance(msg_row.analysis_json, dict) else {}
            if _message_already_processed_or_claimed(analysis_json):
                logger.info("reprocess_message: Skip message %s because it is already processed or claimed", inbound_id)
                return {"_halt": True}

            reply_exists = await session.scalar(
                select(Message.id).where(
                    Message.direction == MessageDirection.OUTBOUND,
                    Message.trigger_id == inbound_id,
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
                return {"_halt": True}

            service_case = await session.scalar(select_active_service_cases(ServiceCase.id == service_case_id))
            if not service_case or not service_case.ai_auto_reply_enabled:
                logger.info("reprocess_message: Skip message %s because case is inactive/silenced", inbound_id)
                return {"_halt": True}

            claimed = dict(analysis_json)
            claimed["_recovery_claim"] = {
                "claimed_at": datetime.now(timezone.utc).isoformat(),
                "reason": "startup_recovery",
            }
            msg_row.analysis_json = claimed
            await session.commit()

        meta = (db_message.analysis_json or {}).get("_qiwe_meta", {}) if db_message.analysis_json else {}
        channel_msg = ChannelMessage(
            channel=self.engine.channel.name,
            chat_id=db_message.group_id,
            chat_type="group",
            text=db_message.content,
            message_id=db_message.wecom_message_id,
            user_id=meta.get("sender_id"),
            user_name=meta.get("sender_name"),
            message_type=db_message.message_type,
            raw_payload=None,
            timestamp=db_message.created_at,
        )

        async with engine_mod.SessionLocal() as session:
            recent_user_messages = await self.engine._recent_user_messages(
                session, service_case_id, exclude_message_id=inbound_id
            )

        return {
            "message": channel_msg,
            "service_case_id": service_case_id,
            "inbound_id": inbound_id,
            "trigger_id": inbound_id,
            "recent_user_messages": recent_user_messages,
            "purpose": db_message.purpose,
            "is_staff": False,
            "analysis": None,
        }

    # ---------- 共享尾部 ----------

    async def _risk(self, state: EngineState) -> dict[str, Any]:
        """对应 462–515 / 620–638：风控/意图分析 + 转人工决策（两入口汇流）。"""
        message: ChannelMessage = state["message"]
        service_case_id = state.get("service_case_id")
        inbound_id = state.get("inbound_id")
        is_staff = state.get("is_staff")
        analysis = state.get("analysis")
        image_analysis_content = state.get("image_analysis_content")
        recent_user_messages = state.get("recent_user_messages") or []

        needs_handoff = False
        case_risk_level = state.get("case_risk_level")

        if service_case_id and inbound_id and not is_staff:
            if image_analysis_content is not None and not image_analysis_content:
                logger.info(
                    "RanrenAI skip intervention/outcome analysis for image-only message chat_id=%s trigger_id=%s service_case_id=%s",
                    message.chat_id, inbound_id, service_case_id,
                )
            else:
                analysis_input = image_analysis_content or message.text
                image_prompt = analysis.get("image_prompt") if isinstance(analysis, dict) else None
                analysis = await engine_mod.analyze_intervention(
                    source="chat_message",
                    content=analysis_input,
                    context={
                        "service_case_id": service_case_id,
                        "message_id": inbound_id,
                        "group_id": message.chat_id,
                        "current_message": analysis_input,
                        "recent_user_messages": recent_user_messages,
                    },
                )
                if image_prompt:
                    analysis["image_prompt"] = image_prompt
                case_risk_level = await self.engine._store_intervention_analysis(
                    service_case_id=service_case_id,
                    inbound_id=inbound_id,
                    analysis=analysis,
                )
                health_handoff = _requires_health_handoff(analysis, case_risk_level)
                needs_handoff = bool(analysis.get("requires_human_intervention")) or health_handoff

        return {
            "analysis": analysis,
            "case_risk_level": case_risk_level,
            "needs_handoff": needs_handoff,
        }

    async def _handoff(self, state: EngineState) -> dict[str, Any]:
        """对应 496–515：30min 未 @ 过则 @班主任转人工；否则跳过刷屏。两分支均结束。"""
        message: ChannelMessage = state["message"]
        started_at = state["started_at"]
        service_case_id = state.get("service_case_id")
        inbound_id = state.get("inbound_id")
        if not await self.engine._recently_handed_off(service_case_id):
            await self.engine._mark_handoff(service_case_id)
            await self.engine._reply_human_handoff(message, service_case_id=service_case_id, trigger_id=inbound_id)
            logger.info(
                "RanrenAI handed off to staff chat_id=%s trigger_id=%s service_case_id=%s total_elapsed_ms=%.1f",
                message.chat_id, inbound_id, service_case_id,
                (time.monotonic() - started_at) * 1000,
            )
        else:
            logger.info(
                "RanrenAI skip duplicate handoff within 30min chat_id=%s trigger_id=%s service_case_id=%s",
                message.chat_id, inbound_id, service_case_id,
            )
        return {}


class ReplyState(TypedDict, total=False):
    # 输入
    message: ChannelMessage
    service_case_id: int | None
    analysis: dict | None
    trigger_id: int | None
    # 计算态
    service_case: Any
    recent_user_messages: list
    reply: str
    outbound_analysis: dict | None
    should_send: bool
    _halt: bool


class ReplyGraph:
    """_reply_with_ai 的子图（Phase 2）：把回复半程的守卫拆成显式节点。

    prep（开关/存活）→ gen（生成+异常）→ guards（no_reply/净化/不拆台/should_send）→ send（新消息/队列守卫 + record-first 发送）。
    与旧 _reply_with_ai 逐段等价：早退→到 END 的条件边；异常语义不变——
    generate_agent_reply 的通用异常在 gen 内吞掉并到 END（方法整体正常返回），
    发送等非生成异常照旧冒出（send 节点抛出 → ainvoke 抛出 → 方法抛出）。
    """

    def __init__(self, engine: "RanrenAIEngine") -> None:
        self.engine = engine
        self.graph = self._compile()

    def _compile(self):
        b = StateGraph(ReplyState)
        b.add_node("prep", self._prep)
        b.add_node("gen", self._gen)
        b.add_node("guards", self._guards)
        b.add_node("send", self._send)
        b.add_edge(START, "prep")
        b.add_conditional_edges("prep", self._route_after_prep, {"gen": "gen", END: END})
        b.add_conditional_edges("gen", self._route_after_gen, {"guards": "guards", END: END})
        b.add_conditional_edges("guards", self._route_after_guards, {"send": "send", END: END})
        b.add_edge("send", END)
        return b.compile()

    async def run(self, state: ReplyState) -> None:
        await self.graph.ainvoke(state)

    # ---------- 路由 ----------

    def _route_after_prep(self, state: ReplyState) -> str:
        return "gen" if not state.get("_halt") else END

    def _route_after_gen(self, state: ReplyState) -> str:
        return "guards" if not state.get("_halt") else END

    def _route_after_guards(self, state: ReplyState) -> str:
        if state.get("_halt"):
            return END
        return "send" if state.get("should_send") else END

    # ---------- 节点 ----------

    async def _prep(self, state: ReplyState) -> dict[str, Any]:
        """对应 401–438 + 494–501：开关/存活检查 + recent；任一早退 → _halt。"""
        message: ChannelMessage = state["message"]
        service_case_id = state.get("service_case_id")
        trigger_id = state.get("trigger_id")
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
                    return {"_halt": True}
            else:
                service_case = None

            if not await engine_mod.get_ai_reply_enabled(session):
                logger.info(
                    "RanrenAI skip AI reply because globally disabled chat_id=%s trigger_id=%s service_case_id=%s",
                    message.chat_id, trigger_id, service_case_id,
                )
                return {"_halt": True}
            if service_case and not service_case.ai_auto_reply_enabled:
                logger.info(
                    "RanrenAI skip AI reply because case auto-reply disabled chat_id=%s trigger_id=%s service_case_id=%s",
                    message.chat_id, trigger_id, service_case_id,
                )
                return {"_halt": True}

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
            return {"_halt": True, "service_case": service_case, "recent_user_messages": recent_user_messages}

        return {"service_case": service_case, "recent_user_messages": recent_user_messages}

    async def _gen(self, state: ReplyState) -> dict[str, Any]:
        """对应 440–493：generate_agent_reply + 异常处理。通用异常 → _halt(丢弃)。"""
        message: ChannelMessage = state["message"]
        service_case_id = state.get("service_case_id")
        trigger_id = state.get("trigger_id")
        analysis = state.get("analysis")
        recent_user_messages = state.get("recent_user_messages") or []
        settings = get_settings()
        user_query = _current_query_body(analysis, message.text)
        try:
            agent_result = await engine_mod.generate_agent_reply(
                service_case_id=service_case_id,
                trigger_id=trigger_id,
                user_query=user_query,
                query_speaker=_current_query_speaker(analysis),
                recent_messages=recent_user_messages,
                analysis=analysis,
                channel=message.channel,
                group_id=message.chat_id if message.chat_type == "group" else None,
                bot_user_id=_bot_user_id_from_message(message),
                settings=settings,
            )
            reply = agent_result.reply
            agent_meta = {
                "enabled": True,
                "context_plan": analysis.get("agent_context_plan") if isinstance(analysis, dict) else None,
                "tool_trace": agent_result.tool_trace,
                "data_tool_failed": agent_result.data_tool_failed,
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
                )
            except Exception:
                logger.exception(
                    "Failed to persist Agent reply failure service_case_id=%s trigger_id=%s",
                    service_case_id,
                    trigger_id,
                )
            return {"_halt": True}
        return {"reply": reply, "outbound_analysis": outbound_analysis}

    async def _guards(self, state: ReplyState) -> dict[str, Any]:
        """对应 503–526 + 572–578：no_reply/净化/不拆台/should_send;should_send False → 丢弃日志。"""
        message: ChannelMessage = state["message"]
        service_case_id = state.get("service_case_id")
        trigger_id = state.get("trigger_id")
        analysis = state.get("analysis")
        service_case = state.get("service_case")
        reply = state.get("reply") or ""

        if reply and _reply_is_no_reply(reply):
            logger.info(
                "RanrenAI skip AI reply because model chose no-reply chat_id=%s trigger_id=%s service_case_id=%s",
                message.chat_id, trigger_id, service_case_id,
            )
            return {"_halt": True}

        should_send = False
        if reply:
            reply = _sanitize_ai_reply(reply, self.engine.settings)
            if await self.engine._staff_replied_after(service_case_id=service_case_id, trigger_id=trigger_id):
                logger.info(
                    "RanrenAI skip AI reply because staff already replied chat_id=%s trigger_id=%s service_case_id=%s",
                    message.chat_id, trigger_id, service_case_id,
                )
                return {"_halt": True}
            async with engine_mod.SessionLocal() as session:
                should_send = await self.engine._should_send_ai_reply(session, service_case, analysis, message)

        if not should_send:
            logger.info(
                "RanrenAI discard AI reply because should_send is false chat_id=%s trigger_id=%s service_case_id=%s",
                message.chat_id, trigger_id, service_case_id,
            )
        return {"reply": reply, "should_send": should_send}

    async def _send(self, state: ReplyState) -> dict[str, Any]:
        """对应 528–570：新消息/队列守卫 + record-first 发送。"""
        message: ChannelMessage = state["message"]
        service_case_id = state.get("service_case_id")
        trigger_id = state.get("trigger_id")
        reply = state.get("reply")
        outbound_analysis = state.get("outbound_analysis")

        if await self.engine._has_newer_inbound_message(service_case_id=service_case_id, trigger_id=trigger_id):
            logger.info(
                "RanrenAI skip stale AI reply chat_id=%s trigger_id=%s service_case_id=%s",
                message.chat_id, trigger_id, service_case_id,
            )
            return {}
        if self.engine.has_pending_newer_message and await self.engine.has_pending_newer_message(message):
            logger.info(
                "RanrenAI skip AI reply because newer raw messages are queued chat_id=%s trigger_id=%s service_case_id=%s",
                message.chat_id, trigger_id, service_case_id,
            )
            return {}
        send_started_at = time.monotonic()
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
        send_elapsed_ms = (time.monotonic() - send_started_at) * 1000
        logger.info(
            "RanrenAI AI reply sent chat_id=%s trigger_id=%s success=%s channel_message_id=%s send_elapsed_ms=%.1f",
            message.chat_id, trigger_id, result.success, result.message_id, send_elapsed_ms,
        )
        return {}


async def run_reply_with_ai(
    engine: "RanrenAIEngine",
    message: ChannelMessage,
    *,
    service_case_id: int | None,
    analysis: dict | None,
    trigger_id: int | None = None,
) -> None:
    """_reply_with_ai 薄封装：构造 ReplyState 后跑 reply 子图。"""
    state: ReplyState = {
        "message": message,
        "service_case_id": service_case_id,
        "analysis": analysis,
        "trigger_id": trigger_id,
    }
    await ReplyGraph(engine).run(state)


async def run_handle_message(engine: "RanrenAIEngine", message: ChannelMessage) -> None:
    """fresh 入口薄封装：构造初始 state 后跑图。"""
    state: EngineState = {
        "message": message,
        "entry_mode": "fresh",
        "started_at": time.monotonic(),
        "should_send_questionnaire": (
            "发送本群入营问卷" in message.text or "发送本群专属问卷" in message.text
        ),
        "group_control_command": _group_bot_control_command(message),
        "explicit_bot_query_text": _explicit_bot_query_text(message),
    }
    await EngineGraph(engine).run(state)


async def run_reprocess_message(engine: "RanrenAIEngine", db_message: Message) -> None:
    """reprocess 入口薄封装：构造初始 state 后跑图。"""
    state: EngineState = {
        "db_message": db_message,
        "entry_mode": "reprocess",
        "started_at": time.monotonic(),
    }
    await EngineGraph(engine).run(state)
