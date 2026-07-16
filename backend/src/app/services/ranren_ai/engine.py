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
from app.services.ai_service import AIService
from app.services.agent import AgentDataUnavailableError, generate_agent_reply
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
from app.services.service_case_queries import select_active_service_cases

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
        # 转人工@班主任的短期防刷屏：每个 service_case 30 分钟内不重复@。
        # 每个新风险事件仍会写入消息分析；节流时间戳持久化在
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
        from app.services.ranren_ai.engine_graph import run_handle_message

        await run_handle_message(self, message)

    async def reprocess_message(self, message: Message) -> None:
        """Reprocess a stuck inbound USER message (e.g., interrupted by container restart)."""
        from app.services.ranren_ai.engine_graph import run_reprocess_message

        await run_reprocess_message(self, message)

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
        from app.services.ranren_ai.engine_graph import run_reply_with_ai

        await run_reply_with_ai(
            self,
            message,
            service_case_id=service_case_id,
            analysis=analysis,
            trigger_id=trigger_id,
        )

    async def _maybe_followup_after_reply(
        self, *, message: ChannelMessage, service_case_id: int | None
    ) -> None:
        """活跃触发·打卡追问：agent 回复成功后，确定性地补发一条追问（不揉进原回复）。

        每 case 每天最多一条（rule_id=checkin_followup 天闸，与不活跃兜底共用，绝不叠加）。
        先算出有内容再抢名额，避免无内容却占掉当天名额。全程兜底，任何异常只记日志，
        绝不影响已发出的正常回复。
        """
        settings = get_settings()
        if not settings.checkin_followup_enabled or not service_case_id:
            return
        try:
            from app.services.archivist.checkin_projection import (
                mark_contradictions_pending_confirmation,
            )
            from app.services.archivist.followup import compose_daily_followup
            from app.services.reminder_service import _parse_time, _reserve_reminder_event
            from app.models.reminder import ReminderEventType
            from app.models.checkin import Checkin

            tz = ZoneInfo(settings.reminder_timezone)
            now = datetime.now(tz)
            today = now.date()
            async with SessionLocal() as session:
                service_case = await session.scalar(
                    select_active_service_cases(ServiceCase.id == service_case_id)
                )
                if service_case is None:
                    return
                followup = await compose_daily_followup(
                    session, service_case=service_case, now=now, tz=tz
                )
                if followup is None:
                    return
                # 日常缺口(早C/午B)只在傍晚窗口发；此刻不在窗口→不发、不占天闸(晚上兜底轮询会补)。
                # 里程碑/矛盾核对不受窗口约束，活跃时即时补发。
                if followup.kind == "daily_gap":
                    start = _parse_time(settings.checkin_followup_window_start)
                    end = _parse_time(settings.checkin_followup_window_end)
                    if not (start <= now.time() < end):
                        return
                reminder_event = await _reserve_reminder_event(
                    session,
                    service_case.id,
                    rule_id="checkin_followup",
                    event_date=today,
                    event_type=ReminderEventType.REMINDED,
                )
                if not reminder_event:
                    return  # 今天已被占（活跃再触发/不活跃兜底），不重复
                row = Message(
                    service_case_id=service_case.id,
                    group_id=message.chat_id,
                    direction=MessageDirection.OUTBOUND,
                    sender_type=MessageSenderType.SYSTEM,
                    message_type="markdown",
                    content=followup.text,
                    purpose=MessagePurpose.REMINDER,
                    status=MessageStatus.SENDING,
                )
                session.add(row)
                await session.flush()
                result = await self.channel.send_text(message.chat_id, followup.text)
                if result.success:
                    row.status = MessageStatus.SENT
                    row.wecom_message_id = result.message_id
                    row.sent_at = datetime.now(timezone.utc)
                    if followup.kind == "contradiction" and followup.confirmation_fields:
                        today_checkin = await session.scalar(
                            select(Checkin).where(
                                Checkin.service_case_id == service_case.id,
                                Checkin.checkin_date == today,
                                Checkin.deleted_at.is_(None),
                            )
                        )
                        if today_checkin is not None:
                            mark_contradictions_pending_confirmation(
                                today_checkin, followup.confirmation_fields
                            )
                else:
                    row.status = MessageStatus.FAILED
                    row.error = result.error
                await session.commit()
                logger.info(
                    "RanrenAI active checkin followup sent chat_id=%s service_case_id=%s kind=%s success=%s",
                    message.chat_id,
                    service_case_id,
                    followup.kind,
                    result.success,
                )
        except Exception:
            logger.exception(
                "Active checkin followup failed service_case_id=%s", service_case_id
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

    async def _build_intervention_context(self, session, *, service_case: ServiceCase | None) -> dict:
        if not service_case:
            return {}
        return {
            "current_risk_level": service_case.risk_level,
            "intervention_reasons": service_case.intervention_reasons or [],
            "risk_flags": service_case.risk_flags or [],
        }


def _prompt_data(value: object) -> str:
    """Escape user-controlled text before placing it inside prompt data tags."""
    return escape(str(value or ""), quote=False)


def _requires_health_handoff(analysis: dict | None, risk_level: str | None) -> bool:
    """持久高风险用户(risk_level=high)问需专业判断的健康/方案问题(health_consultation) → @班主任。

    AI 答不了高风险用户的饮食/运动/用药/方案问题，直接转真人；普通消息(打卡/闲聊/感谢)不在此列、AI 正常回。
    """
    return risk_level == "high" and isinstance(analysis, dict) and bool(analysis.get("health_consultation"))


# AI 可主动选择沉默：模型输出该标记表示本轮无需回复，后端据此不发送、也不入聊天记录。
# 用正则容忍模型的常见格式漂移：<no_reply>、<no_reply/>、< no_reply />、大小写混写等，
# 避免自闭合等变体漏判后把内部信号词当正文发进群。
_NO_REPLY_RE = re.compile(r"<\s*no_reply\s*/?\s*>", re.IGNORECASE)


def _reply_is_no_reply(reply: str) -> bool:
    """模型输出中包含 <no_reply>（含 <no_reply/> 等变体）标记即视为选择不回复。"""
    return bool(_NO_REPLY_RE.search(reply or ""))


def _sanitize_ai_reply(reply: str, settings: Settings) -> str:
    text = (reply or "").strip()
    # 防御性兜底：即便 no-reply 判断被绕过（如夹带正文的变体），也不把内部信号词当正文发出去。
    text = _NO_REPLY_RE.sub("", text).strip()
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


def _format_chat_history_line(message: Message) -> str:
    if message.sender_type == MessageSenderType.BOT:
        speaker = "助教"
    elif message.sender_type == MessageSenderType.STAFF:
        speaker = "班主任"
    else:
        speaker = "用户"
    content = " ".join((message.content or "").split())
    return f"[{_chat_history_time_label(message.created_at)}] {speaker}: {content}"


def _agent_gap_failed_tools(tool_trace: list[dict] | None) -> list[str]:
    """从 tool_trace 里挑出失败/熔断的工具名(去重保序),供缺口留痕。"""
    names: list[str] = []
    for trace in tool_trace or []:
        if isinstance(trace, dict) and trace.get("ok") is False:
            name = trace.get("name")
            if isinstance(name, str) and name and name not in names:
                names.append(name)
    return names


def _build_agent_info_gap(
    *, query: str, tool_trace: list[dict] | None, data_unavailable: bool
) -> dict[str, Any] | None:
    """把 Agent 当轮暴露的"信息缺口"结构化留痕(供下游按需消费;本版只写不读)。

    背景:第 2 点——Agent 命中"查不到数据/工具熔断"时当场就知道有缺口,信号原本只有零散布尔旗、
    无人消费。这里统一沉淀成 analysis_json.agent.info_gap,让"知识库缺口发现/运营排查"等消费方
    以后按需接入,把单向协作补成有反馈。**topic 取触发本轮的用户原问(已在手,零额外 LLM 调用)**,
    刻意不在热路径为打 topic 再调一次模型。type 区分"我们数据查不到"(data_unavailable)与
    "有工具失败但仍答上了"(tool_failed)。无缺口返回 None。
    """
    tools = _agent_gap_failed_tools(tool_trace)
    if data_unavailable:
        gap_type = "data_unavailable"
    elif tools:
        gap_type = "tool_failed"
    else:
        return None
    gap: dict[str, Any] = {"type": gap_type}
    topic = (query or "").strip()
    if topic:
        gap["query"] = topic[:200]  # 截断,防超长撑爆 analysis_json
    if tools:
        gap["tools"] = tools
    return gap


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
