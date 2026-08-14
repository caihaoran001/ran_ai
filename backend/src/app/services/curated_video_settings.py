from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_setting import AppSetting


SETTING_KEY = "curated_video_push_enabled"
DEFAULT_SEND_AT = "12:00"
DEFAULT_SCOPE = "active_7d"
VALID_SCOPES = ("all", "active_24h", "active_7d")


async def get_curated_video_push_settings(session: AsyncSession | None = None) -> dict:
    """视频推送配置。旧数据只有 enabled 时，send_at/scope 回落默认值。"""
    data: dict = {}
    if session is not None:
        setting = await session.get(AppSetting, SETTING_KEY)
        if setting and isinstance(setting.value_json, dict):
            data = setting.value_json
    return {
        "enabled": bool(data.get("enabled", True)),
        "send_at": _normalize_time(data.get("send_at")),
        "scope": _normalize_scope(data.get("scope")),
    }


async def set_curated_video_push_settings(
    payload: dict,
    session: AsyncSession | None = None,
) -> dict:
    current = await get_curated_video_push_settings(session)
    value_json = {
        "enabled": bool(payload.get("enabled", current["enabled"])),
        "send_at": _normalize_time(payload.get("send_at") or current["send_at"]),
        "scope": _normalize_scope(payload.get("scope") or current["scope"]),
    }
    if session is not None:
        setting = await session.get(AppSetting, SETTING_KEY)
        if setting:
            setting.value_json = value_json
        else:
            session.add(AppSetting(key=SETTING_KEY, value_json=value_json))
        await session.commit()
    return value_json


def _normalize_time(value: object) -> str:
    text = str(value or DEFAULT_SEND_AT).strip()
    parts = text.split(":")
    if len(parts) < 2:
        return DEFAULT_SEND_AT
    try:
        hour = min(max(int(parts[0]), 0), 23)
        minute = min(max(int(parts[1]), 0), 59)
    except ValueError:
        return DEFAULT_SEND_AT
    return f"{hour:02d}:{minute:02d}"


def _normalize_scope(value: object) -> str:
    text = str(value or DEFAULT_SCOPE).strip()
    return text if text in VALID_SCOPES else DEFAULT_SCOPE