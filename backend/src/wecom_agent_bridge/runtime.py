from __future__ import annotations

import asyncio
import queue
import threading
from dataclasses import dataclass
from typing import Optional

from .client import WeComBotBridge
from .config import WeComConfig
from .models import BridgeEvent, SendResult, WeComMessage


@dataclass
class RuntimeItem:
    kind: str
    payload: object


class BridgeThreadRuntime:
    """Run a WeComBotBridge in a background asyncio thread."""

    def __init__(self, config: WeComConfig):
        self.config = config
        self.items: "queue.Queue[RuntimeItem]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._bridge: Optional[WeComBotBridge] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._started = threading.Event()
        self._lock = threading.Lock()
        self._connected = False

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def connected(self) -> bool:
        return bool(self._connected and self._bridge and self._bridge.connected)

    def start(self, timeout: float = 20.0) -> bool:
        with self._lock:
            if self.running:
                return True
            self._started.clear()
            self._thread = threading.Thread(target=self._thread_main, daemon=True)
            self._thread.start()
        self._started.wait(timeout=timeout)
        return self.connected

    def stop(self, timeout: float = 10.0) -> None:
        loop = self._loop
        stop_event = self._stop_event
        if loop and stop_event:
            loop.call_soon_threadsafe(stop_event.set)
        thread = self._thread
        if thread:
            thread.join(timeout=timeout)

    def send_markdown(
        self,
        chat_id: str,
        content: str,
        *,
        reply_to: str | None = None,
        timeout: float = 20.0,
    ) -> SendResult:
        if not self._loop or not self._bridge:
            return SendResult(success=False, error="Bridge is not running")
        future = asyncio.run_coroutine_threadsafe(
            self._bridge.send(chat_id=chat_id, content=content, reply_to=reply_to),
            self._loop,
        )
        try:
            return future.result(timeout=timeout)
        except Exception as exc:
            return SendResult(success=False, error=str(exc))

    def drain_items(self, limit: int = 100) -> list[RuntimeItem]:
        drained: list[RuntimeItem] = []
        for _ in range(limit):
            try:
                drained.append(self.items.get_nowait())
            except queue.Empty:
                break
        return drained

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._main())
        finally:
            self._connected = False
            self._bridge = None
            self._stop_event = None
            self._loop = None
            loop.close()

    async def _main(self) -> None:
        self._stop_event = asyncio.Event()

        async def on_message(message: WeComMessage) -> None:
            self.items.put(RuntimeItem("message", message))

        async def on_event(event: BridgeEvent) -> None:
            self.items.put(RuntimeItem("event", event))

        self._bridge = WeComBotBridge(
            self.config,
            message_handler=on_message,
            event_handler=on_event,
        )
        self._connected = await self._bridge.connect()
        self._started.set()
        try:
            if self._connected:
                await self._stop_event.wait()
        finally:
            await self._bridge.disconnect()
            self._connected = False
