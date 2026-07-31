from __future__ import annotations

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from app.api.routes.qiwe import router as qiwe_router
from app.core.config import configure_logging, get_settings


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        yield
    finally:
        # The ingress owns only the async SQLAlchemy pool. It intentionally
        # starts no channel client, scheduler, LangGraph, or aggregation timer.
        from app.db.session import engine

        await engine.dispose()


def create_ingress_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings)
    app = FastAPI(
        title=f"{settings.app_name} Ingress",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.qiwe_dispatch_inline = False
    app.include_router(qiwe_router, prefix=settings.api_prefix)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_ingress_app()


def main() -> None:
    uvicorn.run(
        "app.ingress:app",
        host="0.0.0.0",
        port=8001,
        reload=False,
    )
