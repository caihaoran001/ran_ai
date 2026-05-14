from __future__ import annotations

import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
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
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_router, prefix=settings.api_prefix)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/")
    async def root() -> RedirectResponse:
        return RedirectResponse(url="/docs")

    return app


app = create_app()


def main() -> None:
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
