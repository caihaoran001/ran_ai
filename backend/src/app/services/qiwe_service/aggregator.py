from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
import logging
import time
from typing import Protocol

from app.services.channels.base import ChannelMessage, MessageChannel
from app.services.qiwe_service.inbox import (
    mark_inbound_events_processed,
    persist_inbound_event,
)
from app.services.ranren_ai import RanrenAIEngine


logger = logging.getLogger("uvicorn.error")


class MessageEngine(Protocol):
    async def handle_message(self, message: ChannelMessage) -> None:
        ...


@dataclass
class _PendingBatch:
    messages: list[ChannelMessage] = field(default_factory=list)
    event_ids: list[int] = field(default_factory=list)
    timer_task: asyncio.Task | None = None
    processing_task: asyncio.Task | None = None


class QiWeMessageAggregator:
    """Aggregate short QiWe messages into conversation turns.

    A single group/user pair is processed as one lane: while one merged turn is
    being handled by the AI engine, newly arriving messages wait for the next
    fixed window. This prevents two AI replies for the same user from racing
    each other and appearing out of order in the group.
    """

    def __init__(
        self,
        channel: MessageChannel,
        *,
        window_seconds: float = 8.0,
        engine: MessageEngine | None = None,
        dedupe_ttl_seconds: float = 600.0,
        dedupe_max_size: int = 5000,
    ):
        self.channel = channel
        self.window_seconds = window_seconds
        self.dedupe_ttl_seconds = dedupe_ttl_seconds
        self.dedupe_max_size = dedupe_max_size
        self.engine = engine or RanrenAIEngine(channel, has_pending_newer_message=self.has_pending_messages_for)
        self._batches: dict[tuple[str, str], _PendingBatch] = {}
        self._seen_message_ids: OrderedDict[str, float] = OrderedDict()
        self._next_seen_prune_at = 0.0
        self._lock = asyncio.Lock()

    async def enqueue(self, message: ChannelMessage) -> None:
        key = _conversation_key(message)
        async with self._lock:
            if self._is_duplicate_locked(message):
                logger.info(
                    "QiWe duplicate ignored chat_id=%s user_id=%s message_id=%s",
                    message.chat_id,
                    message.user_id,
                    message.message_id,
                )
                return

            batch = self._batches.setdefault(key, _PendingBatch())
            batch.messages.append(message)
            # durable inbox：进聚合窗口前先落库，堵住「8s 窗口内重启丢消息」的黑洞。
            # 持久化失败只记日志、不阻断实时处理（persist_inbound_event 内部已兜底）。
            event_id = await persist_inbound_event(message)
            if event_id is not None:
                batch.event_ids.append(event_id)

            if batch.processing_task and not batch.processing_task.done():
                logger.info(
                    "QiWe message queued while previous reply is running chat_id=%s user_id=%s message_id=%s pending_size=%s",
                    message.chat_id,
                    message.user_id,
                    message.message_id,
                    len(batch.messages),
                )
                return

            if not batch.timer_task or batch.timer_task.done():
                batch.timer_task = asyncio.create_task(self._flush_later(key))
                logger.info(
                    "QiWe fixed window started chat_id=%s user_id=%s message_id=%s window_seconds=%.2f",
                    message.chat_id,
                    message.user_id,
                    message.message_id,
                    self.window_seconds,
                )
            else:
                logger.info(
                    "QiWe fixed window appended chat_id=%s user_id=%s message_id=%s batch_size=%s",
                    message.chat_id,
                    message.user_id,
                    message.message_id,
                    len(batch.messages),
                )

    async def dispatch_now(self, message: ChannelMessage) -> None:
        started_at = time.monotonic()
        event_id: int | None = None
        async with self._lock:
            if self._is_duplicate_locked(message):
                logger.info(
                    "QiWe immediate duplicate ignored chat_id=%s user_id=%s message_id=%s",
                    message.chat_id,
                    message.user_id,
                    message.message_id,
                )
                return
            key = _conversation_key(message)
            batch = self._batches.get(key)
            if message.message_type == "text" and batch and (
                batch.messages or (batch.processing_task and not batch.processing_task.done())
            ):
                batch.messages.append(message)
                event_id = await persist_inbound_event(message)
                if event_id is not None:
                    batch.event_ids.append(event_id)
                logger.info(
                    "QiWe immediate text queued behind pending batch chat_id=%s user_id=%s message_id=%s pending_size=%s",
                    message.chat_id,
                    message.user_id,
                    message.message_id,
                    len(batch.messages),
                )
                return
            event_id = await persist_inbound_event(message)
        logger.info(
            "QiWe immediate dispatch start chat_id=%s user_id=%s message_id=%s",
            message.chat_id,
            message.user_id,
            message.message_id,
        )
        try:
            await self.engine.handle_message(message)
        except Exception as exc:
            logger.exception(
                "QiWe immediate dispatch failed chat_id=%s user_id=%s message_id=%s error_type=%s",
                message.chat_id,
                message.user_id,
                message.message_id,
                type(exc).__name__,
            )
            return
        await mark_inbound_events_processed([event_id] if event_id is not None else [])
        logger.info(
            "QiWe immediate dispatch done chat_id=%s user_id=%s message_id=%s elapsed_ms=%.1f",
            message.chat_id,
            message.user_id,
            message.message_id,
            (time.monotonic() - started_at) * 1000,
        )

    async def has_pending_messages_for(self, message: ChannelMessage) -> bool:
        key = _conversation_key(message)
        async with self._lock:
            batch = self._batches.get(key)
            return bool(batch and batch.messages)

    async def stop(self) -> None:
        async with self._lock:
            tasks = [
                task
                for batch in self._batches.values()
                for task in (batch.timer_task, batch.processing_task)
                if task
            ]
            self._batches.clear()
        for task in tasks:
            if task and not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _flush_later(self, key: tuple[str, str]) -> None:
        try:
            await asyncio.sleep(self.window_seconds)
            await self.flush(key)
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("QiWe message flush failed for key=%s", key)

    async def flush(self, key: tuple[str, str]) -> None:
        async with self._lock:
            batch = self._batches.get(key)
            if not batch or not batch.messages:
                return
            if batch.processing_task and not batch.processing_task.done():
                if not batch.timer_task or batch.timer_task.done():
                    batch.timer_task = asyncio.create_task(self._flush_later(key))
                logger.info("QiWe fixed window delayed because previous reply is still running key=%s", key)
                return

            messages = batch.messages
            event_ids = batch.event_ids
            batch.messages = []
            batch.event_ids = []
            batch.timer_task = None
            task = asyncio.create_task(self._process_batch(key, messages, event_ids))
            batch.processing_task = task
        await task

    async def _process_batch(
        self, key: tuple[str, str], messages: list[ChannelMessage], event_ids: list[int] | None = None
    ) -> None:
        started_at = time.monotonic()
        first = messages[0]
        last = messages[-1]
        logger.info(
            "QiWe fixed window flush start chat_id=%s user_id=%s count=%s first_message_id=%s last_message_id=%s",
            first.chat_id,
            first.user_id,
            len(messages),
            first.message_id,
            last.message_id,
        )
        processed = False
        try:
            await self.engine.handle_message(_merge_messages(messages))
            processed = True
        except Exception as exc:
            logger.exception(
                "QiWe fixed window flush failed chat_id=%s user_id=%s count=%s first_message_id=%s last_message_id=%s error_type=%s",
                first.chat_id,
                first.user_id,
                len(messages),
                first.message_id,
                last.message_id,
                type(exc).__name__,
            )
            return
        finally:
            # handle_message 已被完整调用（引擎在最开头就把入站落成 Message），才标记这批 inbox
            # 事件为已处理；若引擎抛异常，保留 pending，开机 replay 会按 DB 事实保守判断是否重放。
            if processed:
                await mark_inbound_events_processed(event_ids or [])
            async with self._lock:
                batch = self._batches.get(key)
                if batch and batch.processing_task is asyncio.current_task():
                    batch.processing_task = None
                    if batch.messages:
                        batch.timer_task = asyncio.create_task(self._flush_later(key))
                    else:
                        self._batches.pop(key, None)

        logger.info(
            "QiWe fixed window flush done chat_id=%s user_id=%s count=%s elapsed_ms=%.1f",
            first.chat_id,
            first.user_id,
            len(messages),
            (time.monotonic() - started_at) * 1000,
        )

    def _is_duplicate_locked(self, message: ChannelMessage) -> bool:
        if not message.message_id:
            return False
        now = time.monotonic()
        seen_at = self._seen_message_ids.get(message.message_id)
        if seen_at is not None:
            if now - seen_at <= self.dedupe_ttl_seconds:
                return True
            self._seen_message_ids.pop(message.message_id, None)
        if now >= self._next_seen_prune_at or len(self._seen_message_ids) > self.dedupe_max_size:
            self._prune_seen_locked(now)
        self._seen_message_ids[message.message_id] = now
        return False

    def _prune_seen_locked(self, now: float) -> None:
        expired = [
            message_id
            for message_id, seen_at in self._seen_message_ids.items()
            if now - seen_at > self.dedupe_ttl_seconds
        ]
        for message_id in expired:
            self._seen_message_ids.pop(message_id, None)
        while len(self._seen_message_ids) > self.dedupe_max_size:
            self._seen_message_ids.popitem(last=False)
        self._next_seen_prune_at = now + min(self.dedupe_ttl_seconds, 60.0)


def _conversation_key(message: ChannelMessage) -> tuple[str, str]:
    return (message.chat_id, message.user_id or "")


def _merge_messages(messages: list[ChannelMessage]) -> ChannelMessage:
    if len(messages) == 1:
        return messages[0]
    first = messages[0]
    last = messages[-1]
    text = "\n".join(item.text.strip() for item in messages if item.text.strip())
    timestamp: datetime = last.timestamp
    return ChannelMessage(
        channel=last.channel,
        chat_id=last.chat_id,
        chat_type=last.chat_type,
        text=text,
        message_id=last.message_id,
        user_id=last.user_id or first.user_id,
        user_name=last.user_name or first.user_name,
        message_type="text",
        raw_payload=[item.raw_payload for item in messages],
        timestamp=timestamp,
    )
