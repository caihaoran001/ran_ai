from __future__ import annotations

import asyncio
import base64
import hashlib
import inspect
import json
import logging
import mimetypes
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

import aiohttp
import httpx

from .access import WeComAccessController
from .config import WeComConfig
from .dedup import MessageDeduplicator
from .media import (
    ABSOLUTE_MAX_BYTES,
    MAX_UPLOAD_CHUNKS,
    UPLOAD_CHUNK_SIZE,
    MediaManager,
    detect_wecom_media_type,
)
from .models import BridgeEvent, ChatSource, MessageType, SendResult, WeComMessage

logger = logging.getLogger(__name__)

APP_CMD_SUBSCRIBE = "aibot_subscribe"
APP_CMD_CALLBACK = "aibot_msg_callback"
APP_CMD_LEGACY_CALLBACK = "aibot_callback"
APP_CMD_EVENT_CALLBACK = "aibot_event_callback"
APP_CMD_SEND = "aibot_send_msg"
APP_CMD_RESPONSE = "aibot_respond_msg"
APP_CMD_PING = "ping"
APP_CMD_UPLOAD_MEDIA_INIT = "aibot_upload_media_init"
APP_CMD_UPLOAD_MEDIA_CHUNK = "aibot_upload_media_chunk"
APP_CMD_UPLOAD_MEDIA_FINISH = "aibot_upload_media_finish"

CALLBACK_COMMANDS = {APP_CMD_CALLBACK, APP_CMD_LEGACY_CALLBACK}
NON_RESPONSE_COMMANDS = CALLBACK_COMMANDS | {APP_CMD_EVENT_CALLBACK}

MAX_MESSAGE_LENGTH = 4000
CONNECT_TIMEOUT_SECONDS = 20.0
REQUEST_TIMEOUT_SECONDS = 15.0
HEARTBEAT_INTERVAL_SECONDS = 30.0
RECONNECT_BACKOFF = [2, 5, 10, 30, 60]
DEDUP_MAX_SIZE = 1000
SPLIT_THRESHOLD = 3900

MessageHandler = Callable[[WeComMessage], Awaitable[None] | None]
EventHandler = Callable[[BridgeEvent], Awaitable[None] | None]


