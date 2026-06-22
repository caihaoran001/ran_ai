from __future__ import annotations

from datetime import datetime, timezone
import hmac
import logging
import time
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import PlainTextResponse

from app.core.config import Settings
from app.core.config import get_settings
from app.services.channels import get_current_channel, set_current_channel
from app.services.channels.base import ChannelMessage
from app.services.channels.qiwe import QiWeChannel
from app.services.qiwe_service.aggregator import QiWeMessageAggregator

router = APIRouter()
logger = logging.getLogger("uvicorn.error")


@router.post("/channels/qiwe/callback", response_class=PlainTextResponse)
async def qiwe_callback(request: Request, background_tasks: BackgroundTasks) -> str:
    received_at = time.monotonic()
    settings = get_settings()
    if not _is_authorized_callback(request, settings):
        raise HTTPException(status_code=403, detail="invalid qiwe callback secret")

    payload = await request.json()
    import json
    logger.info("QiWe callback raw payload: %s", json.dumps(payload, ensure_ascii=False))
    if payload.get("code") != 0:
        return ""

    aggregator = _get_aggregator(request)
    accepted = 0
    bypassed = 0
    for item in payload.get("data") or []:
        message = _channel_message_from_qiwe(item)
        if message is not None:
            accepted += 1
            if _bypass_aggregator(message):
                bypassed += 1
                logger.info(
                    "QiWe callback command bypass chat_id=%s user_id=%s message_id=%s text=%s",
                    message.chat_id,
                    message.user_id,
                    message.message_id,
                    message.text[:40],
                )
                background_tasks.add_task(aggregator.dispatch_now, message)
            else:
                background_tasks.add_task(aggregator.enqueue, message)
    logger.info(
        "QiWe callback accepted count=%s bypassed=%s elapsed_ms=%.1f",
        accepted,
        bypassed,
        (time.monotonic() - received_at) * 1000,
    )
    return ""


def _is_authorized_callback(request: Request, settings: Settings) -> bool:
    secret = settings.qiwei_callback_secret.strip()
    if not secret:
        return True
    supplied = (
        request.headers.get("x-qiwei-callback-secret")
        or request.query_params.get("secret")
        or ""
    )
    return hmac.compare_digest(supplied, secret)


def _bypass_aggregator(message: ChannelMessage) -> bool:
    # 非文本(表情/图片等)绕过聚合，直接走 engine：只入库留痕、不回复，且不与文本消息合并
    if message.message_type != "text":
        return True
    return "发送本群入营问卷" in message.text or "发送本群专属问卷" in message.text


def _get_aggregator(request: Request) -> QiWeMessageAggregator:
    aggregator = getattr(request.app.state, "qiwe_aggregator", None)
    if aggregator is not None:
        return aggregator
    settings = get_settings()
    channel = get_current_channel()
    if channel is None:
        channel = QiWeChannel(settings)
        set_current_channel(channel)
    aggregator = QiWeMessageAggregator(
        channel,
        window_seconds=settings.qiwei_batch_window_seconds,
    )
    request.app.state.qiwe_aggregator = aggregator
    return aggregator


def _channel_message_from_qiwe(item: dict[str, Any]) -> ChannelMessage | None:
    if item.get("cmd") != 15000:
        return None

    msg_type = item.get("msgType")
    is_text = msg_type in (0, 2)
    msg_data = item.get("msgData") or {}
    content = str(msg_data.get("content") or "").strip()
    if is_text:
        if not content:
            return None
        message_type = "text"
    else:
        if msg_type in (7, 14, 101):
            message_type = "image"
            content = content or "[图片]"
        elif msg_type in (15, 20, 102):
            message_type = "file"
            content = content or "[文件]"
        elif msg_type == 16:
            message_type = "voice"
            content = content or "[语音]"
        else:
            message_type = "non_text"
            content = content or "[表情/图片等非文本消息]"

    bot_user_id = _as_str(item.get("userId"))
    sender_id = _as_str(item.get("senderId") or msg_data.get("sender_id"))
    if bot_user_id and sender_id and bot_user_id == sender_id:
        return None

    from_room_id = _as_str(item.get("fromRoomId"))
    msg_chat_id = _as_str(msg_data.get("chat_id") or msg_data.get("chatId"))
    is_group = bool(msg_chat_id) or (bool(from_room_id) and from_room_id != "0")
    chat_id = msg_chat_id or (from_room_id if is_group else sender_id)
    if not chat_id:
        return None

    return ChannelMessage(
        channel="qiwe",
        chat_id=chat_id,
        chat_type="group" if is_group else "private",
        text=content,
        message_id=_as_str(item.get("msgUniqueIdentifier")),
        user_id=sender_id,
        user_name=str(item.get("senderName") or msg_data.get("sender_name") or ""),
        message_type=message_type,
        raw_payload=item,
        timestamp=_timestamp(item.get("timestamp")),
    )


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _timestamp(value: Any) -> datetime:
    try:
        return datetime.fromtimestamp(int(value), timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)
