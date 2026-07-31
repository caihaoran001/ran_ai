from __future__ import annotations

import secrets

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# 后端 `/api/*` 默认需要 `X-Admin-API-Key`（BACKEND_ADMIN_API_KEY）。
# 该 key 只存在于 Next 服务端 ↔ FastAPI 之间，严禁暴露到浏览器。
#
# 这套鉴权由 backend 主进程与常驻 ingress 进程共用：两侧都挂了公开的
# C 端入口（问卷展示/提交、短链跳转），必须共享同一份豁免规则，否则
# ingress 上会丢失鉴权语义。见 CLAUDE.md「鉴权（跨栈安全红线）」。


def add_admin_auth_middleware(app: FastAPI, settings) -> None:
    """给 app 挂上 admin key 鉴权 middleware（backend 与 ingress 共用）。"""

    @app.middleware("http")
    async def admin_api_key_auth(request: Request, call_next):
        if _requires_admin_auth(request):
            expected = settings.backend_admin_api_key.strip()
            provided = request.headers.get("x-admin-api-key", "").strip()
            if not expected or not secrets.compare_digest(provided, expected):
                return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return await call_next(request)


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
