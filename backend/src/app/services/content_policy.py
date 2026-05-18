from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_setting import AppSetting

_mode = "manual_review"
SETTING_KEY = "content_delivery_policy"
AI_REPLY_SETTING_KEY = "ai_reply_delivery_policy"


async def get_content_delivery_mode(session: AsyncSession | None = None) -> str:
    if session is not None:
        setting = await session.get(AppSetting, SETTING_KEY)
        if setting:
            return _normalize(setting.value_json.get("mode"))
    return _mode


async def set_content_delivery_mode(value: str, session: AsyncSession | None = None) -> str:
    global _mode
    _mode = _normalize(value)
    if session is not None:
        setting = await session.get(AppSetting, SETTING_KEY)
        if setting:
            setting.value_json = {"mode": _mode}
        else:
            session.add(AppSetting(key=SETTING_KEY, value_json={"mode": _mode}))
        await session.commit()
    return _mode


def _normalize(value: object) -> str:
    return value if value in {"manual_review", "auto_send"} else "manual_review"


async def get_ai_reply_delivery_mode(session: AsyncSession | None = None) -> str:
    if session is not None:
        setting = await session.get(AppSetting, AI_REPLY_SETTING_KEY)
        if setting:
            return _normalize(setting.value_json.get("mode"))
    return "auto_send"


async def set_ai_reply_delivery_mode(value: str, session: AsyncSession | None = None) -> str:
    mode = _normalize(value)
    if session is not None:
        setting = await session.get(AppSetting, AI_REPLY_SETTING_KEY)
        if setting:
            setting.value_json = {"mode": mode}
        else:
            session.add(AppSetting(key=AI_REPLY_SETTING_KEY, value_json={"mode": mode}))
        await session.commit()
    return mode

RISK_REPLY_SETTING_KEY = "risk_fixed_reply"
DEFAULT_RISK_REPLY = (
    "收到您的咨询。为了确保给您提供最安全、精准的方案，本项内容建议您联系群内的班主任，"
    "由班主任为您进行 1 对 1 指导，我也将配合班主任为您全程守护。"
)

async def get_risk_fixed_reply(session: AsyncSession | None = None) -> str:
    if session is not None:
        setting = await session.get(AppSetting, RISK_REPLY_SETTING_KEY)
        if setting:
            return setting.value_json.get("content", DEFAULT_RISK_REPLY)
    return DEFAULT_RISK_REPLY


async def set_risk_fixed_reply(content: str, session: AsyncSession | None = None) -> str:
    if session is not None:
        setting = await session.get(AppSetting, RISK_REPLY_SETTING_KEY)
        if setting:
            setting.value_json = {"content": content}
        else:
            session.add(AppSetting(key=RISK_REPLY_SETTING_KEY, value_json={"content": content}))
        await session.commit()
    return content
