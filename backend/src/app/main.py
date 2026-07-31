from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import logging

import uvicorn
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware

from app.api.admin_auth import add_admin_auth_middleware
from app.api.router import api_router
from app.core.config import configure_logging, get_settings
from app.services.channels import set_current_channel
from app.services.channels.qiwe import QiWeChannel
from app.services.agent.checkpoint import (
    get_agent_checkpointer,
    start_agent_checkpoint_runtime,
    stop_agent_checkpoint_runtime,
)
from app.services.agent.graph import (
    start_agent_graph_runtime,
    stop_agent_graph_runtime,
)
from app.services.qiwe_service.aggregator import QiWeMessageAggregator
from app.services.qiwe_service.inbox_dispatcher import QiWeInboxDispatcher
from app.services.ranren_ai.engine_graph import (
    start_engine_graph_runtime,
    stop_engine_graph_runtime,
)
from app.services.reminder_service import ReminderService


logger = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    # Checkpoint tables are created/upgraded by the controlled deployment step.
    # Runtime startup only opens and validates the async psycopg pool.
    await start_agent_checkpoint_runtime(settings)
    checkpointer = get_agent_checkpointer()
    start_agent_graph_runtime(checkpointer)
    # Scheduled reminders intentionally have no per-inbound checkpoint thread,
    # so keep their no-checkpointer topology as a second precompiled variant.
    start_agent_graph_runtime()
    start_engine_graph_runtime(checkpointer)
    reminder_service = ReminderService(settings)
    channel: QiWeChannel | None = None
    qiwe_aggregator: QiWeMessageAggregator | None = None
    qiwe_inbox_dispatcher: QiWeInboxDispatcher | None = None
    frozen_outbound_ids: list[int] = []
    # 运营在后台切换的账号落库 app_settings，优先于 env；重启后据此恢复。
    from app.db.session import SessionLocal
    from app.services.qiwe_account_settings import get_qiwe_account

    async with SessionLocal() as _session:
        qiwe_account = await get_qiwe_account(_session)
    qiwe_guid = qiwe_account.get("guid", "")
    qiwe_token = qiwe_account.get("token", "")
    qiwe_should_start = settings.qiwei_enabled or bool(qiwe_token and qiwe_guid)
    if qiwe_should_start:
        channel = QiWeChannel(settings)
        if qiwe_guid and qiwe_token:
            channel.apply_credentials(qiwe_guid, qiwe_token)
        set_current_channel(channel)
        qiwe_aggregator = QiWeMessageAggregator(
            channel,
            window_seconds=settings.qiwei_batch_window_seconds,
        )
        from app.services.ranren_ai.engine import (
            reconcile_group_introduction_state,
            reconcile_interrupted_sends,
        )

        startup_time = datetime.now(timezone.utc)
        # 在任何会发送消息的后台服务启动、以及 FastAPI 对外 ready 之前冻结恢复批次。
        # 后续历史对账只接收这份固定 id 清单，不可能扫到恢复后新产生的 SENDING。
        frozen_outbound_ids = await reconcile_interrupted_sends(startup_time)
        from app.services.qiwe_service.inbox import (
            replay_pending_spam_block_events,
        )
        from app.services.qiwe_service.outbound_reconciliation import (
            reconcile_frozen_unknown_sends,
        )

        # 风控通知优先于一切恢复发送；出站历史对账也在 reminder/HTTP ready
        # 之前完成，避免尚未确认渠道状态时产生新的自动发送。
        await replay_pending_spam_block_events(startup_time)
        if frozen_outbound_ids:
            try:
                await reconcile_frozen_unknown_sends(
                    channel,
                    frozen_outbound_ids,
                )
            except Exception:
                logger.exception(
                    "QiWe startup outbound reconciliation failed frozen_ids=%s",
                    frozen_outbound_ids,
                )
        await reconcile_group_introduction_state()
        qiwe_inbox_dispatcher = QiWeInboxDispatcher(qiwe_aggregator)
        await qiwe_inbox_dispatcher.start()
        # Ensure messages accepted by the stable ingress during the deploy have
        # become durable business Messages before reminders/old graph recovery.
        await qiwe_inbox_dispatcher.wait_until_handed_off(
            before=startup_time,
            timeout_seconds=30.0,
        )

    await reminder_service.start()
    app.state.reminder_service = reminder_service
    app.state.qiwe_aggregator = qiwe_aggregator
    app.state.qiwe_inbox_dispatcher = qiwe_inbox_dispatcher
    if qiwe_should_start and channel:
        from app.services.ranren_ai.engine import (
            recover_stuck_inbound_messages,
        )
        async def _recover_qiwe_messages() -> None:
            # Durable inbox handoff has already completed above. Resume older
            # graph runs only after those newer inbound Messages are visible,
            # so stale-reply guards can safely suppress obsolete output.
            await recover_stuck_inbound_messages(
                channel,
                startup_time,
                has_pending_newer_message=qiwe_aggregator.has_pending_messages_for,
            )

        asyncio.create_task(
            _recover_qiwe_messages(),
            name="qiwe-inbound-startup-recovery",
        )
    try:
        yield
    finally:
        await reminder_service.stop()
        if qiwe_inbox_dispatcher:
            await qiwe_inbox_dispatcher.stop()
        if qiwe_aggregator:
            await qiwe_aggregator.stop(
                drain_timeout_seconds=settings.qiwei_shutdown_drain_seconds,
            )
        if qiwe_should_start:
            set_current_channel(None)
        stop_engine_graph_runtime()
        stop_agent_graph_runtime()
        await stop_agent_checkpoint_runtime()
        # Gracefully dispose database connection pool to avoid shutdown hangs
        from app.db.session import engine
        await engine.dispose()



def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings)
    docs_enabled = settings.environment != "production"
    app = FastAPI(
        title=settings.app_name,
        lifespan=lifespan,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )
    # This process is no longer the callback worker. Keeping the route mounted
    # is a harmless fallback, but it only writes the durable inbox.
    app.state.qiwe_dispatch_inline = False
    cors_origins = _cors_origins(settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    add_admin_auth_middleware(app, settings)

    app.include_router(api_router, prefix=settings.api_prefix)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/")
    async def root() -> RedirectResponse:
        return RedirectResponse(url="/docs" if docs_enabled else "/health")

    return app


def _cors_origins(settings) -> list[str]:
    configured = [
        item.strip()
        for item in settings.cors_allowed_origins.split(",")
        if item.strip()
    ]
    if configured:
        return configured
    return [settings.frontend_public_base_url]


app = create_app()


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.environment == "development",
    )
