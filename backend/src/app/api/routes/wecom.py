from __future__ import annotations

from fastapi import APIRouter, Request

from app.core.config import get_settings

router = APIRouter()


@router.get("/wecom/status")
async def get_wecom_status(request: Request) -> dict[str, object]:
    settings = get_settings()
    worker = getattr(request.app.state, "wecom_worker", None)
    bridge = getattr(worker, "bridge", None)
    return {
        "enabled": settings.wecom_worker_enabled,
        "running": bool(bridge and bridge.running),
        "connected": bool(bridge and bridge.connected),
        "last_error": getattr(bridge, "last_error", None) if bridge else None,
        "dm_policy": settings.wecom_dm_policy,
        "group_policy": settings.wecom_group_policy,
    }
