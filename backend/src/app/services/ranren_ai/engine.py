from __future__ import annotations

import logging
import secrets
from datetime import datetime

from sqlalchemy import select

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
from app.services.ai_service import AIService
from app.services.channels.base import ChannelMessage, MessageChannel
from app.services.checkin_service import create_checkin_from_message, is_checkin_message
from app.services.knowledge_base import retrieve_knowledge
from app.services.reminder_service import mark_seen

logger = logging.getLogger(__name__)


class RanrenAIEngine:
    """Channel-agnostic business engine for inbound customer messages."""

    def __init__(self, channel: MessageChannel, settings: Settings | None = None):
        self.channel = channel
        self.settings = settings or get_settings()

    async def handle_message(self, message: ChannelMessage) -> None:
        should_send_questionnaire = "发送本群入营问卷" in message.text
        async with SessionLocal() as session:
            service_case = await session.scalar(
                select(ServiceCase).where(
                    ServiceCase.group_id == message.chat_id,
                    ServiceCase.deleted_at.is_(None),
                )
            )
            if should_send_questionnaire and not service_case:
                service_case = await self._create_case_for_message(session, message)

            purpose = MessagePurpose.CHAT
            if service_case and is_checkin_message(
                message.text,
                self.settings.reminder_required_pattern or r"^#打卡",
            ):
                await create_checkin_from_message(
                    session,
                    service_case=service_case,
                    content=message.text,
                    raw_message_id=message.message_id,
                )
                purpose = MessagePurpose.CHECKIN
                await mark_seen(service_case.id)

            session.add(
                Message(
                    service_case_id=service_case.id if service_case else None,
                    group_id=message.chat_id,
                    wecom_message_id=message.message_id,
                    direction=MessageDirection.INBOUND,
                    sender_type=MessageSenderType.USER,
                    message_type=message.message_type,
                    content=message.text,
                    purpose=purpose,
                    status=MessageStatus.RECEIVED,
                )
            )
            await session.commit()

        if should_send_questionnaire:
            await self._reply_questionnaire_link(message, service_case)
            return
        if purpose == MessagePurpose.CHAT:
            await self._reply_with_ai(message)

    async def _create_case_for_message(
        self, session, message: ChannelMessage
    ) -> ServiceCase:
        user = None
        if message.channel == "wecom" and message.user_id:
            user = await session.scalar(
                select(User).where(
                    User.wecom_user_id == message.user_id,
                    User.deleted_at.is_(None),
                )
            )
        if not user:
            user = User(
                name="待填写问卷用户",
                wecom_user_id=message.user_id if message.channel == "wecom" else None,
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
        self, message: ChannelMessage, service_case: ServiceCase | None
    ) -> None:
        if not service_case:
            return
        base_url = self.settings.frontend_public_base_url.rstrip("/")
        link = f"{base_url}/q/{service_case.questionnaire_token}"
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
        result = await self.channel.send_text(
            message.chat_id,
            reply,
            reply_to=message.message_id,
        )
        await self._record_outbound(
            message=message,
            service_case_id=service_case.id,
            content=reply,
            message_id=result.message_id,
            status=MessageStatus.SENT if result.success else MessageStatus.FAILED,
            error=result.error,
        )

    async def _reply_with_ai(self, message: ChannelMessage) -> None:
        ai = AIService()
        knowledge_context = retrieve_knowledge(message.text)
        try:
            reply = await ai.generate_text(
                system_prompt=(
                    "你是企业微信里的燃AI助手。请简洁、准确、友好地回复。"
                    "如果知识库命中内容与用户问题相关，优先依据知识库回答；"
                    "如果没有相关内容，就按常识和当前业务上下文回答，不要编造内部规则。"
                ),
                user_prompt=(
                    f"用户问题：\n{message.text}\n\n"
                    f"知识库检索结果：\n{knowledge_context or '无'}"
                ),
            )
        except Exception:
            logger.exception("AI reply failed")
            reply = "抱歉，我刚才处理这条消息时出错了，请稍后再试。"

        result = await self.channel.send_text(
            message.chat_id,
            reply,
            reply_to=message.message_id,
        )
        async with SessionLocal() as session:
            service_case = await session.scalar(
                select(ServiceCase).where(
                    ServiceCase.group_id == message.chat_id,
                    ServiceCase.deleted_at.is_(None),
                )
            )
        await self._record_outbound(
            message=message,
            service_case_id=service_case.id if service_case else None,
            content=reply,
            message_id=result.message_id,
            status=MessageStatus.SENT if result.success else MessageStatus.FAILED,
            error=result.error,
        )

    async def _record_outbound(
        self,
        *,
        message: ChannelMessage,
        service_case_id: int | None,
        content: str,
        message_id: str | None,
        status: MessageStatus,
        error: str | None,
    ) -> None:
        async with SessionLocal() as session:
            session.add(
                Message(
                    service_case_id=service_case_id,
                    group_id=message.chat_id,
                    wecom_message_id=message_id,
                    direction=MessageDirection.OUTBOUND,
                    sender_type=MessageSenderType.BOT,
                    message_type="markdown",
                    content=content,
                    purpose=MessagePurpose.CHAT,
                    status=status,
                    error=error,
                )
            )
            await session.commit()
