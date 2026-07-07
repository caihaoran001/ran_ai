from __future__ import annotations

from datetime import datetime, timezone
import hmac
import logging
import re
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
from app.services.qiwe_service.inbox import (
    persist_qiwe_spam_block_event,
    process_qiwe_spam_block_event,
    qiwe_spam_block_notice,
)

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
    blocked = 0
    for item in payload.get("data") or []:
        spam_block = _spam_block_notice(item)
        if spam_block is not None:
            channel_message_id, reason = spam_block
            blocked += 1
            logger.warning(
                "QiWe callback spam_block notice channel_message_id=%s reason=%s",
                channel_message_id,
                reason,
            )
            event_id = await persist_qiwe_spam_block_event(
                channel_message_id=channel_message_id,
                reason=reason,
                raw_payload=item,
            )
            background_tasks.add_task(
                process_qiwe_spam_block_event, event_id, channel_message_id, reason
            )
            continue
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
        "QiWe callback accepted count=%s bypassed=%s blocked=%s elapsed_ms=%.1f",
        accepted,
        bypassed,
        blocked,
        (time.monotonic() - received_at) * 1000,
    )
    return ""


def _is_authorized_callback(request: Request, settings: Settings) -> bool:
    secret = settings.qiwei_callback_secret.strip()
    # fail-closed：未配置回调密钥时拒绝所有回调，而非放行。
    # 线上 compose 用 ${QIWEI_CALLBACK_SECRET:?required} 强制注入，密钥必非空，
    # 故此分支只在本地/测试未配密钥时触发，不影响线上(线上回调 URL 内嵌 ?secret=)。
    if not secret:
        return False
    supplied = (
        request.headers.get("x-qiwei-callback-secret")
        or request.query_params.get("secret")
        or ""
    )
    return hmac.compare_digest(supplied, secret)


def _bypass_aggregator(message: ChannelMessage) -> bool:
    # 图片进入聚合窗口：用户常见操作是先发图、再补一句问题，需要合成一轮回复。
    if message.message_type == "image":
        return False
    # 文件/语音/表情等仍绕过聚合，直接走 engine 的对应留痕/回执逻辑。
    if message.message_type != "text":
        return True
    # 群内显式@机器人并带正文：立即作为一次独立提问处理，避免被8秒聚合窗口混入普通发言
    if _mentions_qiwe_bot(message) and _strip_leading_mentions(message.text):
        return True
    # 群内@机器人控制命令需要立即生效，不等待8秒聚合窗口
    msg_data = (message.raw_payload or {}).get("msgData") if isinstance(message.raw_payload, dict) else None
    at_list = msg_data.get("atList") if isinstance(msg_data, dict) else None
    if at_list and ("退下" in message.text or "回来" in message.text):
        return True
    return "发送本群入营问卷" in message.text or "发送本群专属问卷" in message.text


def _mentions_qiwe_bot(message: ChannelMessage) -> bool:
    payload = message.raw_payload if isinstance(message.raw_payload, dict) else {}
    msg_data = payload.get("msgData") if isinstance(payload.get("msgData"), dict) else {}
    at_list = msg_data.get("atList") or []
    if not isinstance(at_list, list) or not at_list:
        return False

    bot_user_id = _as_str(payload.get("userId") or payload.get("receiverId")).strip()
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


def _strip_leading_mentions(text: str) -> str:
    return re.sub(r"^(?:@\S+\s*)+", "", " ".join((text or "").split()).strip()).strip()


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


def _spam_block_notice(item: dict[str, Any]) -> tuple[str, str] | None:
    """识别企微风控拦截通知(msgType=1011)：返回(被拦截消息的渠道id, 风控原因文案)。

    通知形如 msgUniqueIdentifier="spam_block_<channel_message_id>"，正文(base64RawData)
    解码后是「因账号违规，当前功能已被限制使用。查看详情」之类。非该类通知返回 None。
    """
    return qiwe_spam_block_notice(item)


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
