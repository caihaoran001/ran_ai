from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
import logging
import time
from typing import Protocol

from app.services.channels.base import ChannelMessage, MessageChannel
from app.services.ranren_ai import RanrenAIEngine


logger = logging.getLogger("uvicorn.error")


class MessageEngine(Protocol):
    async def handle_message(self, message: ChannelMessage) -> None:
        ...


@dataclass
class _PendingBatch:
    messages: list[ChannelMessage] = field(default_factory=list)
    task: asyncio.Task | None = None


class QiWeMessageAggregator:
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
        self.engine = engine or RanrenAIEngine(channel)
        self._batches: dict[tuple[str, str], _PendingBatch] = {}
        self._seen_message_ids: OrderedDict[str, float] = OrderedDict()
        self._next_seen_prune_at = 0.0
        self._lock = asyncio.Lock()

    async def enqueue(self, message: ChannelMessage) -> None:
        key = (message.chat_id, message.user_id or "")
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
            if not batch.task or batch.task.done():
                batch.task = asyncio.create_task(self._flush_later(key))
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
        async with self._lock:
            if self._is_duplicate_locked(message):
                logger.info(
                    "QiWe immediate duplicate ignored chat_id=%s user_id=%s message_id=%s",
                    message.chat_id,
                    message.user_id,
                    message.message_id,
                )
                return
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
        logger.info(
            "QiWe immediate dispatch done chat_id=%s user_id=%s message_id=%s elapsed_ms=%.1f",
            message.chat_id,
            message.user_id,
            message.message_id,
            (time.monotonic() - started_at) * 1000,
        )

    async def stop(self) -> None:
        async with self._lock:
            tasks = [batch.task for batch in self._batches.values() if batch.task]
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
        started_at = time.monotonic()
        async with self._lock:
            batch = self._batches.pop(key, None)
        if not batch or not batch.messages:
            return
        first = batch.messages[0]
        last = batch.messages[-1]
        logger.info(
            "QiWe fixed window flush start chat_id=%s user_id=%s count=%s first_message_id=%s last_message_id=%s",
            first.chat_id,
            first.user_id,
            len(batch.messages),
            first.message_id,
            last.message_id,
        )
        merged = _merge_messages(batch.messages)
        try:
            await self.engine.handle_message(merged)
        except Exception as exc:
            logger.exception(
                "QiWe fixed window flush failed chat_id=%s user_id=%s count=%s first_message_id=%s last_message_id=%s error_type=%s",
                first.chat_id,
                first.user_id,
                len(batch.messages),
                first.message_id,
                last.message_id,
                type(exc).__name__,
            )
            return
        logger.info(
            "QiWe fixed window flush done chat_id=%s user_id=%s count=%s elapsed_ms=%.1f",
            first.chat_id,
            first.user_id,
            len(batch.messages),
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
