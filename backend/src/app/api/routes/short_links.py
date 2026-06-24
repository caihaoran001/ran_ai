from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.services.short_link import resolve_short_link

router = APIRouter()


@router.get("/short-links/{code}")
async def get_short_link(code: str, session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    """公开解析短码 → 站内目标路径，供前端 /s/[code] 重定向使用（免 admin key）。"""
    target = await resolve_short_link(session, code)
    if not target:
        raise HTTPException(status_code=404, detail="short link not found")
    return {"target_path": target}
