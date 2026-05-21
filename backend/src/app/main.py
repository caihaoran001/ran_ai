from __future__ import annotations

import uvicorn
import secrets
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import get_settings
from app.services.reminder_service import ReminderService
from app.services.wecom_service.worker import WeComBotWorker


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    reminder_service = ReminderService(settings)
    wecom_worker: WeComBotWorker | None = None
    if settings.wecom_worker_enabled:
        wecom_worker = WeComBotWorker(settings)
        await wecom_worker.start()
    await reminder_service.start()
    app.state.reminder_service = reminder_service
    app.state.wecom_worker = wecom_worker
    try:
        yield
    finally:
        await reminder_service.stop()
        if wecom_worker:
            await wecom_worker.stop()


def create_app() -> FastAPI:
    settings = get_settings()
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
    if _is_public_questionnaire_path(path, request.method):
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
