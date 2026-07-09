from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_setting import AppSetting

logger = logging.getLogger("uvicorn.error")

SETTING_KEY = "qiwe_account"


async def get_qiwe_account(session: AsyncSession | None = None) -> dict[str, str]:
    """返回当前生效的企微账号凭证 {"guid", "token"}。

    优先读 app_settings（运营在后台切换后落库的值），没有则回退到 env/settings
    （首次部署时由 .env / compose 注入）。
    """
    if session is not None and hasattr(session, "get"):
        setting = await session.get(AppSetting, SETTING_KEY)
        if setting and isinstance(setting.value_json, dict):
            guid = str(setting.value_json.get("guid") or "").strip()
            token = str(setting.value_json.get("token") or "").strip()
            if guid and token:
                return {"guid": guid, "token": token}

    from app.core.config import get_settings

    settings = get_settings()
    return {
        "guid": (settings.qiwei_guid or "").strip(),
        "token": (settings.qiwei_token or "").strip(),
    }


async def set_qiwe_account(
    guid: str,
    token: str,
    session: AsyncSession | None = None,
) -> dict[str, str]:
    """保存新账号凭证并热切换到运行中的 channel（无需重启后端）。"""
    guid = (guid or "").strip()
    token = (token or "").strip()
    if not guid or not token:
        raise ValueError("guid 和 token 均不能为空")

    if session is not None and hasattr(session, "get"):
        setting = await session.get(AppSetting, SETTING_KEY)
        if setting:
            setting.value_json = {"guid": guid, "token": token}
        else:
            session.add(AppSetting(key=SETTING_KEY, value_json={"guid": guid, "token": token}))
        await session.commit()

    apply_to_live_channel(guid, token)
    return {"guid": guid, "token": token}


def apply_to_live_channel(guid: str, token: str) -> None:
    """把凭证热应用到当前注册的 QiWe channel 单例。

    关键：必须原地 mutate 现有 channel 对象（aggregator 持有的是同一引用），
    不能 set_current_channel 换新对象，否则聚合器仍指向旧账号。
    若当前没有 channel（启动时 env 为空），则新建并注册，供后续回调惰性绑定聚合器。
    """
    from app.services.channels import get_current_channel, set_current_channel
    from app.services.channels.qiwe import QiWeChannel

    channel = get_current_channel()
    if isinstance(channel, QiWeChannel):
        channel.apply_credentials(guid, token)
        logger.info("QiWe account hot-switched on live channel guid=%s", guid)
        return

    if channel is None:
        new_channel = QiWeChannel()
        new_channel.apply_credentials(guid, token)
        set_current_channel(new_channel)
        logger.info("QiWe account applied by creating a new channel guid=%s", guid)
        return

    logger.warning(
        "Current channel is not a QiWeChannel (%s); qiwe account not hot-applied",
        type(channel).__name__,
    )
