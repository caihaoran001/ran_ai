from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_setting import AppSetting


SETTING_KEY = "curated_video_push_enabled"


async def get_curated_video_push_enabled(session: AsyncSession | None = None) -> bool:
    """视频推送全局开关。默认开启（与 CURATED_VIDEO_ENABLED 现状一致）。"""
    if session is not None:
        setting = await session.get(AppSetting, SETTING_KEY)
        if setting and isinstance(setting.value_json, dict):
            return bool(setting.value_json.get("enabled", True))
    return True


async def set_curated_video_push_enabled(value: bool, session: AsyncSession | None = None) -> bool:
    enabled = bool(value)
    if session is not None:
        data = {"enabled": enabled}
        setting = await session.get(AppSetting, SETTING_KEY)
        if setting:
            setting.value_json = data
        else:
            session.add(AppSetting(key=SETTING_KEY, value_json=data))
        await session.commit()
    return enabled