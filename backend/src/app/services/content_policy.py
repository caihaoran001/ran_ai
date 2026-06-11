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

STAFF_SENDERS_SETTING_KEY = "qiwei_staff_senders"

async def get_qiwei_staff_senders(session: AsyncSession | None = None) -> list[dict[str, str]]:
    if session is not None and hasattr(session, "get"):
        setting = await session.get(AppSetting, STAFF_SENDERS_SETTING_KEY)
        if setting:
            return setting.value_json.get("rules", [])
    
    # Fallback to env variable
    from app.core.config import get_settings
    env_senders = get_settings().qiwei_staff_senders
    if env_senders:
        return [
            {"type": "equals", "pattern": name.strip()}
            for name in env_senders.split(",")
            if name.strip()
        ]
    return []

async def set_qiwei_staff_senders(rules: list[dict[str, str]], session: AsyncSession | None = None) -> list[dict[str, str]]:
    if session is not None and hasattr(session, "get"):
        setting = await session.get(AppSetting, STAFF_SENDERS_SETTING_KEY)
        if setting:
            setting.value_json = {"rules": rules}
        else:
            session.add(AppSetting(key=STAFF_SENDERS_SETTING_KEY, value_json={"rules": rules}))
        await session.commit()
    return rules


QIWE_NICKNAME_SETTING_KEY = "qiwe_nickname_map"


async def get_qiwe_nickname(session: AsyncSession | None, user_id: str | None) -> str:
    """持久昵称缓存：返回 user_id 已知的微信昵称，没有返回空。"""
    if not user_id or session is None or not hasattr(session, "get"):
        return ""
    setting = await session.get(AppSetting, QIWE_NICKNAME_SETTING_KEY)
    if setting and isinstance(setting.value_json, dict):
        return str(setting.value_json.get(str(user_id)) or "")
    return ""


async def remember_qiwe_nickname(session: AsyncSession | None, user_id: str | None, nickname: str) -> None:
    """把解析到的昵称写入持久缓存，避免重启后重复调用 API。"""
    if not user_id or not nickname or session is None or not hasattr(session, "get"):
        return
    setting = await session.get(AppSetting, QIWE_NICKNAME_SETTING_KEY)
    if setting and isinstance(setting.value_json, dict):
        if setting.value_json.get(str(user_id)) == nickname:
            return
        data = dict(setting.value_json)
        data[str(user_id)] = nickname
        setting.value_json = data
    else:
        session.add(AppSetting(key=QIWE_NICKNAME_SETTING_KEY, value_json={str(user_id): nickname}))
    await session.commit()
