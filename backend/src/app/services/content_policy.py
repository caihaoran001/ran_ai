from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_setting import AppSetting

_mode = "manual_review"
SETTING_KEY = "content_delivery_policy"
AI_REPLY_ENABLED_KEY = "ai_reply_enabled"


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


async def get_ai_reply_enabled(session: AsyncSession | None = None) -> bool:
    """全局「AI 回不回复」总开关。默认开启（历史默认即自动回复）。"""
    if session is not None:
        setting = await session.get(AppSetting, AI_REPLY_ENABLED_KEY)
        if setting and isinstance(setting.value_json, dict):
            return bool(setting.value_json.get("enabled", True))
    return True


async def get_ai_reply_state(session: AsyncSession | None = None) -> dict:
    """总开关完整状态：enabled + 是否因风控自动关闭（供前端区分「人工关」/「风控自动关」并显示提醒条）。

    auto_disabled_reason/auto_disabled_at 仅在「系统因企微风控自动关闭」时有值；
    人工手动开/关都会清空这两个字段（见 set_ai_reply_enabled）。
    """
    if session is not None:
        setting = await session.get(AppSetting, AI_REPLY_ENABLED_KEY)
        if setting and isinstance(setting.value_json, dict):
            data = setting.value_json
            return {
                "enabled": bool(data.get("enabled", True)),
                "auto_disabled_reason": data.get("auto_disabled_reason"),
                "auto_disabled_at": data.get("auto_disabled_at"),
            }
    return {"enabled": True, "auto_disabled_reason": None, "auto_disabled_at": None}


async def set_ai_reply_enabled(value: bool, session: AsyncSession | None = None) -> bool:
    """人工设置总开关。手动操作视为「已确认」，一并清空风控自动关闭标记：

    - 手动开启：清除标记 → 提醒条消失，且下次风控可重新触发自动关闭。
    - 手动关闭：清除标记 → 记为人工关闭，而非风控关闭。
    """
    enabled = bool(value)
    if session is not None:
        data = {"enabled": enabled, "auto_disabled_reason": None, "auto_disabled_at": None}
        setting = await session.get(AppSetting, AI_REPLY_ENABLED_KEY)
        if setting:
            setting.value_json = data
        else:
            session.add(AppSetting(key=AI_REPLY_ENABLED_KEY, value_json=data))
        await session.commit()
    return enabled


async def disable_ai_reply_due_to_risk(
    reason: str, session: AsyncSession | None = None
) -> bool:
    """企微账号被风控时自动关闭总开关，并记录原因/时间供前端提醒。

    幂等：若当前已因风控关闭（enabled=False 且带 reason），直接跳过、不刷新时间戳，
    避免同一次风控期间重复的 1011 通知/开机重放反复改写。返回是否本次真正执行了关闭。
    人工重新开启后（标记被清空），下次风控可再次触发本函数。
    """
    if session is None:
        return False
    setting = await session.get(AppSetting, AI_REPLY_ENABLED_KEY)
    current = setting.value_json if setting and isinstance(setting.value_json, dict) else {}
    if current.get("enabled", True) is False and current.get("auto_disabled_reason"):
        return False
    data = {
        "enabled": False,
        "auto_disabled_reason": reason or "企业微信账号被风控，消息无法送达",
        "auto_disabled_at": datetime.now(timezone.utc).isoformat(),
    }
    if setting:
        setting.value_json = data
    else:
        session.add(AppSetting(key=AI_REPLY_ENABLED_KEY, value_json=data))
    await session.commit()
    return True

RISK_REPLY_SETTING_KEY = "risk_fixed_reply"
DEFAULT_RISK_REPLY = "收到，已呼叫营养师班主任，稍后为您解答哈～"

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
QIWE_GROUP_NICKNAME_SETTING_KEY = "qiwe_group_nickname_map"


async def get_qiwe_group_nickname(
    session: AsyncSession | None,
    group_id: str | None,
    user_id: str | None,
) -> str:
    """持久群昵称缓存：按 group_id + user_id 返回群内展示名。"""
    if not group_id or not user_id or session is None or not hasattr(session, "get"):
        return ""
    setting = await session.get(AppSetting, QIWE_GROUP_NICKNAME_SETTING_KEY)
    if not setting or not isinstance(setting.value_json, dict):
        return ""
    members = setting.value_json.get(str(group_id))
    if not isinstance(members, dict):
        return ""
    return str(members.get(str(user_id)) or "").strip()


async def remember_qiwe_group_nicknames(
    session: AsyncSession | None,
    group_id: str | None,
    nicknames: dict[str, str],
) -> None:
    """持久一个群的成员昵称映射，缓存命中后不再请求 QiWe。"""
    if not group_id or not nicknames or session is None or not hasattr(session, "get"):
        return
    cleaned = {
        str(user_id): str(name).strip()
        for user_id, name in nicknames.items()
        if user_id and str(name).strip()
    }
    if not cleaned:
        return

    setting = await session.get(AppSetting, QIWE_GROUP_NICKNAME_SETTING_KEY)
    data = dict(setting.value_json) if setting and isinstance(setting.value_json, dict) else {}
    current = data.get(str(group_id))
    members = dict(current) if isinstance(current, dict) else {}
    changed = any(members.get(user_id) != name for user_id, name in cleaned.items())
    if not changed:
        return
    members.update(cleaned)
    data[str(group_id)] = members
    if setting:
        setting.value_json = data
    else:
        session.add(AppSetting(key=QIWE_GROUP_NICKNAME_SETTING_KEY, value_json=data))
    await session.commit()


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
