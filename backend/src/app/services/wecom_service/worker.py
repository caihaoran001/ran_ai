from __future__ import annotations

import asyncio
import logging

from wecom_agent_bridge import BridgeEvent, WeComBotBridge, WeComConfig, WeComMessage

from app.core.config import Settings, get_settings
from app.services.channels import set_current_channel
from app.services.channels.wecom import WeComChannel, from_wecom_message
from app.services.ranren_ai import RanrenAIEngine


logger = logging.getLogger(__name__)


class WeComBotWorker:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.bridge: WeComBotBridge | None = None
        self.engine: RanrenAIEngine | None = None

    async def start(self) -> None:
        config = WeComConfig.from_env()
        self.bridge = WeComBotBridge(
            config,
            message_handler=self._handle_message,
            event_handler=self._handle_event,
        )
        connected = await self.bridge.connect()
        if not connected:
            raise RuntimeError(self.bridge.last_error or "failed to connect to WeCom")
        channel = WeComChannel(self.bridge)
        set_current_channel(channel)
        self.engine = RanrenAIEngine(channel, self.settings)
        logger.info("WeCom bot worker started")

    async def stop(self) -> None:
        set_current_channel(None)
        self.engine = None
        if self.bridge:
            await self.bridge.disconnect()
            self.bridge = None

    async def _handle_event(self, event: BridgeEvent) -> None:
        if event.kind != "message":
            logger.info("WeCom event: %s - %s", event.kind, event.message)

    async def _handle_message(self, message: WeComMessage) -> None:
        if not self.engine:
            logger.warning("RanrenAI engine is not initialized")
            return
        await self.engine.handle_message(from_wecom_message(message))


async def run_forever() -> None:
    worker = WeComBotWorker()
    await worker.start()
    try:
        await asyncio.Event().wait()
    finally:
        await worker.stop()
