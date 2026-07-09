from __future__ import annotations

from html import escape
import httpx
import logging
import re
import secrets
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import selectinload

from app.core.config import Settings, get_settings
from app.db.session import SessionLocal
from app.models.message import (
    Message,
    MessageDirection,
    MessagePurpose,
    MessageSenderType,
    MessageStatus,
)
from app.models.service_case import ServiceCase
from app.models.user import User
from app.models.user_profile import UserProfile
from app.models.user_state_profile import UserStateProfile
from app.models.user_document import DocumentStatus, DocumentType, UserDocument
from app.services.ai_service import AIService
from app.services.agent import AgentDataUnavailableError, generate_agent_reply
from app.services.checkin_queries import recent_checkins
from app.services.channels.base import ChannelMessage, MessageChannel
from app.services.ranren_ai.attachments import (
    InboundAttachmentProcessor,
    _IMAGE_PARSE_FAILED_PROMPT,
    _image_description_is_available,
    _image_payload_items,
    _is_allowed_image_host,
    _strip_image_placeholders,
)
from app.services.content_policy import get_ai_reply_enabled
from app.services.short_link import get_or_create_short_link
from app.services.intervention_service import analyze_intervention, merge_case_insight
from app.services.outcome_engine import (
    apply_outcome_analysis_plan,
    extract_outcome_from_plan,
    prepare_outcome_extraction,
)
from app.services.service_case_queries import effective_start_at, select_active_service_cases

logger = logging.getLogger("uvicorn.error")

AI_REPLY_MAX_CHARS = 1500
URL_PATTERN = re.compile(r"https?://[^\s<>()\"']+")

# Unified and concise risk replies are imported from content_policy.py


def _group_bot_control_command(message: ChannelMessage) -> str | None:
    """群内@机器人控制：@机器人说“退下”静默；@机器人说“回来”解除静默。"""
    if message.chat_type != "group" or message.message_type != "text":
        return None
    if not _message_mentions_bot(message):
        return None
    text = re.sub(r"\s+", "", message.text or "")
    if "退下" in text:
        return "silence"
    if "回来" in text:
        return "resume"
    return None


def _message_mentions_bot(message: ChannelMessage) -> bool:
    if isinstance(message.raw_payload, list):
        return any(_message_mentions_bot(ChannelMessage(
            channel=message.channel,
            chat_id=message.chat_id,
            chat_type=message.chat_type,
            text=message.text,
            raw_payload=item,
        )) for item in message.raw_payload)

    payload = message.raw_payload if isinstance(message.raw_payload, dict) else {}
    msg_data = payload.get("msgData") if isinstance(payload.get("msgData"), dict) else {}
    at_list = msg_data.get("atList") or []
    if not isinstance(at_list, list) or not at_list:
        return False

    bot_user_id = str(payload.get("userId") or payload.get("receiverId") or "").strip()
    if not bot_user_id:
        return False

    for item in at_list:
        values: list[str] = []
        if isinstance(item, dict):
            for key in ("userId", "user_id", "wxid", "id", "uid"):
                value = item.get(key)
                if value is not None:
                    values.append(str(value).strip())
        elif item is not None:
            values.append(str(item).strip())
        if bot_user_id in values:
            return True
    return False


def _explicit_bot_query_text(message: ChannelMessage) -> str | None:
    if message.chat_type != "group" or message.message_type != "text":
        return None
    if not _message_mentions_bot(message):
        return None
    text = " ".join((message.text or "").split()).strip()
    if not text:
        return None
    text = re.sub(r"^(?:@\S+\s*)+", "", text).strip()
    return text or None


