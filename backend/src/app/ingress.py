from __future__ import annotations

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from app.api.admin_auth import add_admin_auth_middleware
from app.api.routes.qiwe import router as qiwe_router
from app.api.routes.questionnaires import router as questionnaires_router
from app.api.routes.short_links import router as short_links_router
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
    # ingress 也是 C 端公开入口的常驻收口：问卷展示/提交、短链跳转都挂在这里，
    # backend 频繁重启时这些外部流量不受影响。挂上与 backend 共享的 admin 鉴权
    # middleware，让豁免语义（问卷 GET/submit POST、短链 GET 免 key；PATCH 等
    # 管理端点仍需 key）在两个进程完全一致。
    add_admin_auth_middleware(app, settings)
    app.include_router(qiwe_router, prefix=settings.api_prefix)
    app.include_router(
        questionnaires_router, prefix=f"{settings.api_prefix}/questionnaires"
    )
    app.include_router(short_links_router, prefix=settings.api_prefix)

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
