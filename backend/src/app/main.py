from __future__ import annotations

import uvicorn
import secrets
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import configure_logging, get_settings
from app.services.channels import set_current_channel
from app.services.channels.qiwe import QiWeChannel
from app.services.agent.checkpoint import (
    start_agent_checkpoint_runtime,
    stop_agent_checkpoint_runtime,
)
from app.services.qiwe_service.aggregator import QiWeMessageAggregator
from app.services.reminder_service import ReminderService


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    # Checkpoint tables are created/upgraded by the controlled deployment step.
    # Runtime startup only opens and validates the async psycopg pool.
    await start_agent_checkpoint_runtime(settings)
    reminder_service = ReminderService(settings)
    qiwe_aggregator: QiWeMessageAggregator | None = None
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
    await reminder_service.start()
    app.state.reminder_service = reminder_service
    app.state.qiwe_aggregator = qiwe_aggregator
    if qiwe_should_start and channel:
        import asyncio
        from datetime import datetime, timezone
        from app.services.ranren_ai.engine import (
            reconcile_group_introduction_state,
            reconcile_interrupted_sends,
            recover_stuck_inbound_messages,
        )
        from app.services.qiwe_service.inbox import replay_pending_inbound_events
        startup_time = datetime.now(timezone.utc)
        # 先把重启前残留的「在途(SENDING)」出站记录对账为 FAILED（宁可少发、不自动重发），
        # 再补发那些「收到但从未回复」的在途入站消息，最后重放卡在聚合窗口、从未进引擎的消息。
        await reconcile_interrupted_sends(startup_time)
        await reconcile_group_introduction_state()
        asyncio.create_task(recover_stuck_inbound_messages(channel, startup_time))
        asyncio.create_task(replay_pending_inbound_events(channel, startup_time))
    try:
        yield
    finally:
        await reminder_service.stop()
        if qiwe_aggregator:
            await qiwe_aggregator.stop()
        if qiwe_should_start:
            set_current_channel(None)
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
    cors_origins = _cors_origins(settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def admin_api_key_auth(request: Request, call_next):
        if _requires_admin_auth(request):
            expected = settings.backend_admin_api_key.strip()
            provided = request.headers.get("x-admin-api-key", "").strip()
            if not expected or not secrets.compare_digest(provided, expected):
                return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return await call_next(request)

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


def _requires_admin_auth(request: Request) -> bool:
    path = request.url.path
    if request.method == "OPTIONS":
        return False
    if path == "/health":
        return False
    if path == "/api/channels/qiwe/callback":
        return False
    if _is_public_questionnaire_path(path, request.method):
        return False
    # 短链接解析是公开入口(用户点 /s/{code} 跳转用)，免 admin key
    if path.startswith("/api/short-links/") and request.method == "GET":
        return False
    if path.startswith("/api/"):
        return True
    if path in {"/docs", "/redoc", "/openapi.json"}:
        return True
    return False


def _is_public_questionnaire_path(path: str, method: str) -> bool:
    prefix = "/api/questionnaires/"
    if not path.startswith(prefix):
        return False
    rest = path.removeprefix(prefix).strip("/")
    if not rest:
        return False
    parts = rest.split("/")
    if len(parts) == 1 and method == "GET":
        return True
    # POST=用户首次提交，走 token 鉴权、免 admin key；
    # PATCH=运营编辑已提交问卷并重新生成，属管理操作，不在此豁免——需带 admin key 走鉴权
    if len(parts) == 2 and parts[1] == "submit" and method == "POST":
        return True
    return False


app = create_app()


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.environment == "development",
    )
