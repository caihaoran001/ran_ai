from __future__ import annotations

import secrets
import string

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.short_link import ShortLink

# base62 字符集；6 位 ≈ 568 亿种组合，随机生成、不可枚举
_ALPHABET = string.ascii_letters + string.digits
_CODE_LEN = 6


async def _generate_unique_code(session: AsyncSession) -> str:
    for _ in range(12):
        code = "".join(secrets.choice(_ALPHABET) for _ in range(_CODE_LEN))
        exists = await session.scalar(select(ShortLink.id).where(ShortLink.code == code))
        if not exists:
            return code
    raise RuntimeError("failed to generate a unique short link code")


async def get_or_create_short_link(
    session: AsyncSession,
    *,
    service_case_id: int | None,
    template_key: str,
    target_path: str,
) -> str:
    """按 (service_case_id, template_key) 复用短码；不存在则生成。返回 code。

    复用避免每天发打卡都生成新码导致表膨胀；target_path 变化时同步更新（token 基本不变，仅作保险）。
    """
    existing = await session.scalar(
        select(ShortLink).where(
            ShortLink.service_case_id == service_case_id,
            ShortLink.template_key == template_key,
        )
    )
    if existing:
        if existing.target_path != target_path:
            existing.target_path = target_path
        return existing.code

    code = await _generate_unique_code(session)
    link = ShortLink(
        code=code,
        service_case_id=service_case_id,
        template_key=template_key,
        target_path=target_path,
    )
    session.add(link)
    await session.flush()
    return code


async def resolve_short_link(session: AsyncSession, code: str) -> str | None:
    """根据短码返回站内目标路径（如 /q/{token}?template=xxx），不存在返回 None。"""
    return await session.scalar(select(ShortLink.target_path).where(ShortLink.code == code))