class WeComBotBridge:
    """WeCom AI Bot WebSocket bridge.

    The bridge is intentionally framework-free. Callers pass a message handler
    callback and decide how to route messages into their own agent/application.
    """

    MAX_MESSAGE_LENGTH = MAX_MESSAGE_LENGTH

    def __init__(
        self,
        config: WeComConfig,
        *,
        message_handler: Optional[MessageHandler] = None,
        event_handler: Optional[EventHandler] = None,
        media_manager: Optional[MediaManager] = None,
    ):
        self.config = config
        self.message_handler = message_handler
        self.event_handler = event_handler
        self.access = WeComAccessController.from_config(config)
        self.media = media_manager or MediaManager(config.cache_dir)

        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._http_client: Optional[httpx.AsyncClient] = None
        self._listen_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._pending_responses: dict[str, asyncio.Future] = {}
        self._dedup = MessageDeduplicator(max_size=DEDUP_MAX_SIZE)
        self._reply_req_ids: dict[str, str] = {}
        self._last_chat_req_ids: dict[str, str] = {}
        self._pending_text_batches: dict[str, WeComMessage] = {}
        self._pending_text_batch_tasks: dict[str, asyncio.Task] = {}
        self._device_id = uuid.uuid4().hex
        self._running = False
        self._connected = False
        self.last_error: Optional[str] = None

    @property
    def running(self) -> bool:
        return self._running

    @property
    def connected(self) -> bool:
        return self._connected and bool(self._ws and not self._ws.closed)

    async def connect(self) -> bool:
        if not self.config.bot_id or not self.config.secret:
            self.last_error = "WECOM_BOT_ID and WECOM_SECRET are required"
            await self._emit("error", self.last_error)
            return False

        self._running = True
        try:
            self._http_client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)
            self.media.http_client = self._http_client
            await self._open_connection()
            self._connected = True
            self._listen_task = asyncio.create_task(self._listen_loop())
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            await self._emit("connected", f"Connected to {self.config.websocket_url}")
            return True
        except Exception as exc:
            self.last_error = str(exc)
            await self._emit("error", f"WeCom startup failed: {exc}")
            logger.error("Failed to connect to WeCom: %s", exc, exc_info=True)
            self._running = False
            self._connected = False
            await self._cleanup_ws()
            if self._http_client:
                await self._http_client.aclose()
                self._http_client = None
            return False

    async def disconnect(self) -> None:
        self._running = False
        self._connected = False

        for task in (self._listen_task, self._heartbeat_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._listen_task = None
        self._heartbeat_task = None

        for task in list(self._pending_text_batch_tasks.values()):
            if not task.done():
                task.cancel()
        self._pending_text_batch_tasks.clear()
        self._pending_text_batches.clear()

        self._fail_pending_responses(RuntimeError("WeCom bridge disconnected"))
        await self._cleanup_ws()
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None
        self._dedup.clear()
        await self._emit("disconnected", "Disconnected")

    async def _cleanup_ws(self) -> None:
        if self._ws and not self._ws.closed:
            await self._ws.close()
        self._ws = None

        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def _open_connection(self) -> None:
        await self._cleanup_ws()
        self._session = aiohttp.ClientSession(trust_env=True)
        self._ws = await self._session.ws_connect(
            self.config.websocket_url,
            heartbeat=HEARTBEAT_INTERVAL_SECONDS * 2,
            timeout=CONNECT_TIMEOUT_SECONDS,
        )

        req_id = self._new_req_id("subscribe")
        await self._send_json(
            {
                "cmd": APP_CMD_SUBSCRIBE,
                "headers": {"req_id": req_id},
                "body": {
                    "bot_id": self.config.bot_id,
                    "secret": self.config.secret,
                    "device_id": self._device_id,
                },
            }
        )

        auth_payload = await self._wait_for_handshake(req_id)
        errcode = auth_payload.get("errcode", 0)
        if errcode not in (0, None):
            errmsg = auth_payload.get("errmsg", "authentication failed")
            raise RuntimeError(f"{errmsg} (errcode={errcode})")

    async def _wait_for_handshake(self, req_id: str) -> dict[str, Any]:
        if not self._ws:
            raise RuntimeError("WebSocket not initialized")

        deadline = asyncio.get_running_loop().time() + CONNECT_TIMEOUT_SECONDS
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError("Timed out waiting for WeCom subscribe acknowledgement")
            msg = await asyncio.wait_for(self._ws.receive(), timeout=remaining)
            if msg.type == aiohttp.WSMsgType.TEXT:
                payload = self._parse_json(msg.data)
                if not payload:
                    continue
                if payload.get("cmd") == APP_CMD_PING:
                    continue
                if self._payload_req_id(payload) == req_id:
                    return payload
                logger.debug("Ignoring pre-auth payload: %s", payload.get("cmd"))
            elif msg.type in (
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.ERROR,
            ):
                raise RuntimeError("WeCom websocket closed during authentication")

    async def _listen_loop(self) -> None:
        backoff_idx = 0
        while self._running:
            try:
                await self._read_events()
                backoff_idx = 0
            except asyncio.CancelledError:
                return
            except Exception as exc:
                if not self._running:
                    return
                self._connected = False
                self.last_error = str(exc)
                await self._emit("error", f"WebSocket error: {exc}")
                self._fail_pending_responses(RuntimeError("WeCom connection interrupted"))

                delay = RECONNECT_BACKOFF[min(backoff_idx, len(RECONNECT_BACKOFF) - 1)]
                backoff_idx += 1
                await self._emit("reconnecting", f"Reconnecting in {delay}s")
                await asyncio.sleep(delay)
                try:
                    await self._open_connection()
                    self._connected = True
                    backoff_idx = 0
                    await self._emit("connected", "Reconnected")
                except Exception as reconnect_exc:
                    self.last_error = str(reconnect_exc)
                    await self._emit("error", f"Reconnect failed: {reconnect_exc}")

    async def _read_events(self) -> None:
        if not self._ws:
            raise RuntimeError("WebSocket not connected")

        while self._running and self._ws and not self._ws.closed:
            msg = await self._ws.receive()
            if msg.type == aiohttp.WSMsgType.TEXT:
                payload = self._parse_json(msg.data)
                if payload:
                    await self._dispatch_payload(payload)
            elif msg.type in (
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.ERROR,
            ):
                raise RuntimeError("WeCom websocket closed")

    async def _heartbeat_loop(self) -> None:
        try:
            while self._running:
                await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
                if not self._ws or self._ws.closed:
                    continue
                try:
                    await self._send_json(
                        {
                            "cmd": APP_CMD_PING,
                            "headers": {"req_id": self._new_req_id("ping")},
                            "body": {},
                        }
                    )
                except Exception as exc:
                    logger.debug("Heartbeat send failed: %s", exc)
        except asyncio.CancelledError:
            pass

    async def _dispatch_payload(self, payload: dict[str, Any]) -> None:
        req_id = self._payload_req_id(payload)
        cmd = str(payload.get("cmd") or "")

        if req_id and req_id in self._pending_responses and cmd not in NON_RESPONSE_COMMANDS:
            future = self._pending_responses.get(req_id)
            if future and not future.done():
                future.set_result(payload)
            return

        if cmd in CALLBACK_COMMANDS:
            await self._on_message(payload)
            return
        if cmd in {APP_CMD_PING, APP_CMD_EVENT_CALLBACK}:
            return

        logger.debug("Ignoring websocket payload: %s", cmd or payload)

    def _fail_pending_responses(self, exc: Exception) -> None:
        for req_id, future in list(self._pending_responses.items()):
            if not future.done():
                future.set_exception(exc)
            self._pending_responses.pop(req_id, None)

    async def _send_json(self, payload: dict[str, Any]) -> None:
        if not self._ws or self._ws.closed:
            raise RuntimeError("WeCom websocket is not connected")
        await self._ws.send_json(payload)

    async def _send_request(
        self,
        cmd: str,
        body: dict[str, Any],
        timeout: float = REQUEST_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        if not self._ws or self._ws.closed:
            raise RuntimeError("WeCom websocket is not connected")

        req_id = self._new_req_id(cmd)
        future = asyncio.get_running_loop().create_future()
        self._pending_responses[req_id] = future
        try:
            await self._send_json({"cmd": cmd, "headers": {"req_id": req_id}, "body": body})
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending_responses.pop(req_id, None)

    async def _send_reply_request(
        self,
        reply_req_id: str,
        body: dict[str, Any],
        cmd: str = APP_CMD_RESPONSE,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        if not self._ws or self._ws.closed:
            raise RuntimeError("WeCom websocket is not connected")
        normalized_req_id = str(reply_req_id or "").strip()
        if not normalized_req_id:
            raise ValueError("reply_req_id is required")

        future = asyncio.get_running_loop().create_future()
        self._pending_responses[normalized_req_id] = future
        try:
            await self._send_json(
                {"cmd": cmd, "headers": {"req_id": normalized_req_id}, "body": body}
            )
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending_responses.pop(normalized_req_id, None)

    @staticmethod
    def _new_req_id(prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex}"

    @staticmethod
    def _payload_req_id(payload: dict[str, Any]) -> str:
        headers = payload.get("headers")
        if isinstance(headers, dict):
            return str(headers.get("req_id") or "")
        return ""

    @staticmethod
    def _parse_json(raw: Any) -> Optional[dict[str, Any]]:
        try:
            payload = json.loads(raw)
        except Exception:
            logger.debug("Failed to parse WeCom payload: %r", raw)
            return None
        return payload if isinstance(payload, dict) else None

    async def _on_message(self, payload: dict[str, Any]) -> None:
        body = payload.get("body")
        if not isinstance(body, dict):
            return

        msg_id = str(body.get("msgid") or self._payload_req_id(payload) or uuid.uuid4().hex)
        if self._dedup.is_duplicate(msg_id):
            logger.debug("Duplicate WeCom message ignored: %s", msg_id)
            return
        self._remember_reply_req_id(msg_id, self._payload_req_id(payload))

        sender = body.get("from") if isinstance(body.get("from"), dict) else {}
        sender_id = str(sender.get("userid") or "").strip()
        chat_id = str(body.get("chatid") or sender_id).strip()
        if not chat_id:
            logger.debug("Missing WeCom chat id, skipping message")
            return

        is_group = str(body.get("chattype") or "").lower() == "group"
        chat_type = "group" if is_group else "dm"
        decision = self.access.admit(
            chat_type=chat_type,
            chat_id=chat_id,
            sender_id=sender_id,
        )
        if not decision.allowed:
            await self._emit(
                "blocked",
                f"Blocked inbound {chat_type} message from {sender_id or 'unknown'}",
                {"reason": decision.reason, "chat_id": chat_id, "sender_id": sender_id},
            )
            return

        self._remember_chat_req_id(chat_id, self._payload_req_id(payload))

        text, reply_text = self.extract_text(body)
        if is_group and text:
            text = re.sub(r"^@\S+\s*", "", text).strip()

        media_paths, media_types = await self.extract_media(body)
        message_type = self.derive_message_type(body, text, media_types)
        has_reply_context = bool(reply_text and (text or media_paths))
        if not text and reply_text and not media_paths:
            text = reply_text
        if not text and not media_paths:
            logger.debug("Empty WeCom message skipped")
            return

        source = ChatSource(
            chat_id=chat_id,
            chat_type=chat_type,
            user_id=sender_id or None,
            user_name=sender_id or None,
            chat_name=chat_id,
        )
        event = WeComMessage(
            text=text,
            message_type=message_type,
            source=source,
            raw_payload=payload,
            message_id=msg_id,
            media_paths=media_paths,
            media_types=media_types,
            reply_to_message_id=f"quote:{msg_id}" if has_reply_context else None,
            reply_to_text=reply_text if has_reply_context else None,
            timestamp=datetime.now(tz=timezone.utc),
        )

        if message_type == MessageType.TEXT and self.config.text_batch_delay_seconds > 0:
            self._enqueue_text_event(event)
        else:
            await self._dispatch_message(event)

    def _text_batch_key(self, event: WeComMessage) -> str:
        return event.source.session_key(
            group_sessions_per_user=self.config.group_sessions_per_user
        )

    def _enqueue_text_event(self, event: WeComMessage) -> None:
        key = self._text_batch_key(event)
        existing = self._pending_text_batches.get(key)
        chunk_len = len(event.text or "")
        if existing is None:
            setattr(event, "_last_chunk_len", chunk_len)
            self._pending_text_batches[key] = event
        else:
            if event.text:
                existing.text = f"{existing.text}\n{event.text}" if existing.text else event.text
            setattr(existing, "_last_chunk_len", chunk_len)
            if event.media_paths:
                existing.media_paths.extend(event.media_paths)
                existing.media_types.extend(event.media_types)

        prior_task = self._pending_text_batch_tasks.get(key)
        if prior_task and not prior_task.done():
            prior_task.cancel()
        self._pending_text_batch_tasks[key] = asyncio.create_task(self._flush_text_batch(key))

    async def _flush_text_batch(self, key: str) -> None:
        current_task = asyncio.current_task()
        try:
            pending = self._pending_text_batches.get(key)
            last_len = getattr(pending, "_last_chunk_len", 0) if pending else 0
            delay = (
                self.config.text_batch_split_delay_seconds
                if last_len >= SPLIT_THRESHOLD
                else self.config.text_batch_delay_seconds
            )
            await asyncio.sleep(delay)
            event = self._pending_text_batches.pop(key, None)
            if event:
                await self._dispatch_message(event)
        finally:
            if self._pending_text_batch_tasks.get(key) is current_task:
                self._pending_text_batch_tasks.pop(key, None)

    async def _dispatch_message(self, event: WeComMessage) -> None:
        await self._emit("message", "Inbound message", event)
        if not self.message_handler:
            return
        result = self.message_handler(event)
        if inspect.isawaitable(result):
            await result

    @staticmethod
    def extract_text(body: dict[str, Any]) -> tuple[str, Optional[str]]:
        text_parts: list[str] = []
        reply_text: Optional[str] = None
        msgtype = str(body.get("msgtype") or "").lower()

        if msgtype == "mixed":
            mixed = body.get("mixed") if isinstance(body.get("mixed"), dict) else {}
            items = mixed.get("msg_item") if isinstance(mixed.get("msg_item"), list) else []
            for item in items:
                if not isinstance(item, dict):
                    continue
                if str(item.get("msgtype") or "").lower() == "text":
                    text_block = item.get("text") if isinstance(item.get("text"), dict) else {}
                    content = str(text_block.get("content") or "").strip()
                    if content:
                        text_parts.append(content)
        else:
            text_block = body.get("text") if isinstance(body.get("text"), dict) else {}
            content = str(text_block.get("content") or "").strip()
            if content:
                text_parts.append(content)

            if msgtype == "voice":
                voice_block = body.get("voice") if isinstance(body.get("voice"), dict) else {}
                voice_text = str(voice_block.get("content") or "").strip()
                if voice_text:
                    text_parts.append(voice_text)

            if msgtype == "appmsg":
                appmsg = body.get("appmsg") if isinstance(body.get("appmsg"), dict) else {}
                title = str(appmsg.get("title") or "").strip()
                if title:
                    text_parts.append(title)

        quote = body.get("quote") if isinstance(body.get("quote"), dict) else {}
        quote_type = str(quote.get("msgtype") or "").lower()
        if quote_type == "text":
            quote_text = quote.get("text") if isinstance(quote.get("text"), dict) else {}
            reply_text = str(quote_text.get("content") or "").strip() or None
        elif quote_type == "voice":
            quote_voice = quote.get("voice") if isinstance(quote.get("voice"), dict) else {}
            reply_text = str(quote_voice.get("content") or "").strip() or None

        return "\n".join(part for part in text_parts if part).strip(), reply_text

    async def extract_media(self, body: dict[str, Any]) -> tuple[list[str], list[str]]:
        media_paths: list[str] = []
        media_types: list[str] = []
        refs: list[tuple[str, dict[str, Any]]] = []
        msgtype = str(body.get("msgtype") or "").lower()

        if msgtype == "mixed":
            mixed = body.get("mixed") if isinstance(body.get("mixed"), dict) else {}
            items = mixed.get("msg_item") if isinstance(mixed.get("msg_item"), list) else []
            for item in items:
                if not isinstance(item, dict):
                    continue
                item_type = str(item.get("msgtype") or "").lower()
                if item_type == "image" and isinstance(item.get("image"), dict):
                    refs.append(("image", item["image"]))
        else:
            if isinstance(body.get("image"), dict):
                refs.append(("image", body["image"]))
            if msgtype == "file" and isinstance(body.get("file"), dict):
                refs.append(("file", body["file"]))
            if msgtype == "appmsg" and isinstance(body.get("appmsg"), dict):
                appmsg = body["appmsg"]
                if isinstance(appmsg.get("file"), dict):
                    refs.append(("file", appmsg["file"]))
                elif isinstance(appmsg.get("image"), dict):
                    refs.append(("image", appmsg["image"]))

        quote = body.get("quote") if isinstance(body.get("quote"), dict) else {}
        quote_type = str(quote.get("msgtype") or "").lower()
        if quote_type == "image" and isinstance(quote.get("image"), dict):
            refs.append(("image", quote["image"]))
        elif quote_type == "file" and isinstance(quote.get("file"), dict):
            refs.append(("file", quote["file"]))

        for kind, ref in refs:
            try:
                cached = await self.media.cache_media(kind, ref)
            except Exception as exc:
                logger.debug("Failed to cache WeCom %s media: %s", kind, exc)
                cached = None
            if cached:
                media_paths.append(cached.path)
                media_types.append(cached.content_type)
        return media_paths, media_types

    @staticmethod
    def derive_message_type(
        body: dict[str, Any], text: str, media_types: list[str]
    ) -> MessageType:
        if any(mtype.startswith(("application/", "text/")) for mtype in media_types):
            return MessageType.DOCUMENT
        if any(mtype.startswith("image/") for mtype in media_types):
            return MessageType.TEXT if text else MessageType.PHOTO
        if str(body.get("msgtype") or "").lower() == "voice":
            return MessageType.VOICE
        return MessageType.TEXT

    def _remember_reply_req_id(self, message_id: str, req_id: str) -> None:
        normalized_message_id = str(message_id or "").strip()
        normalized_req_id = str(req_id or "").strip()
        if not normalized_message_id or not normalized_req_id:
            return
        self._reply_req_ids[normalized_message_id] = normalized_req_id
        while len(self._reply_req_ids) > DEDUP_MAX_SIZE:
            self._reply_req_ids.pop(next(iter(self._reply_req_ids)))

    def _remember_chat_req_id(self, chat_id: str, req_id: str) -> None:
        normalized_chat_id = str(chat_id or "").strip()
        normalized_req_id = str(req_id or "").strip()
        if not normalized_chat_id or not normalized_req_id:
            return
        self._last_chat_req_ids[normalized_chat_id] = normalized_req_id
        while len(self._last_chat_req_ids) > DEDUP_MAX_SIZE:
            self._last_chat_req_ids.pop(next(iter(self._last_chat_req_ids)))

    def _reply_req_id_for_message(self, reply_to: Optional[str]) -> Optional[str]:
        normalized = str(reply_to or "").strip()
        if not normalized or normalized.startswith("quote:"):
            return None
        return self._reply_req_ids.get(normalized)

    @staticmethod
    def response_error(response: dict[str, Any]) -> Optional[str]:
        errcode = response.get("errcode", 0)
        if errcode in (0, None):
            return None
        errmsg = str(response.get("errmsg") or "unknown error")
        return f"WeCom errcode {errcode}: {errmsg}"

    @classmethod
    def raise_for_wecom_error(cls, response: dict[str, Any], operation: str) -> None:
        error = cls.response_error(response)
        if error:
            raise RuntimeError(f"{operation} failed: {error}")

    async def upload_media_bytes(
        self, data: bytes, media_type: str, filename: str
    ) -> dict[str, Any]:
        if not data:
            raise ValueError("Cannot upload empty media")

        total_size = len(data)
        total_chunks = (total_size + UPLOAD_CHUNK_SIZE - 1) // UPLOAD_CHUNK_SIZE
        if total_chunks > MAX_UPLOAD_CHUNKS:
            raise ValueError(
                f"File too large: {total_chunks} chunks exceeds maximum of {MAX_UPLOAD_CHUNKS}"
            )

        init_response = await self._send_request(
            APP_CMD_UPLOAD_MEDIA_INIT,
            {
                "type": media_type,
                "filename": filename,
                "total_size": total_size,
                "total_chunks": total_chunks,
                "md5": hashlib.md5(data).hexdigest(),
            },
        )
        self.raise_for_wecom_error(init_response, "media upload init")
        init_body = init_response.get("body") if isinstance(init_response.get("body"), dict) else {}
        upload_id = str(init_body.get("upload_id") or "").strip()
        if not upload_id:
            raise RuntimeError(f"media upload init failed: missing upload_id in {init_response}")

        for chunk_index, start in enumerate(range(0, total_size, UPLOAD_CHUNK_SIZE)):
            chunk = data[start : start + UPLOAD_CHUNK_SIZE]
            chunk_response = await self._send_request(
                APP_CMD_UPLOAD_MEDIA_CHUNK,
                {
                    "upload_id": upload_id,
                    "chunk_index": chunk_index,
                    "base64_data": base64.b64encode(chunk).decode("ascii"),
                },
            )
            self.raise_for_wecom_error(chunk_response, f"media upload chunk {chunk_index}")

        finish_response = await self._send_request(
            APP_CMD_UPLOAD_MEDIA_FINISH,
            {"upload_id": upload_id},
        )
        self.raise_for_wecom_error(finish_response, "media upload finish")
        finish_body = finish_response.get("body") if isinstance(finish_response.get("body"), dict) else {}
        media_id = str(finish_body.get("media_id") or "").strip()
        if not media_id:
            raise RuntimeError(f"media upload finish failed: missing media_id in {finish_response}")
        return {
            "type": str(finish_body.get("type") or media_type),
            "media_id": media_id,
            "created_at": finish_body.get("created_at"),
        }

    async def _send_media_message(
        self, chat_id: str, media_type: str, media_id: str
    ) -> dict[str, Any]:
        response = await self._send_request(
            APP_CMD_SEND,
            {
                "chatid": chat_id,
                "msgtype": media_type,
                media_type: {"media_id": media_id},
            },
        )
        self.raise_for_wecom_error(response, "send media message")
        return response

    async def _send_reply_markdown(self, reply_req_id: str, content: str) -> dict[str, Any]:
        response = await self._send_reply_request(
            reply_req_id,
            {
                "msgtype": "markdown",
                "markdown": {"content": content[: self.MAX_MESSAGE_LENGTH]},
            },
        )
        self.raise_for_wecom_error(response, "send reply markdown")
        return response

    async def _send_reply_media_message(
        self, reply_req_id: str, media_type: str, media_id: str
    ) -> dict[str, Any]:
        response = await self._send_reply_request(
            reply_req_id,
            {
                "msgtype": media_type,
                media_type: {"media_id": media_id},
            },
        )
        self.raise_for_wecom_error(response, "send reply media message")
        return response

    async def send_followup_markdown(
        self, chat_id: str, content: str, reply_to: Optional[str] = None
    ) -> Optional[SendResult]:
        if not content:
            return None
        return await self.send(chat_id=chat_id, content=content, reply_to=reply_to)

    async def send_media_source(
        self,
        chat_id: str,
        media_source: str,
        *,
        caption: Optional[str] = None,
        file_name: Optional[str] = None,
        reply_to: Optional[str] = None,
    ) -> SendResult:
        if not chat_id:
            return SendResult(success=False, error="chat_id is required")

        try:
            prepared = await self.media.prepare_outbound_media(
                media_source, file_name=file_name
            )
        except FileNotFoundError as exc:
            return SendResult(success=False, error=str(exc))
        except Exception as exc:
            logger.error("Failed to prepare outbound media %s: %s", media_source, exc)
            return SendResult(success=False, error=str(exc))

        if prepared.rejected:
            await self.send_followup_markdown(chat_id, prepared.reject_reason or "", reply_to=reply_to)
            return SendResult(success=False, error=prepared.reject_reason)

        reply_req_id = self._reply_req_id_for_message(reply_to)
        if not reply_req_id and chat_id in self._last_chat_req_ids:
            reply_req_id = self._last_chat_req_ids[chat_id]

        try:
            upload_result = await self.upload_media_bytes(
                prepared.data,
                prepared.final_type,
                prepared.file_name,
            )
            if reply_req_id:
                media_response = await self._send_reply_media_message(
                    reply_req_id,
                    prepared.final_type,
                    upload_result["media_id"],
                )
            else:
                media_response = await self._send_media_message(
                    chat_id,
                    prepared.final_type,
                    upload_result["media_id"],
                )
        except asyncio.TimeoutError:
            return SendResult(success=False, error="Timeout sending media to WeCom")
        except Exception as exc:
            logger.error("Failed to send media %s: %s", media_source, exc)
            return SendResult(success=False, error=str(exc))

        caption_result = None
        downgrade_result = None
        if caption:
            caption_result = await self.send_followup_markdown(
                chat_id, caption, reply_to=reply_to
            )
        if prepared.downgraded and prepared.downgrade_note:
            downgrade_result = await self.send_followup_markdown(
                chat_id, prepared.downgrade_note, reply_to=reply_to
            )

        return SendResult(
            success=True,
            message_id=self._payload_req_id(media_response) or uuid.uuid4().hex[:12],
            raw_response={
                "upload": upload_result,
                "media": media_response,
                "caption": caption_result.raw_response if caption_result else None,
                "caption_error": (
                    caption_result.error
                    if caption_result and not caption_result.success
                    else None
                ),
                "downgrade": downgrade_result.raw_response if downgrade_result else None,
                "downgrade_error": (
                    downgrade_result.error
                    if downgrade_result and not downgrade_result.success
                    else None
                ),
            },
        )

    async def send(
        self,
        chat_id: str,
        content: str,
        *,
        reply_to: Optional[str] = None,
    ) -> SendResult:
        if not chat_id:
            return SendResult(success=False, error="chat_id is required")
        try:
            reply_req_id = self._reply_req_id_for_message(reply_to)
            if not reply_req_id and chat_id in self._last_chat_req_ids:
                reply_req_id = self._last_chat_req_ids[chat_id]

            if reply_req_id:
                response = await self._send_reply_markdown(reply_req_id, content)
            else:
                response = await self._send_request(
                    APP_CMD_SEND,
                    {
                        "chatid": chat_id,
                        "msgtype": "markdown",
                        "markdown": {"content": content[: self.MAX_MESSAGE_LENGTH]},
                    },
                )
        except asyncio.TimeoutError:
            return SendResult(success=False, error="Timeout sending message to WeCom")
        except Exception as exc:
            logger.error("WeCom send failed: %s", exc)
            return SendResult(success=False, error=str(exc))

        error = self.response_error(response)
        if error:
            return SendResult(success=False, error=error, raw_response=response)
        return SendResult(
            success=True,
            message_id=self._payload_req_id(response) or uuid.uuid4().hex[:12],
            raw_response=response,
        )

    async def send_image(
        self,
        chat_id: str,
        image_url: str,
        *,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
    ) -> SendResult:
        result = await self.send_media_source(
            chat_id,
            image_url,
            caption=caption,
            reply_to=reply_to,
        )
        if result.success or not self.media.looks_like_url(image_url):
            return result
        fallback_text = f"{caption}\n{image_url}" if caption else image_url
        return await self.send(chat_id, fallback_text, reply_to=reply_to)

    async def send_image_file(
        self,
        chat_id: str,
        image_path: str,
        *,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
    ) -> SendResult:
        return await self.send_media_source(
            chat_id,
            image_path,
            caption=caption,
            reply_to=reply_to,
        )

    async def send_document(
        self,
        chat_id: str,
        file_path: str,
        *,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
    ) -> SendResult:
        return await self.send_media_source(
            chat_id,
            file_path,
            caption=caption,
            reply_to=reply_to,
        )

    async def send_voice(
        self,
        chat_id: str,
        audio_path: str,
        *,
        reply_to: Optional[str] = None,
    ) -> SendResult:
        return await self.send_media_source(chat_id, audio_path, reply_to=reply_to)

    async def send_video(
        self,
        chat_id: str,
        video_path: str,
        *,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
    ) -> SendResult:
        return await self.send_media_source(
            chat_id,
            video_path,
            caption=caption,
            reply_to=reply_to,
        )

    async def send_typing(self, chat_id: str) -> None:
        del chat_id

    async def get_chat_info(self, chat_id: str) -> dict[str, Any]:
        return {"name": chat_id, "type": "wecom"}

    async def _emit(self, kind: str, message: str, data: Any = None) -> None:
        if not self.event_handler:
            return
        event = BridgeEvent(kind=kind, message=message, data=data)
        result = self.event_handler(event)
        if inspect.isawaitable(result):
            await result


def check_wecom_requirements() -> bool:
    return True


def classify_media_source(filename: str) -> str:
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return detect_wecom_media_type(content_type)