class RanrenAIEngine(InboundAttachmentProcessor):
    """Channel-agnostic business engine for inbound customer messages."""

    # 转人工 @班主任 的短期防刷屏窗口（秒）：同一 service_case 30 分钟内不重复@。
    HANDOFF_THROTTLE_SECONDS = 1800

    def __init__(
        self,
        channel: MessageChannel,
        settings: Settings | None = None,
        has_pending_newer_message: Callable[[ChannelMessage], Awaitable[bool]] | None = None,
    ):
        self.channel = channel
        self.settings = settings or get_settings()
        self.has_pending_newer_message = has_pending_newer_message
        # 转人工@班主任的短期防刷屏：每个 service_case 30 分钟内不重复@（替代旧的
        # needs_human_intervention 永久标记——旧逻辑会在第一次@后永久不再@后续风险，导致班主任漏消息；
        # 新逻辑每个新风险事件都会@、只在短期内防刷屏）。节流时间戳持久化在
        # service_cases.last_handoff_at（见 _recently_handed_off / _mark_handoff），重启不再清零。

    async def _process_inbound_attachments(
        self,
        *,
        message: ChannelMessage,
        inbound_id: int | None,
        service_case_id: int | None,
        analysis: dict | None,
    ) -> tuple[dict | None, str | None, bool, bool]:
        """Parse inbound images/files before deciding whether the bot may reply."""
        image_analysis_content: str | None = None
        file_processed = False
        non_text_stored_only = False

        if inbound_id is None:
            return analysis, image_analysis_content, file_processed, non_text_stored_only

        if message.message_type == "text" and _image_payload_items(message):
            image_analysis_content = _strip_image_placeholders(message.text)
            image_prompt = await self._process_embedded_images_for_text(
                inbound_id,
                message,
                service_case_id,
                image_analysis_content,
            )
            if image_prompt:
                analysis = {**(analysis or {}), "image_prompt": image_prompt}
            return analysis, image_analysis_content, file_processed, non_text_stored_only

        if message.message_type == "image":
            image_prompt = await self._process_inbound_image(inbound_id, message, service_case_id)
            # 图片内容作为独立 prompt 元数据进入回复上下文；message.text 保持为空，避免伪装成用户原话。
            # 视觉描述不进入风控/成效结构化提取。
            image_analysis_content = ""
            analysis = {**(analysis or {}), "image_prompt": image_prompt}
            return analysis, image_analysis_content, file_processed, non_text_stored_only

        if message.message_type == "file":
            await self._process_inbound_file(inbound_id, message, service_case_id)
            file_processed = True
            return analysis, image_analysis_content, file_processed, non_text_stored_only

        if message.message_type != "text":
            # 其他非文本消息（语音/表情等）仍直接入库留痕，不触发 AI 回复
            logger.info(
                "RanrenAI non-text message stored without AI reply channel=%s chat_id=%s message_id=%s type=%s",
                message.channel,
                message.chat_id,
                message.message_id,
                message.message_type,
            )
            non_text_stored_only = True

        return analysis, image_analysis_content, file_processed, non_text_stored_only

    async def handle_message(self, message: ChannelMessage) -> None:
        started_at = time.monotonic()
        should_send_questionnaire = "发送本群入营问卷" in message.text or "发送本群专属问卷" in message.text
        group_control_command = _group_bot_control_command(message)
        explicit_bot_query_text = _explicit_bot_query_text(message)
        service_case_id: int | None = None
        service_case_for_command: ServiceCase | None = None
        service_case_silenced = False
        resume_takeover_receipt = False
        analysis: dict | None = None
        inbound_id: int | None = None
        recent_user_messages: list[str] = []
        image_analysis_content: str | None = None

        is_staff = False
        sender_type = MessageSenderType.USER

        async with SessionLocal() as session:
            from app.services.content_policy import (
                get_qiwei_staff_senders,
                get_qiwe_nickname,
                remember_qiwe_nickname,
            )
            # 解析发送者昵称：持久缓存优先；缓存未命中才调用一次 API 并落库，避免重复调用
            if message.channel == "qiwe" and not message.user_name and message.user_id:
                cached = await get_qiwe_nickname(session, message.user_id)
                if cached:
                    message.user_name = cached
                elif hasattr(self.channel, "get_contact_nickname"):
                    try:
                        resolved = await self.channel.get_contact_nickname(message.user_id)
                        if resolved:
                            message.user_name = resolved
                            await remember_qiwe_nickname(session, message.user_id, resolved)
                    except Exception as e:
                        logger.error("Error fetching contact nickname in engine: %s", e)

            rules = await get_qiwei_staff_senders(session)

            is_staff = self._check_is_staff_by_rules(message.user_id, message.user_name, rules)
            sender_type = MessageSenderType.STAFF if is_staff else MessageSenderType.USER

            service_case = await self._latest_active_case(session, message.chat_id)
            if should_send_questionnaire and not service_case:
                service_case = await self._create_case_for_message(session, message, is_staff=is_staff)

            if service_case:
                service_case_id = service_case.id
                service_case_for_command = service_case

                if group_control_command == "silence":
                    service_case.ai_auto_reply_enabled = False
                    session.add(service_case)
                    await session.flush()
                    logger.info(
                        "RanrenAI group command silenced bot chat_id=%s service_case_id=%s message_id=%s",
                        message.chat_id,
                        service_case.id,
                        message.message_id,
                    )
                elif group_control_command == "resume":
                    was_silenced = not bool(getattr(service_case, "ai_auto_reply_enabled", True))
                    service_case.ai_auto_reply_enabled = True
                    session.add(service_case)
                    await session.flush()
                    logger.info(
                        "RanrenAI group command resumed bot chat_id=%s service_case_id=%s message_id=%s was_silenced=%s",
                        message.chat_id,
                        service_case.id,
                        message.message_id,
                        was_silenced,
                    )
                    # 仅当之前确实处于静默、本次「回来」真正解除时才回执（避免本就在岗还冒一句）；
                    # 实际发送放到入站消息入库后，确保回执能挂上 trigger_id。
                    resume_takeover_receipt = was_silenced
                # 群内机器人总开关(=退下/回来)关闭时：完全安静，只入库留痕，不进入风险/成效/LLM链路、不@班主任
                service_case_silenced = not bool(getattr(service_case, "ai_auto_reply_enabled", True))

                # Self-healing and dynamic binding for real customer messages
                if not is_staff and message.channel == "qiwe" and message.user_id:
                    case_user = None
                    if hasattr(session, "get"):
                        case_user = await session.get(User, service_case.user_id)
                    if case_user:
                        needs_binding = False
                        if not case_user.wecom_user_id:
                            needs_binding = True
                        else:
                            is_bound_staff = self._check_is_staff_by_rules(case_user.wecom_user_id, None, rules)
                            if is_bound_staff:
                                needs_binding = True
                        
                        if needs_binding:
                            logger.info(
                                "Binding real customer WeCom User ID %s to case user_id=%s (previous was %s)",
                                message.user_id,
                                case_user.id,
                                case_user.wecom_user_id,
                            )
                            case_user.wecom_user_id = message.user_id
                            if message.user_name and case_user.name == "待填写问卷用户":
                                case_user.name = message.user_name
                            session.add(case_user)
                            await session.flush()

            if service_case and not should_send_questionnaire and not is_staff:
                recent_user_messages = await self._recent_user_messages(session, service_case.id)

            purpose = MessagePurpose.CHAT

            meta = {
                "sender_id": message.user_id,
                "sender_name": message.user_name,
                "sender_type": sender_type.value,
                "timestamp": int(message.timestamp.timestamp()),
            }
            if explicit_bot_query_text:
                meta["explicit_bot_query_text"] = explicit_bot_query_text
            if analysis is None:
                analysis = {"_qiwe_meta": meta}
            else:
                analysis["_qiwe_meta"] = meta

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
                message.channel,
                message.chat_id,
                sender_type,
                message.message_id,
                inbound_id,
                service_case_id,
                should_send_questionnaire,
                (time.monotonic() - started_at) * 1000,
            )

        if group_control_command:
            # 「回来」真正解除静默时回执一句（入库留痕、挂 trigger_id）；「退下」保持安静、不回执
            if resume_takeover_receipt:
                receipt = "那我继续来跟进哈~"
                try:
                    await self._send_and_record(
                        message=message,
                        service_case_id=service_case_id,
                        content=receipt,
                        trigger_id=inbound_id,
                        send=lambda: self.channel.send_text(
                            message.chat_id,
                            receipt,
                            reply_to=message.message_id,
                            reply_context=message.raw_payload,
                        ),
                    )
                except Exception as e:
                    logger.error("Failed to send resume command receipt: %s", e)
            logger.info(
                "RanrenAI group control command stored without reply chat_id=%s command=%s trigger_id=%s total_elapsed_ms=%.1f",
                message.chat_id,
                group_control_command,
                inbound_id,
                (time.monotonic() - started_at) * 1000,
            )
            return

        analysis, image_analysis_content, file_processed, non_text_stored_only = await self._process_inbound_attachments(
            message=message,
            inbound_id=inbound_id,
            service_case_id=service_case_id,
            analysis=analysis,
        )

        if service_case_silenced:
            logger.info(
                "RanrenAI silenced by group command; stored inbound and attachment context only chat_id=%s trigger_id=%s service_case_id=%s total_elapsed_ms=%.1f",
                message.chat_id,
                inbound_id,
                service_case_id,
                (time.monotonic() - started_at) * 1000,
            )
            return

        if file_processed:
            # 文件消息：后台下载/解密/抽取文本入库，供 Agent 链路 read_group_file 读取。
            # 用户上传文件回执一句（挂 trigger_id）；班主任(staff)上传静默处理、不打扰（沿用“AI 不拆班主任台”）。
            if not is_staff:
                ack = "文件收到啦，需要我帮你看什么？"
                try:
                    await self._send_and_record(
                        message=message,
                        service_case_id=service_case_id,
                        content=ack,
                        trigger_id=inbound_id,
                        send=lambda: self.channel.send_text(
                            message.chat_id,
                            ack,
                            reply_to=message.message_id,
                            reply_context=message.raw_payload,
                        ),
                    )
                except Exception:
                    logger.exception("Failed to send file receipt ack message_id=%s", inbound_id)
            # 文件本身不触发完整 AI 回复；后续被 @/被问才由 Agent 调 read_group_file 读取
            return

        if non_text_stored_only:
            return

        # 班主任 @机器人 但本条无正文（问题与@被拆成两条发）：回溯紧邻的班主任发言作为问题，
        # 使其能像「@和问题同条」一样触发回复。仅限班主任路径；普通用户纯@维持原有上下文回复逻辑。
        if (
            is_staff
            and not should_send_questionnaire
            and not explicit_bot_query_text
            and message.message_type == "text"
            and _message_mentions_bot(message)
        ):
            recovered_query = await self._staff_query_from_context(
                service_case_id=service_case_id,
                before_message_id=inbound_id,
            )
            if recovered_query:
                explicit_bot_query_text = recovered_query
                logger.info(
                    "RanrenAI recovered staff query from context chat_id=%s trigger_id=%s recovered_len=%d",
                    message.chat_id,
                    inbound_id,
                    len(recovered_query),
                )

        if explicit_bot_query_text:
            message.text = explicit_bot_query_text

        if is_staff and not should_send_questionnaire and not explicit_bot_query_text:
            logger.info(
                "RanrenAI staff message ignored from triggering AI reply channel=%s chat_id=%s message_id=%s",
                message.channel,
                message.chat_id,
                message.message_id,
            )
            return

        if should_send_questionnaire:
            await self._reply_questionnaire_link(message, service_case_for_command, trigger_id=inbound_id)
            logger.info(
                "RanrenAI command handled channel=%s chat_id=%s message_id=%s trigger_id=%s total_elapsed_ms=%.1f",
                message.channel,
                message.chat_id,
                message.message_id,
                inbound_id,
                (time.monotonic() - started_at) * 1000,
            )
            return

        if service_case_id and inbound_id and not is_staff:
            if image_analysis_content is not None and not image_analysis_content:
                logger.info(
                    "RanrenAI skip intervention/outcome analysis for image-only message chat_id=%s trigger_id=%s service_case_id=%s",
                    message.chat_id,
                    inbound_id,
                    service_case_id,
                )
            else:
                analysis_input = image_analysis_content or message.text
                image_prompt = analysis.get("image_prompt") if isinstance(analysis, dict) else None
                analysis = await analyze_intervention(
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
                case_risk_level = await self._store_intervention_analysis(
                    service_case_id=service_case_id,
                    inbound_id=inbound_id,
                    analysis=analysis,
                )
                # 转人工 @班主任 = 当前句需要任何人工介入(requires_human_intervention，含急性/高风险/诉求/中风险)，
                # 外加：持久高风险用户(risk_level=high)问需专业判断的健康/方案问题(health_consultation)。
                # 注：暂不使用「AI 生成回复进待审核」机制——凡需介入一律 @班主任转人工，AI 不再生成待审核回复。
                health_handoff = _requires_health_handoff(analysis, case_risk_level)
                needs_handoff = bool(analysis.get("requires_human_intervention")) or health_handoff
                if needs_handoff:
                    if not await self._recently_handed_off(service_case_id):
                        await self._mark_handoff(service_case_id)
                        await self._reply_human_handoff(message, service_case_id=service_case_id, trigger_id=inbound_id)
                        logger.info(
                            "RanrenAI handed off to staff chat_id=%s trigger_id=%s service_case_id=%s total_elapsed_ms=%.1f",
                            message.chat_id,
                            inbound_id,
                            service_case_id,
                            (time.monotonic() - started_at) * 1000,
                        )
                    else:
                        # 30 分钟内已 @ 过班主任，不重复 @ 刷屏；同样不生成 AI 回复，仅留痕返回。
                        logger.info(
                            "RanrenAI skip duplicate handoff within 30min chat_id=%s trigger_id=%s service_case_id=%s",
                            message.chat_id,
                            inbound_id,
                            service_case_id,
                        )
                    return
                outcome_result = await self._run_outcome_analysis(
                    service_case_id=service_case_id,
                    inbound_id=inbound_id,
                    analysis=analysis,
                    recent_user_messages=recent_user_messages,
                )
                if outcome_result is not None:
                    analysis = {**analysis, "outcome": outcome_result}
                    await self._update_inbound_analysis(inbound_id, analysis)

        if not service_case_id:
            logger.info(
                "RanrenAI ignored message without service case channel=%s chat_id=%s message_id=%s trigger_id=%s total_elapsed_ms=%.1f",
                message.channel,
                message.chat_id,
                message.message_id,
                inbound_id,
                (time.monotonic() - started_at) * 1000,
            )
            return
        if purpose == MessagePurpose.CHAT:
            await self._reply_with_ai(message, service_case_id=service_case_id, analysis=analysis, trigger_id=inbound_id)

    async def reprocess_message(self, message: Message) -> None:
        """Reprocess a stuck inbound USER message (e.g., interrupted by container restart)."""
        started_at = time.monotonic()
        inbound_id = message.id
        service_case_id = message.service_case_id
        if not service_case_id:
            logger.warning("reprocess_message: message %s has no service_case_id", inbound_id)
            return

        # Double check with SELECT FOR UPDATE lock to prevent concurrent double processing
        async with SessionLocal() as session:
            stmt = select(Message).where(Message.id == inbound_id).with_for_update()
            msg_row = await session.scalar(stmt)
            if not msg_row:
                logger.warning("reprocess_message: Message %s not found in lock", inbound_id)
                return

            analysis_json = msg_row.analysis_json if isinstance(msg_row.analysis_json, dict) else {}
            if _message_already_processed_or_claimed(analysis_json):
                logger.info("reprocess_message: Skip message %s because it is already processed or claimed", inbound_id)
                return

            # 1. Re-verify if any outbound message exists for this trigger_id
            reply_exists = await session.scalar(
                select(Message.id)
                .where(
                    Message.direction == MessageDirection.OUTBOUND,
                    Message.trigger_id == inbound_id
                )
                .limit(1)
            )
            if reply_exists:
                logger.info("reprocess_message: Skip message %s because outbound reply already exists in lock", inbound_id)
                return

            # 2. Check if there are newer inbound messages (both user and staff) in this case.
            # If so, do not process this message to prevent out-of-order replies.
            newer_msg_exists = await session.scalar(
                select(Message.id)
                .where(
                    Message.service_case_id == service_case_id,
                    Message.id > inbound_id,
                    Message.direction == MessageDirection.INBOUND,
                )
                .limit(1)
            )
            if newer_msg_exists:
                logger.info(
                    "reprocess_message: Skip message %s because newer inbound message %s exists",
                    inbound_id,
                    newer_msg_exists
                )
                return

            # 3. Double check case status and AI toggle
            service_case = await session.scalar(select_active_service_cases(ServiceCase.id == service_case_id))
            
            if not service_case or not service_case.ai_auto_reply_enabled:
                logger.info("reprocess_message: Skip message %s because case is inactive/silenced", inbound_id)
                return

            # 4. Claim the message before releasing the row lock. This keeps another
            # startup recovery task from passing the same checks before this one
            # writes analysis or records outbound.
            claimed = dict(analysis_json)
            claimed["_recovery_claim"] = {
                "claimed_at": datetime.now(timezone.utc).isoformat(),
                "reason": "startup_recovery",
            }
            msg_row.analysis_json = claimed
            await session.commit()

        # Reconstruct ChannelMessage
        meta = (message.analysis_json or {}).get("_qiwe_meta", {}) if message.analysis_json else {}
        channel_msg = ChannelMessage(
            channel=self.channel.name,
            chat_id=message.group_id,
            chat_type="group",
            text=message.content,
            message_id=message.wecom_message_id,
            user_id=meta.get("sender_id"),
            user_name=meta.get("sender_name"),
            message_type=message.message_type,
            raw_payload=None,
            timestamp=message.created_at
        )

        async with SessionLocal() as session:
            recent_user_messages = await self._recent_user_messages(session, service_case_id, exclude_message_id=inbound_id)

        analysis = await analyze_intervention(
            source="chat_message",
            content=channel_msg.text,
            context={
                "service_case_id": service_case_id,
                "message_id": inbound_id,
                "group_id": channel_msg.chat_id,
                "current_message": channel_msg.text,
                "recent_user_messages": recent_user_messages,
            },
        )
        case_risk_level = await self._store_intervention_analysis(
            service_case_id=service_case_id,
            inbound_id=inbound_id,
            analysis=analysis,
        )

        health_handoff = _requires_health_handoff(analysis, case_risk_level)
        needs_handoff = bool(analysis.get("requires_human_intervention")) or health_handoff

        if needs_handoff:
            if not await self._recently_handed_off(service_case_id):
                await self._mark_handoff(service_case_id)
                await self._reply_human_handoff(channel_msg, service_case_id=service_case_id, trigger_id=inbound_id)
                logger.info(
                    "RanrenAI reprocess handed off to staff chat_id=%s trigger_id=%s service_case_id=%s total_elapsed_ms=%.1f",
                    channel_msg.chat_id,
                    inbound_id,
                    service_case_id,
                    (time.monotonic() - started_at) * 1000,
                )
            else:
                logger.info(
                    "RanrenAI reprocess skip duplicate handoff within 30min chat_id=%s trigger_id=%s service_case_id=%s",
                    channel_msg.chat_id,
                    inbound_id,
                    service_case_id,
                )
            return

        outcome_result = await self._run_outcome_analysis(
            service_case_id=service_case_id,
            inbound_id=inbound_id,
            analysis=analysis,
            recent_user_messages=recent_user_messages,
        )
        if outcome_result is not None:
            analysis = {**analysis, "outcome": outcome_result}
            await self._update_inbound_analysis(inbound_id, analysis)

        if message.purpose == MessagePurpose.CHAT:
            await self._reply_with_ai(channel_msg, service_case_id=service_case_id, analysis=analysis, trigger_id=inbound_id)

    async def _reply_human_handoff(
        self,
        message: ChannelMessage,
        *,
        service_case_id: int | None,
        trigger_id: int | None,
    ) -> None:
        settings = get_settings()
        target_user_id = (settings.qiwei_handoff_at_user_id or "").strip()

        # 安抚话术正文用运营在审核台可配的「高风险固定话术」(原本几乎不触发，现作①层统一安抚语)，末尾接 @班主任。
        from app.services.content_policy import get_risk_fixed_reply, DEFAULT_RISK_REPLY
        async with SessionLocal() as session:
            safety_text = (await get_risk_fixed_reply(session) or "").strip()
        if not safety_text:
            safety_text = DEFAULT_RISK_REPLY

        # 1. If we have a target staff User ID and channel supports hypertext, send native mention
        if target_user_id and hasattr(self.channel, "send_hypertext"):
            prefix_text = f"{safety_text}\n"
            content_list = [
                {"subtype": 0, "text": prefix_text},
                {"subtype": 1, "text": target_user_id},
                {"subtype": 0, "text": " 麻烦有空时接手跟进一下哈～"}
            ]

            # Resolve the nickname dynamically for database logging (using cached contact info)
            nickname = "班主任"
            if hasattr(self.channel, "get_contact_nickname"):
                try:
                    resolved = await self.channel.get_contact_nickname(target_user_id)
                    if resolved:
                        nickname = resolved
                except Exception as e:
                    logger.error("Failed to resolve nickname for at-mention ID %s: %s", target_user_id, e)

            # Reconstruct readable text for outbound message logging in DB
            plain_text = f"{prefix_text}@{nickname} 麻烦有空时接手跟进一下哈～"
            send = lambda: self.channel.send_hypertext(
                message.chat_id,
                content_list,
                reply_to=message.message_id,
                reply_context=message.raw_payload,
            )
        else:
            # Fallback to standard plain text send（不支持 @，仅发安抚话术）
            plain_text = safety_text
            send = lambda: self.channel.send_text(
                message.chat_id,
                safety_text,
                reply_to=message.message_id,
                reply_context=message.raw_payload,
            )

        # 3. record-first 发送并落库（先 SENDING、后回填），保证崩溃不重复@班主任
        result = await self._send_and_record(
            message=message,
            service_case_id=service_case_id,
            content=plain_text,
            trigger_id=trigger_id,
            send=send,
        )
        if result is None:
            logger.warning("Human handoff send returned None chat_id=%s service_case_id=%s", message.chat_id, service_case_id)

    async def _store_intervention_analysis(
        self,
        *,
        service_case_id: int,
        inbound_id: int,
        analysis: dict,
    ) -> str | None:
        """合并风险洞察并持久化，返回合并后该 case 的 risk_level（供路由判断高风险用户）。"""
        async with SessionLocal() as session:
            service_case = await session.scalar(select_active_service_cases(ServiceCase.id == service_case_id))
            db_inbound = await session.get(Message, inbound_id)
            risk_level = service_case.risk_level if service_case else None
            if service_case:
                merge_case_insight(service_case, analysis)
                risk_level = service_case.risk_level
            if db_inbound:
                existing = db_inbound.analysis_json or {}
                meta = existing.get("_qiwe_meta")
                new_analysis = dict(analysis)
                if meta:
                    new_analysis["_qiwe_meta"] = meta
                db_inbound.analysis_json = new_analysis
            await session.commit()
            return risk_level

    async def _recently_handed_off(self, service_case_id: int) -> bool:
        """30 分钟内是否已 @ 过班主任。节流时间持久化在 service_cases.last_handoff_at，重启不丢。"""
        async with SessionLocal() as session:
            service_case = await session.scalar(
                select_active_service_cases(ServiceCase.id == service_case_id)
            )
        last = service_case.last_handoff_at if service_case else None
        if last is None:
            return False
        if last.tzinfo is None:  # SQLite 等可能返回 naive datetime，按 UTC 处理
            last = last.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - last).total_seconds() < self.HANDOFF_THROTTLE_SECONDS

    async def _mark_handoff(self, service_case_id: int) -> None:
        """记录本次 @班主任时间，用于短期防刷屏节流。"""
        async with SessionLocal() as session:
            service_case = await session.scalar(
                select_active_service_cases(ServiceCase.id == service_case_id)
            )
            if service_case:
                service_case.last_handoff_at = datetime.now(timezone.utc)
                await session.commit()

    async def _run_outcome_analysis(
        self,
        *,
        service_case_id: int,
        inbound_id: int,
        analysis: dict,
        recent_user_messages: list[str],
    ) -> dict | None:
        try:
            async with SessionLocal() as session:
                plan = await prepare_outcome_extraction(
                    session,
                    service_case_id=service_case_id,
                    message_id=inbound_id,
                    intervention_analysis=analysis,
                    recent_messages=recent_user_messages,
                )
                await session.commit()
            if plan is None:
                return None
            raw = await extract_outcome_from_plan(plan)
            async with SessionLocal() as session:
                result = await apply_outcome_analysis_plan(
                    session,
                    plan=plan,
                    raw=raw,
                    intervention_analysis=analysis,
                )
                await session.commit()
                return result
        except Exception:
            logger.exception(
                "RanrenAI outcome analysis failed service_case_id=%s inbound_id=%s",
                service_case_id,
                inbound_id,
            )
            return None

    async def _update_inbound_analysis(self, inbound_id: int, analysis: dict) -> None:
        async with SessionLocal() as session:
            db_inbound = await session.get(Message, inbound_id)
            if db_inbound:
                existing = db_inbound.analysis_json or {}
                meta = existing.get("_qiwe_meta")
                new_analysis = dict(analysis)
                if meta:
                    new_analysis["_qiwe_meta"] = meta
                db_inbound.analysis_json = new_analysis
            await session.commit()

    async def _create_case_for_message(
        self, session, message: ChannelMessage, is_staff: bool = False
    ) -> ServiceCase:
        user = User(
            name="待填写问卷用户",
            wecom_user_id=None if is_staff else (message.user_id if message.channel == "qiwe" else None),
            notes=f"由 {message.channel} 入营问卷指令自动创建",
        )
        session.add(user)
        await session.flush()
        service_case = ServiceCase(
            case_no=f"RAI-{datetime.now().strftime('%Y%m%d')}-{secrets.token_hex(3).upper()}",
            user_id=user.id,
            group_id=message.chat_id,
            questionnaire_token=secrets.token_urlsafe(24),
            notes=f"由 {message.channel} 入营问卷指令自动创建",
        )
        session.add(service_case)
        await session.flush()
        return service_case

    async def _reply_questionnaire_link(
        self, message: ChannelMessage, service_case: ServiceCase | None, *, trigger_id: int | None = None
    ) -> None:
        if not service_case:
            return
        started_at = time.monotonic()
        base_url = self.settings.frontend_public_base_url.rstrip("/")
        async with SessionLocal() as link_session:
            code = await get_or_create_short_link(
                link_session,
                service_case_id=service_case.id,
                template_key="intake",
                target_path=f"/q/{service_case.questionnaire_token}",
            )
            await link_session.commit()
        link = f"{base_url}/s/{code}"
        reply = (
            "收到，已为本群生成专属入营问卷。\n\n"
            "请点击下方链接完成测评：\n"
            f"{link}\n\n"
            "这份问卷大约需要 3–5 分钟，主要了解你的体重基础、作息节律、"
            "饮食习惯、睡眠恢复、运动情况和阶段目标。\n\n"
            "提交后，我们会基于你的填写结果生成两份专属文件：\n\n"
            "1.《个人能量重启处方单》\n"
            "2.《28 天执行标准与打卡要求》\n\n"
            "填得越真实，后面的建议越准确。"
        )
        result = await self._send_and_record(
            message=message,
            service_case_id=service_case.id,
            content=reply,
            trigger_id=trigger_id,
            send=lambda: self.channel.send_text(
                message.chat_id,
                reply,
                reply_to=message.message_id,
                reply_context=message.raw_payload,
            ),
        )
        send_elapsed_ms = (time.monotonic() - started_at) * 1000
        logger.info(
            "RanrenAI questionnaire reply sent chat_id=%s trigger_id=%s success=%s channel_message_id=%s send_elapsed_ms=%.1f",
            message.chat_id,
            trigger_id,
            result.success,
            result.message_id,
            send_elapsed_ms,
        )

    async def _reply_with_ai(
        self,
        message: ChannelMessage,
        *,
        service_case_id: int | None,
        analysis: dict | None,
        trigger_id: int | None = None,
    ) -> None:
        reply = ""
        should_send = False
        service_case: ServiceCase | None = None
        outbound_analysis: dict | None = None
        settings = get_settings()
        async with SessionLocal() as session:
            if service_case_id:
                stmt = select_active_service_cases(ServiceCase.id == service_case_id).options(selectinload(ServiceCase.user))
                service_case = await session.scalar(stmt)
                if service_case is None:
                    logger.info("RanrenAI skip reply because service case is inactive service_case_id=%s", service_case_id)
                    return
            else:
                service_case = None

            # 全局「AI 回不回复」总开关：关闭时不回复（生成前早退，省 LLM 调用）。
            if not await get_ai_reply_enabled(session):
                logger.info(
                    "RanrenAI skip AI reply because globally disabled chat_id=%s trigger_id=%s service_case_id=%s",
                    message.chat_id,
                    trigger_id,
                    service_case_id,
                )
                return
            # 每单开关作为总闸下的子闸(AND)：该服务单关闭 AI 自动回复 → 不回。
            if service_case and not service_case.ai_auto_reply_enabled:
                logger.info(
                    "RanrenAI skip AI reply because case auto-reply disabled chat_id=%s trigger_id=%s service_case_id=%s",
                    message.chat_id,
                    trigger_id,
                    service_case_id,
                )
                return

            recent_user_messages = (
                await self._recent_user_messages(
                    session,
                    service_case.id,
                    exclude_message_id=trigger_id,
                )
                if service_case
                else []
            )

        if service_case_id and service_case:
            try:
                agent_result = await generate_agent_reply(
                    service_case_id=service_case_id,
                    trigger_id=trigger_id,
                    user_query=_current_query_body(analysis, message.text),
                    query_speaker=_current_query_speaker(analysis),
                    recent_messages=recent_user_messages,
                    analysis=analysis,
                    channel=message.channel,
                    settings=settings,
                )
                reply = agent_result.reply
                outbound_analysis = {
                    "agent": {
                        "enabled": True,
                        "context_plan": analysis.get("agent_context_plan") if isinstance(analysis, dict) else None,
                        "tool_trace": agent_result.tool_trace,
                        "data_tool_failed": agent_result.data_tool_failed,
                    }
                }
            except AgentDataUnavailableError as exc:
                logger.warning(
                    "Agent data unavailable; using safe fixed reply service_case_id=%s trigger_id=%s",
                    service_case_id,
                    trigger_id,
                )
                reply = str(exc)
                outbound_analysis = {"agent": {"enabled": True, "data_unavailable": True}}
            except Exception:
                # AI 生成失败：直接丢弃不发（只记日志），不再落草稿进审核台。
                logger.exception(
                    "Agent reply failed; discarding without sending service_case_id=%s trigger_id=%s",
                    service_case_id,
                    trigger_id,
                )
                return
        else:
            logger.info(
                "RanrenAI skip Agent reply because no active service case chat_id=%s trigger_id=%s service_case_id=%s",
                message.chat_id,
                trigger_id,
                service_case_id,
            )
            return

        # AI 主动选择沉默：模型输出 <no_reply> 表示对话已自然收尾、本轮无需回复。
        # 直接丢弃——不发送、不入聊天记录、不进待审核，避免把内心独白/旁白当正文发进群。
        if reply and _reply_is_no_reply(reply):
            logger.info(
                "RanrenAI skip AI reply because model chose no-reply chat_id=%s trigger_id=%s service_case_id=%s",
                message.chat_id,
                trigger_id,
                service_case_id,
            )
            return

        if reply:
            reply = _sanitize_ai_reply(reply, self.settings)
            # 逻辑①不拆台：AI 生成期间班主任(真人)已就这条接话 → AI 不抢话，直接丢弃(不发、不入待审核)
            if await self._staff_replied_after(service_case_id=service_case_id, trigger_id=trigger_id):
                logger.info(
                    "RanrenAI skip AI reply because staff already replied chat_id=%s trigger_id=%s service_case_id=%s",
                    message.chat_id,
                    trigger_id,
                    service_case_id,
                )
                return
            async with SessionLocal() as session:
                should_send = await self._should_send_ai_reply(session, service_case, analysis, message)

        if should_send:
            if await self._has_newer_inbound_message(service_case_id=service_case_id, trigger_id=trigger_id):
                logger.info(
                    "RanrenAI skip stale AI reply chat_id=%s trigger_id=%s service_case_id=%s",
                    message.chat_id,
                    trigger_id,
                    service_case_id,
                )
                return
            if self.has_pending_newer_message and await self.has_pending_newer_message(message):
                logger.info(
                    "RanrenAI skip AI reply because newer raw messages are queued chat_id=%s trigger_id=%s service_case_id=%s",
                    message.chat_id,
                    trigger_id,
                    service_case_id,
                )
                return
            send_started_at = time.monotonic()
            result = await self._send_and_record(
                message=message,
                service_case_id=service_case_id,
                content=reply,
                trigger_id=trigger_id,
                analysis_json=outbound_analysis,
                send=lambda: self.channel.send_text(
                    message.chat_id,
                    reply,
                    reply_to=message.message_id,
                    reply_context=message.raw_payload,
                ),
            )
            send_elapsed_ms = (time.monotonic() - send_started_at) * 1000
            logger.info(
                "RanrenAI AI reply sent chat_id=%s trigger_id=%s success=%s channel_message_id=%s send_elapsed_ms=%.1f",
                message.chat_id,
                trigger_id,
                result.success,
                result.message_id,
                send_elapsed_ms,
            )
            return

        # should_send 为 False（防御性兜底命中）→ 直接丢弃，不发送、不落库。
        logger.info(
            "RanrenAI discard AI reply because should_send is false chat_id=%s trigger_id=%s service_case_id=%s",
            message.chat_id,
            trigger_id,
            service_case_id,
        )

    async def _should_send_ai_reply(
        self,
        session,
        service_case: ServiceCase | None,
        analysis: dict | None,
        message: ChannelMessage,
    ) -> bool:
        # 全局/每单开关已在生成前早退处理。此处仅保留防御性兜底：
        # 凡需人工介入(requires_human_intervention)的消息，主流程已提前 @班主任转人工并 return，
        # 正常走不到这里；保留此判断，绝不让需介入的消息自动发 AI 回复。
        if analysis and analysis.get("requires_human_intervention"):
            return False
        return True

    async def _create_outbound_sending(
        self,
        *,
        message: ChannelMessage,
        service_case_id: int | None,
        content: str,
        trigger_id: int | None = None,
        analysis_json: dict | None = None,
    ) -> int:
        """record-first：发送前先落一条 SENDING 出站记录，返回其 id。

        这样即使进程恰好在「已调用渠道 API 发出、但还没回填结果」的瞬间崩溃，DB 里也已存在
        一条挂了 trigger_id 的出站记录，开机 recover_stuck 会据此去重、不重复补发（宁可少发）。
        """
        async with SessionLocal() as session:
            row = Message(
                service_case_id=service_case_id,
                group_id=message.chat_id,
                wecom_message_id=None,
                direction=MessageDirection.OUTBOUND,
                sender_type=MessageSenderType.BOT,
                message_type="markdown",
                content=content,
                purpose=MessagePurpose.CHAT,
                status=MessageStatus.SENDING,
                error=None,
                trigger_id=trigger_id,
                analysis_json=analysis_json,
            )
            session.add(row)
            await session.flush()
            outbound_id = row.id
            await session.commit()
            return outbound_id

    async def _finalize_outbound(
        self,
        outbound_id: int,
        *,
        message_id: str | None,
        success: bool,
        error: str | None,
    ) -> None:
        """回填 SENDING 记录的最终结果：成功→SENT+sent_at，失败→FAILED。"""
        async with SessionLocal() as session:
            row = await session.get(Message, outbound_id)
            if row is None:
                return
            row.wecom_message_id = message_id
            row.status = MessageStatus.SENT if success else MessageStatus.FAILED
            row.error = None if success else error
            if success:
                row.sent_at = datetime.now(timezone.utc)
            await session.commit()

    async def _send_and_record(
        self,
        *,
        message: ChannelMessage,
        service_case_id: int | None,
        content: str,
        send: Callable[[], Awaitable[Any]],
        trigger_id: int | None = None,
        analysis_json: dict | None = None,
    ) -> Any:
        """record-first 出站发送：先落 SENDING 行 → 调 send() 真正发送 → 回填 SENT/FAILED。

        send() 抛异常时也会把记录回填为 FAILED 再向上抛，保证不残留悬空 SENDING（崩溃场景除外，
        由开机对账兜底）。返回 send() 的原始结果供调用方记日志。
        """
        outbound_id = await self._create_outbound_sending(
            message=message,
            service_case_id=service_case_id,
            content=content,
            trigger_id=trigger_id,
            analysis_json=analysis_json,
        )
        try:
            result = await send()
        except Exception as exc:
            await self._finalize_outbound(
                outbound_id,
                message_id=None,
                success=False,
                error=f"send raised: {type(exc).__name__}: {exc}",
            )
            raise
        await self._finalize_outbound(
            outbound_id,
            message_id=(result.message_id if result else None),
            success=bool(result and result.success),
            error=(result.error if result else "send result was None"),
        )
        return result

    async def _has_newer_inbound_message(self, *, service_case_id: int | None, trigger_id: int | None) -> bool:
        if not service_case_id or not trigger_id:
            return False
        async with SessionLocal() as session:
            newer_id = await session.scalar(
                select(Message.id)
                .where(
                    Message.service_case_id == service_case_id,
                    Message.id > trigger_id,
                    Message.direction == MessageDirection.INBOUND,
                    Message.sender_type == MessageSenderType.USER,
                    Message.purpose == MessagePurpose.CHAT,
                    Message.status == MessageStatus.RECEIVED,
                )
                .order_by(Message.id.desc())
                .limit(1)
            )
            return newer_id is not None

    async def _staff_replied_after(self, *, service_case_id: int | None, trigger_id: int | None) -> bool:
        """当前用户消息(trigger)之后，班主任(真人)是否已在群里发言——有则 AI 让位，不抢话/不拆台。"""
        if not service_case_id or not trigger_id:
            return False
        async with SessionLocal() as session:
            staff_id = await session.scalar(
                select(Message.id)
                .where(
                    Message.service_case_id == service_case_id,
                    Message.id > trigger_id,
                    Message.direction == MessageDirection.INBOUND,
                    Message.sender_type == MessageSenderType.STAFF,
                )
                .order_by(Message.id.desc())
                .limit(1)
            )
            return staff_id is not None

    async def _staff_query_from_context(
        self, *, service_case_id: int | None, before_message_id: int | None
    ) -> str | None:
        """班主任 @机器人 但那条消息本身无正文时，回溯其紧邻、尚未被 AI 回应的连续发言拼成问题。

        真人班主任常把「问题」和「@机器人」拆成两条发（甚至跨聚合窗口），只@那条去掉@昵称后为空，
        单看本条无法触发回复。这里从 @这条逆序扫描：遇到出站消息（前文已被回应过）或非班主任/非文本
        消息即停，只收集连续的班主任文本发言，正序拼成问题。找不到返回 None（维持「班主任未提问不打扰」）。
        """
        if not service_case_id or not before_message_id:
            return None
        async with SessionLocal() as session:
            rows = (
                await session.execute(
                    select(Message)
                    .where(
                        Message.service_case_id == service_case_id,
                        Message.id < before_message_id,
                    )
                    .order_by(Message.id.desc())
                    .limit(10)
                )
            ).scalars().all()
        parts: list[str] = []
        for m in rows:
            # 遇到出站消息，说明更早的发言已被 AI/系统回应过，不再往前回溯
            if m.direction != MessageDirection.INBOUND:
                break
            # 只承接连续的班主任文本发言；遇到用户消息或非文本消息即停
            if m.sender_type != MessageSenderType.STAFF or m.message_type != "text":
                break
            cleaned = re.sub(
                r"^(?:@\S+\s*)+", "", " ".join((m.content or "").split()).strip()
            ).strip()
            if not cleaned:
                continue
            parts.append(cleaned)
        if not parts:
            return None
        parts.reverse()
        return "\n".join(parts)

    def _check_is_staff_by_rules(
        self, user_id: str | None, user_name: str | None, rules: list[dict]
    ) -> bool:
        if not user_id and not user_name:
            return False
        for rule in rules:
            rule_type = rule.get("type")
            pattern = rule.get("pattern", "").strip()
            if not pattern:
                continue
            pattern_lower = pattern.lower()
            for val in (user_id, user_name):
                if not val:
                    continue
                val_lower = val.strip().lower()
                if rule_type == "equals":
                    if val_lower == pattern_lower:
                        return True
                elif rule_type == "contains":
                    if pattern_lower in val_lower:
                        return True
        return False

    async def _latest_active_case(self, session, group_id: str) -> ServiceCase | None:
        return await session.scalar(
            select_active_service_cases(ServiceCase.group_id == group_id)
            .order_by(ServiceCase.created_at.desc())
            .limit(1)
        )

    async def _recent_user_messages(
        self,
        session,
        service_case_id: int,
        *,
        limit: int = 10,
        exclude_message_id: int | None = None,
    ) -> list[str]:
        conditions = [
            Message.service_case_id == service_case_id,
            or_(
                and_(
                    Message.direction == MessageDirection.INBOUND,
                    Message.sender_type.in_([MessageSenderType.USER, MessageSenderType.STAFF]),
                    Message.purpose == MessagePurpose.CHAT,
                    Message.status == MessageStatus.RECEIVED,
                ),
                and_(
                    Message.direction == MessageDirection.OUTBOUND,
                    Message.sender_type == MessageSenderType.BOT,
                    Message.purpose.in_([MessagePurpose.CHAT, MessagePurpose.DOCUMENT_DELIVERY]),
                    Message.status == MessageStatus.SENT,
                ),
                and_(
                    Message.direction == MessageDirection.OUTBOUND,
                    Message.sender_type == MessageSenderType.STAFF,
                    Message.purpose.in_([MessagePurpose.CHAT, MessagePurpose.DOCUMENT_DELIVERY]),
                    Message.status == MessageStatus.SENT,
                ),
            ),
        ]
        if exclude_message_id is not None:
            conditions.append(Message.id != exclude_message_id)
        result = await session.scalars(
            select(Message)
            .where(*conditions)
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
        return [_format_chat_history_line(item) for item in reversed(list(result))]

    async def _build_user_context(self, session, service_case: ServiceCase | None) -> str:
        return await build_ai_user_context(session, service_case)

    async def _build_intervention_context(self, session, *, service_case: ServiceCase | None) -> dict:
        if not service_case:
            return {}
        context: dict[str, object] = {
            "current_risk_level": service_case.risk_level,
            "intervention_reasons": service_case.intervention_reasons or [],
            "risk_flags": service_case.risk_flags or [],
        }
        state_profile = await session.scalar(
            select(UserStateProfile)
            .where(UserStateProfile.service_case_id == service_case.id)
            .order_by(UserStateProfile.updated_at.desc())
            .limit(1)
        )
        if state_profile:
            action_focus = _state_action_focus(state_profile.state_json)
            if action_focus:
                context["current_action_focus"] = action_focus
        return context


async def build_ai_user_context(session, service_case: ServiceCase | None) -> str:
    """Build the exact customer context injected into chat AI replies.

    This function is also exposed to the admin UI as a preview endpoint. Keep it
    as the single source of truth so operators see the same context that the
    model receives, instead of a frontend approximation that can drift.
    """

    if not service_case:
        return "未知用户，无历史档案。"

    days_text = "服务准备中"
    start = effective_start_at(service_case)
    if start:
        now = datetime.now(start.tzinfo)
        days = (now - start).days + 1
        days_text = f"第 {days} 天" if days >= 1 else "服务尚未正式开始（计划开始日未到）"

    profile = await session.scalar(
        select(UserProfile)
        .where(UserProfile.service_case_id == service_case.id)
        .order_by(UserProfile.created_at.desc())
        .limit(1)
    )
    profile_json = profile.profile_json if profile and isinstance(profile.profile_json, dict) else {}
    overview = profile_json.get("overview") if isinstance(profile_json.get("overview"), dict) else {}
    need_analysis = profile_json.get("need_analysis") if isinstance(profile_json.get("need_analysis"), dict) else {}
    profile_summary = str(profile.summary or profile_json.get("summary") or "暂无详细画像") if profile else "暂无详细画像"

    stable_lines = [
        f"- 身体状况及方案总结：{profile_summary}",
        f"- 性别/年龄：{_context_value(overview.get('gender_age'))}",
        f"- 身高/体重：{_context_value(overview.get('height_weight'))}",
        f"- BMI：{_context_value(overview.get('bmi'))}",
        f"- 腰围：{_context_value(overview.get('waist') or overview.get('waist_bodyfat'))}",
        f"- 减重带：{_context_value(overview.get('weight_loss_band') or profile_json.get('weight_loss_band'))}",
        f"- 核心问题：{_context_value(profile_json.get('main_problem'))}",
        f"- 28天目标：{_context_value(profile_json.get('day28_goal'))}",
        f"- 明确风险：{_context_value(profile_json.get('risks'))}",
        f"- 真实需求：{_context_value(need_analysis.get('priority_need'))}",
    ]

    state_profile = await session.scalar(
        select(UserStateProfile)
        .where(UserStateProfile.service_case_id == service_case.id)
        .order_by(UserStateProfile.updated_at.desc())
        .limit(1)
    )
    state_summary = (
        _compact_state_profile_for_prompt(state_profile.state_json)
        if state_profile
        else "暂无动态状态"
    )

    checkin_texts = await _recent_checkin_context_lines(session, service_case.id)
    documents = list(
        await session.scalars(
            select(UserDocument)
            .where(UserDocument.service_case_id == service_case.id)
            .order_by(UserDocument.created_at.desc())
        )
    )
    latest_by_type: dict[DocumentType, UserDocument] = {}
    for document in documents:
        latest_by_type.setdefault(document.document_type, document)

    document_lines = _document_status_lines(latest_by_type)
    document_core_lines = _document_core_context_lines(latest_by_type)
    latest_delivery_text = await _latest_delivery_context(session, service_case.id)

    return (
        f"### 稳定画像与基础事实（{days_text}）\n"
        + "\n".join(stable_lines)
        + "\n\n"
        "### 当前动态状态与真实需求\n"
        f"{state_summary}\n\n"
        "### 交付文件核心口径\n"
        + ("\n".join(document_core_lines) if document_core_lines else "- 暂无交付文件核心内容。")
        + "\n\n"
        "### 服务进度与交付状态\n"
        f"- 当前服务单状态：{service_case.status.value}\n"
        + ("\n".join(document_lines) + "\n" if document_lines else "- 暂无交付文件。\n")
        + f"- 最近一次交付动作：{latest_delivery_text}\n\n"
        "### 最近打卡趋势（近3次记录）\n"
        + "\n".join(checkin_texts)
    )


async def _recent_checkin_context_lines(session, service_case_id: int) -> list[str]:
    # 最新在前（用于画像上下文展示）。查询主体见 checkin_queries.recent_checkins，与 agent 工具共用一处定义。
    checkins = await recent_checkins(session, service_case_id, limit=3, ascending=False)
    if not checkins:
        return ["- 暂无打卡记录。"]

    lines: list[str] = []
    for checkin in checkins:
        date_str = checkin.checkin_date.strftime("%Y-%m-%d")
        if not checkin.parsed_json:
            short_content = checkin.content.replace("\n", " ").strip()[:60]
            lines.append(f"- [{date_str}] {short_content}...")
            continue
        pj = checkin.parsed_json
        ps = pj.get("product_schedule") or {}
        cba = (
            f"早C:{ps.get('morning_c') or '未记录'}/"
            f"午B:{ps.get('noon_b') or '未记录'}/"
            f"晚A:{ps.get('night_a') or '未记录'}"
        )
        detail = (
            f"体重：{pj.get('weight') or '未记录'} ｜ 腰围：{pj.get('waist') or '未记录'} ｜ {cba} ｜ "
            f"睡眠：{pj.get('sleep') or '未记录'} ｜ 饮食：{pj.get('diet') or '未记录'} ｜ "
            f"饮水：{pj.get('water') or '未记录'} ｜ 排便：{pj.get('bowel') or '未记录'} ｜ "
            f"食欲/饥饿感：{pj.get('hunger') or '未记录'} ｜ 体感：{pj.get('sensation') or '未记录'} ｜ "
            f"不适：{pj.get('discomfort') or '无'}"
        )
        lines.append(f"- [{date_str}] {detail}")
    return lines


async def _latest_delivery_context(session, service_case_id: int) -> str:
    latest_delivery = await session.scalar(
        select(Message)
        .where(
            Message.service_case_id == service_case_id,
            Message.purpose == MessagePurpose.DOCUMENT_DELIVERY,
            Message.direction == MessageDirection.OUTBOUND,
            Message.sender_type == MessageSenderType.BOT,
            Message.status == MessageStatus.SENT,
        )
        .order_by(Message.created_at.desc())
        .limit(1)
    )
    if not latest_delivery:
        return "暂无交付发送记录。"
    return (
        f"{latest_delivery.content} "
        f"（{latest_delivery.created_at.astimezone().strftime('%Y-%m-%d %H:%M')}）"
    )


def _document_status_lines(latest_by_type: dict[DocumentType, UserDocument]) -> list[str]:
    status_labels = {
        DocumentStatus.DRAFT: "草稿",
        DocumentStatus.REVIEWING: "待审核",
        DocumentStatus.APPROVED: "已审核",
        DocumentStatus.SENT: "已发送",
        DocumentStatus.ARCHIVED: "未发送归档",
    }
    title_labels = {
        DocumentType.ENERGY_RESTART_PRESCRIPTION: "个人能量重启处方单",
        DocumentType.DAY28_EXECUTION_STANDARD: "28天执行标准与打卡要求",
        DocumentType.STAGE_REPORT: "阶段报告",
        DocumentType.GRADUATION_REPORT: "毕业报告",
    }
    lines: list[str] = []
    for document_type in title_labels:
        document = latest_by_type.get(document_type)
        if not document:
            continue
        status = status_labels.get(document.status, document.status.value)
        sent_suffix = (
            f"（发送于 {document.sent_at.astimezone().strftime('%Y-%m-%d %H:%M')}）"
            if document.sent_at
            else ""
        )
        lines.append(f"- {title_labels[document_type]}：{status}{sent_suffix}")
    return lines


def _document_core_context_lines(latest_by_type: dict[DocumentType, UserDocument]) -> list[str]:
    energy = latest_by_type.get(DocumentType.ENERGY_RESTART_PRESCRIPTION)
    execution = latest_by_type.get(DocumentType.DAY28_EXECUTION_STANDARD)
    items = [
        ("一句话结论", _document_block_text(energy, "one_sentence")),
        ("当前最该先抓方向", _document_block_text(energy, "priority_direction")),
        ("最不能打折的一件事", _document_block_text(energy, "non_negotiable")),
        ("执行总原则", _document_block_text(execution, "principle")),
        ("每日打卡项", _document_block_text(execution, "daily_checkin")),
    ]
    return [f"- {label}：{_short_prompt_value(value)}" for label, value in items if _short_prompt_value(value)]


def _document_block_text(document: UserDocument | None, key: str) -> str:
    if not document or not isinstance(document.content_json, dict):
        return ""
    blocks = document.content_json.get("blocks")
    if not isinstance(blocks, list):
        return ""
    for block in blocks:
        if not isinstance(block, dict) or block.get("key") != key:
            continue
        content = block.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        items = block.get("items")
        if isinstance(items, list):
            return "；".join(str(item).strip() for item in items if str(item).strip())
    return ""


def _context_value(value: object) -> str:
    return _short_prompt_value(value) or "未提供"


def _prompt_data(value: object) -> str:
    """Escape user-controlled text before placing it inside prompt data tags."""
    return escape(str(value or ""), quote=False)


# 急性体感 code：medium 及以上即视为需真人立即接手
_IMMEDIATE_SAFETY_CODES = {"severe_insomnia", "chest_pain", "palpitation", "allergy", "medical_claim"}
# 用户诉求类：要求停机器人 / 投诉 / 退款退群
_HANDOFF_CODES = {"robot_stop", "complaint", "refund_request"}


def _has_high_risk(analysis: dict | None) -> bool:
    """风险分析里是否有任何 level=high 信号（健康红线：孕哺/慢病/激素药/抑郁危机/疗效承诺等）。"""
    if not isinstance(analysis, dict):
        return False
    for key in ("risk_flags", "intervention_reasons"):
        for item in (analysis.get(key) or []):
            if isinstance(item, dict) and item.get("level") == "high":
                return True
    return False


def _requires_immediate_handoff(analysis: dict | None) -> bool:
    """①层：需立即 @班主任 转人工（真人接手，AI 不再生成回复）。

    触发 = 需人工介入 且满足任一：
    - 任何 level=high 风险（健康红线）
    - requires_immediate_safety_reply=true（正在发生的急症/心理危机/诊疗询问）
    - 急性体感 code（心悸/胸痛/严重失眠/过敏/医学声称）且 level>=medium
    - 用户诉求 code（停机器人/投诉/退款）
    """
    if not isinstance(analysis, dict) or not analysis.get("requires_human_intervention"):
        return False
    if _has_high_risk(analysis):
        return True
    if analysis.get("requires_immediate_safety_reply") is True:
        return True
    flags = analysis.get("risk_flags") or []
    reasons = analysis.get("intervention_reasons") or []
    for item in [*flags, *reasons]:
        if not isinstance(item, dict):
            continue
        code = item.get("code")
        if code in _HANDOFF_CODES:
            return True
        if code in _IMMEDIATE_SAFETY_CODES and item.get("level") in {"medium", "high"}:
            return True
    return False


def _requires_health_handoff(analysis: dict | None, risk_level: str | None) -> bool:
    """持久高风险用户(risk_level=high)问需专业判断的健康/方案问题(health_consultation) → @班主任。

    AI 答不了高风险用户的饮食/运动/用药/方案问题，直接转真人；普通消息(打卡/闲聊/感谢)不在此列、AI 正常回。
    """
    return risk_level == "high" and isinstance(analysis, dict) and bool(analysis.get("health_consultation"))


# AI 可主动选择沉默：模型输出该标记表示本轮无需回复，后端据此不发送、也不入聊天记录。
_NO_REPLY_MARKER = "<no_reply>"


def _reply_is_no_reply(reply: str) -> bool:
    """模型输出中包含 <no_reply> 标记即视为选择不回复（大小写不敏感）。"""
    return _NO_REPLY_MARKER in (reply or "").lower()


def _sanitize_ai_reply(reply: str, settings: Settings) -> str:
    text = (reply or "").strip()
    for marker in ("**", "__", "###", "##", "#"):
        text = text.replace(marker, "")
    online_claim_patterns = (
        r"27\s*[×xX]\s*7\s*(?:都)?(?:在线|陪伴|响应)?",
        r"7\s*[×xX]\s*24\s*(?:小时)?(?:在线|陪伴|响应)?",
        r"24\s*小时(?:都)?(?:在线|陪伴|响应)",
        r"全天(?:都)?(?:在线|陪伴|响应)",
        r"随时(?:都)?(?:在线|响应|回复)",
    )
    for pattern in online_claim_patterns:
        text = re.sub(pattern, "我会尽量及时回应，重要问题班主任也会一起跟进", text, flags=re.IGNORECASE)

    allowed_hosts = {
        host
        for host in (
            urlparse(settings.frontend_public_base_url or "").netloc,
        )
        if host
    }

    def _replace_url(match: re.Match[str]) -> str:
        url = match.group(0)
        host = urlparse(url).netloc
        if host and host in allowed_hosts:
            return url
        return "[链接已隐藏，需人工确认]"

    text = URL_PATTERN.sub(_replace_url, text)
    if len(text) > AI_REPLY_MAX_CHARS:
        text = text[:AI_REPLY_MAX_CHARS].rstrip() + "\n\n（内容较长，我先保留重点，其余部分可由班主任补充。）"
    return text


def _compact_state_profile_for_prompt(state_json: object) -> str:
    if not isinstance(state_json, dict):
        return "暂无动态状态"
    domains = state_json.get("domains")
    if not isinstance(domains, dict):
        return "暂无动态状态"
    lines: list[str] = []
    for key in (
        "action_focus",
        "need_analysis",
        "personal_preferences",
        "lifestyle_habits",
        "goals",
        "body_feedback",
        "product_usage",
        "risk_profile",
        "outcome_changes",
    ):
        domain = domains.get(key)
        if not isinstance(domain, dict):
            continue
        label = str(domain.get("label") or key)
        current = domain.get("current")
        if not isinstance(current, dict):
            continue
        value = current.get("value")
        if not isinstance(value, dict) or not value:
            continue
        if key == "action_focus":
            compact = _action_focus_prompt_value(value)
        else:
            compact = "；".join(
                f"{field}: {_short_prompt_value(field_value)}"
                for field, field_value in list(value.items())[:5]
                if _short_prompt_value(field_value)
            )
        if compact:
            lines.append(f"- {label}：{compact}")
    return "\n".join(lines) if lines else "暂无动态状态"


def _state_action_focus(state_json: object) -> dict[str, object] | None:
    if not isinstance(state_json, dict):
        return None
    domains = state_json.get("domains")
    if not isinstance(domains, dict):
        return None
    action_focus = domains.get("action_focus")
    if not isinstance(action_focus, dict):
        return None
    current = action_focus.get("current")
    if not isinstance(current, dict):
        return None
    value = current.get("value")
    if not isinstance(value, dict) or not value:
        return None
    return {
        "key": value.get("key") or "",
        "label": value.get("label") or "",
        "action": value.get("action") or "",
        "reason": value.get("reason") or "",
        "score": value.get("score") or 0,
        "evidence": value.get("evidence") or [],
    }


def _short_prompt_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "、".join(str(item).strip() for item in value if str(item).strip())[:160]
    if isinstance(value, dict):
        return "；".join(
            str(item).strip()
            for item in value.values()
            if str(item).strip()
        )[:160]
    text = str(value).strip()
    if text in {"未提供", "暂无数据", "None"}:
        return ""
    return text[:160]


def _action_focus_prompt_value(value: dict) -> str:
    focus = value.get("current_focus")
    if not isinstance(focus, dict) or not focus:
        return ""
    label = str(focus.get("label") or "").strip()
    action = str(focus.get("action") or "").strip()
    reason = str(focus.get("reason") or "").strip()
    return "；".join(item for item in (f"当前重点: {label}" if label else "", f"建议动作: {action}" if action else "", f"原因: {reason}" if reason else "") if item)[:260]


def _format_chat_history_line(message: Message) -> str:
    if message.sender_type == MessageSenderType.BOT:
        speaker = "AI助教"
    elif message.sender_type == MessageSenderType.STAFF:
        speaker = "班主任"
    else:
        speaker = "用户"
    content = " ".join((message.content or "").split())
    return f"[{_chat_history_time_label(message.created_at)}] {speaker}: {content}"


def _current_query_speaker(analysis: dict | None) -> str:
    meta = analysis.get("_qiwe_meta") if isinstance(analysis, dict) else None
    sender_type = meta.get("sender_type") if isinstance(meta, dict) else None
    if sender_type == MessageSenderType.STAFF.value:
        return "班主任"
    return "用户"


def _current_query_body(analysis: dict | None, message_text: str) -> str:
    speaker = _current_query_speaker(analysis)
    image_prompt = analysis.get("image_prompt") if isinstance(analysis, dict) else None
    user_text = (message_text or "").strip()
    if not user_text and isinstance(image_prompt, dict):
        user_text = "（发了一张图片）"
    lines = [f"{speaker}：{_prompt_data(user_text)}"]
    if isinstance(image_prompt, dict):
        image_content = str(image_prompt.get("content") or "").strip() or _IMAGE_PARSE_FAILED_PROMPT
        lines.append(f"图片内容：{_prompt_data(image_content)}")
    return "\n".join(lines)


def _chat_history_time_label(created_at: datetime | None) -> str:
    """用北京时间给 recent_chat_messages 加短时间戳，避免模型把历史场景当当前。"""
    if created_at is None:
        return "时间未知"
    tz = ZoneInfo("Asia/Shanghai")
    if created_at.tzinfo is None:
        local_time = created_at.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
    else:
        local_time = created_at.astimezone(tz)
    now = datetime.now(tz)
    day_delta = (now.date() - local_time.date()).days
    if day_delta == 0:
        prefix = "今天"
    elif day_delta == 1:
        prefix = "昨天"
    elif 1 < day_delta <= 6:
        prefix = f"{day_delta}天前"
    else:
        prefix = local_time.strftime("%m-%d")
    return f"{prefix} {local_time:%H:%M}"


async def reconcile_interrupted_sends(startup_time: datetime) -> None:
    """开机对账：把重启前残留的 SENDING(在途)出站记录标为 FAILED。

    正常发送在 record-first 落库后会立即回填 SENT/FAILED；进程重启后仍是 SENDING 的记录，
    说明它在「已落库、但发送结果未回填」的瞬间被中断。按「宁可少发」策略：绝不自动重发
    (避免可能已送达的消息重复刷屏)，只标记为 FAILED 让运营在后台可见、必要时手动补发。
    这些记录仍带 trigger_id，故 recover_stuck_inbound_messages 也会据此判定「已回复」而不再补发。
    """
    from datetime import timedelta

    cutoff = startup_time - timedelta(seconds=15)
    async with SessionLocal() as session:
        rows = list(
            await session.scalars(
                select(Message).where(
                    Message.direction == MessageDirection.OUTBOUND,
                    Message.status == MessageStatus.SENDING,
                    Message.created_at <= cutoff,
                )
            )
        )
        if not rows:
            return
        for row in rows:
            row.status = MessageStatus.FAILED
            if not row.error:
                row.error = "interrupted during send by restart; not auto-resent (send-once policy)"
        await session.commit()
        logger.info(
            "reconcile_interrupted_sends: marked %d interrupted SENDING outbound messages as FAILED: %s",
            len(rows),
            [r.id for r in rows],
        )


async def mark_outbound_blocked_by_channel_id(channel_message_id: str, reason: str) -> bool:
    """企微风控拦截对账：收到 spam_block 通知后，把对应出站消息标为 FAILED。

    发送时渠道 API 会当场返回成功、消息记为 SENT；但账号被风控时，企微随后会异步回一条
    spam_block 通知（msgType=1011），说明这条其实没能进群。此处按通知携带的渠道消息 id
    (wecom_message_id) 回查那条已发记录，改为 FAILED 并写入风控原因，让后台如实反映「没送达」。
    只降级 SENT/SENDING；已是 FAILED 等终态则视为已对账、不重复改写。返回是否命中一条记录。
    """
    channel_message_id = (channel_message_id or "").strip()
    if not channel_message_id:
        return False
    async with SessionLocal() as session:
        row = await session.scalar(
            select(Message).where(
                Message.direction == MessageDirection.OUTBOUND,
                Message.wecom_message_id == channel_message_id,
            )
        )
        if row is None:
            logger.warning(
                "mark_outbound_blocked_by_channel_id: no outbound message for channel_message_id=%s",
                channel_message_id,
            )
            return False
        if row.status not in (MessageStatus.SENT, MessageStatus.SENDING):
            logger.info(
                "mark_outbound_blocked_by_channel_id: outbound message id=%s already status=%s channel_message_id=%s",
                row.id,
                row.status,
                channel_message_id,
            )
            return True
        row.status = MessageStatus.FAILED
        row.error = reason or "企业微信官方风控拦截：消息未送达"
        row.sent_at = None
        await session.commit()
        logger.warning(
            "mark_outbound_blocked_by_channel_id: marked message id=%s as FAILED (QiWe spam_block) channel_message_id=%s",
            row.id,
            channel_message_id,
        )
        return True


async def recover_stuck_inbound_messages(channel: MessageChannel, startup_time: datetime) -> None:
    """Scan and reprocess inbound user messages that were received but never processed (e.g. due to service restart).
    This function is run on application startup to recover any messages that were lost/interrupted during deployment restart.
    """
    from datetime import datetime, timedelta, timezone
    from app.models.service_case import ServiceCaseStatus
    
    # Only scan messages received before startup_time - 15 seconds, and within the last 2 hours.
    max_created_at = startup_time - timedelta(seconds=15)
    min_created_at = startup_time - timedelta(hours=2)
    
    async with SessionLocal() as session:
        # Get active service cases
        cases = list(
            await session.scalars(
                select(ServiceCase)
                .where(
                    # 退款客户仍保留群、正常聊天：REFUNDED 也要能在重启后回复在途消息（只停产品类主动推送）。
                    ServiceCase.status.in_(
                        [
                            ServiceCaseStatus.APPROVED,
                            ServiceCaseStatus.IN_SERVICE,
                            ServiceCaseStatus.REFUNDED,
                        ]
                    ),
                    ServiceCase.ai_auto_reply_enabled == True,
                    ServiceCase.deleted_at.is_(None),
                )
            )
        )
        if not cases:
            return

        stuck_messages = []
        for case in cases:
            # Query the latest inbound user chat message in the last 2 hours for this case
            latest_inbound = await session.scalar(
                select(Message)
                .where(
                    Message.service_case_id == case.id,
                    Message.direction == MessageDirection.INBOUND,
                    Message.sender_type == MessageSenderType.USER,
                    Message.purpose == MessagePurpose.CHAT,
                    Message.message_type == "text",
                    Message.created_at >= min_created_at,
                    Message.created_at <= max_created_at,
                )
                .order_by(Message.id.desc())
                .limit(1)
            )
            if not latest_inbound:
                continue

            # Skip if it was already processed or claimed by another recovery task.
            aj = latest_inbound.analysis_json or {}
            if _message_already_processed_or_claimed(aj):
                continue

            # Check if there is already any outbound reply (sent/pending/failed) for this message
            reply_exists = await session.scalar(
                select(Message.id)
                .where(
                    Message.direction == MessageDirection.OUTBOUND,
                    Message.trigger_id == latest_inbound.id,
                )
                .limit(1)
            )
            if not reply_exists:
                stuck_messages.append(latest_inbound)

        if not stuck_messages:
            return

        logger.info(
            "recover_stuck_inbound_messages: found %d stuck inbound user messages to reprocess: %s",
            len(stuck_messages),
            [m.id for m in stuck_messages]
        )

    # Process messages outside session block to avoid holding connections/locks
    engine = RanrenAIEngine(channel)
    for msg in stuck_messages:
        try:
            await engine.reprocess_message(msg)
        except Exception:
            logger.exception("Failed to reprocess stuck message id=%s", msg.id)


def _message_already_processed_or_claimed(analysis_json: dict | None) -> bool:
    if not isinstance(analysis_json, dict):
        return False
    if any(key in analysis_json for key in ("requires_human_intervention", "outcome", "agent")):
        return True
    claim = analysis_json.get("_recovery_claim")
    if not isinstance(claim, dict):
        return False
    claimed_at = _parse_datetime(claim.get("claimed_at"))
    if claimed_at is None:
        return True
    return (datetime.now(timezone.utc) - claimed_at).total_seconds() < 600


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
