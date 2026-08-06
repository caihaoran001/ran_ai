"""入站语音消息的转写编排。

语音在业务上就是用户说的话，只是载体不同。这里把它转成文字后**当作普通文本消息**
交回消息链路，从而能进 8 秒聚合窗口、触发回复、写入画像与成效——和用户打字发送等效。

转写必须发生在「解析出 ChannelMessage 之后、`_bypass_aggregator` 判断之前」：非文本
消息会绕过聚合窗口直接留痕，只有先转成文本才能走正常回合。

转写结果回写进事件的 raw_payload（`msgData.voiceToText`），解析器据此把这条消息认成
文本。因此重启重放同一事件时不会再调一次转写接口。

任何一步失败都退回原来的语音消息，保持「记一行[语音]、不回复」的既有行为——转不出
文字不能中断整条入站链路。
"""

from __future__ import annotations

import copy
import logging
from typing import Any

from app.db.session import SessionLocal
from app.models.inbound_event import InboundEvent
from app.services.channels.base import ChannelMessage


logger = logging.getLogger("uvicorn.error")

# 转写文本在 msgData 里的落点。企微原始回调不含此字段，由我们写入。
VOICE_TRANSCRIPT_FIELD = "voiceToText"


def _voice_payload(message: ChannelMessage) -> dict[str, Any] | None:
    payload = message.raw_payload
    if not isinstance(payload, dict):
        return None
    return payload


async def transcribe_inbound_voice(message: ChannelMessage) -> ChannelMessage:
    """语音 → 文本消息；非语音或转写失败时原样返回。"""
    if message.message_type != "voice":
        return message

    payload = _voice_payload(message)
    if payload is None:
        return message

    msg_data = payload.get("msgData") if isinstance(payload.get("msgData"), dict) else {}
    if str(msg_data.get(VOICE_TRANSCRIPT_FIELD) or "").strip():
        # 已转写过（重放路径），交给解析器即可，不重复调用网关。
        return message

    msg_server_id = payload.get("msgServerId")
    # 必须用收到这条语音的那台设备发起转写：账号换号后全局 guid 会与它不一致。
    device_guid = str(payload.get("guid") or "").strip()

    transcript = await _transcribe(msg_server_id, device_guid)
    if not transcript:
        return message

    updated_payload = copy.deepcopy(payload)
    updated_msg_data = updated_payload.get("msgData")
    if not isinstance(updated_msg_data, dict):
        updated_msg_data = {}
        updated_payload["msgData"] = updated_msg_data
    updated_msg_data[VOICE_TRANSCRIPT_FIELD] = transcript

    await _persist_transcript(message.inbound_event_ids, updated_payload, transcript)

    from app.api.routes.qiwe import _channel_message_from_qiwe

    parsed = _channel_message_from_qiwe(updated_payload)
    if parsed is None:
        logger.warning(
            "QiWe voice transcript could not be re-parsed message_id=%s",
            message.message_id,
        )
        return message
    parsed.inbound_event_ids = list(message.inbound_event_ids)
    logger.info(
        "QiWe voice transcribed chat_id=%s message_id=%s voice_seconds=%s chars=%d",
        message.chat_id,
        message.message_id,
        msg_data.get("voiceTime"),
        len(transcript),
    )
    return parsed


async def transcribe_and_dispatch(
    aggregator: Any,
    message: ChannelMessage,
    *,
    inbound_event_id: int | None = None,
) -> None:
    """转写（若为语音）后再决定走聚合窗口还是立即处理。

    生产由收件箱 dispatcher 调用，本地/测试单体 app 由回调路由的后台任务调用；
    两条路径共用这里，避免路由判定出现两套。
    """
    from app.api.routes.qiwe import _bypass_aggregator

    message = await transcribe_inbound_voice(message)
    if _bypass_aggregator(message):
        await aggregator.dispatch_now(message, inbound_event_id=inbound_event_id)
    else:
        await aggregator.enqueue(message, inbound_event_id=inbound_event_id)


async def _transcribe(msg_server_id: Any, device_guid: str) -> str | None:
    from app.services.channels import get_current_channel

    channel = get_current_channel()
    transcribe = getattr(channel, "transcribe_voice", None)
    if transcribe is None:
        return None
    try:
        return await transcribe(msg_server_id, guid=device_guid or None)
    except Exception:
        logger.exception(
            "QiWe voice transcribe raised msg_server_id=%s", msg_server_id
        )
        return None


async def _persist_transcript(
    event_ids: list[int],
    updated_payload: dict[str, Any],
    transcript: str,
) -> None:
    """把转写结果落回收件箱事件，保证重启重放拿到的是同一份文本。

    落库失败只记日志：本轮消息已经拿到文字，能正常回复；最坏情况是重启后重放时
    再转一次，不影响正确性。
    """
    ids = [event_id for event_id in event_ids if event_id]
    if not ids:
        return
    try:
        async with SessionLocal() as session:
            for event_id in ids:
                event = await session.get(InboundEvent, event_id)
                if event is None:
                    continue
                event.raw_payload = updated_payload
                event.text = transcript
                event.message_type = "text"
            await session.commit()
    except Exception:
        logger.exception("QiWe voice transcript persist failed event_ids=%s", ids)
