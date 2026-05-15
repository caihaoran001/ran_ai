from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.config import get_settings

router = APIRouter()


class AutoSendSetting(BaseModel):
    auto_send_documents: bool


@router.get("/settings/auto-send")
async def get_auto_send_setting() -> dict[str, bool]:
    return {"auto_send_documents": get_settings().auto_send_documents}


@router.post("/settings/auto-send")
async def update_auto_send_setting(payload: AutoSendSetting) -> dict[str, bool]:
    return {"auto_send_documents": payload.auto_send_documents}
