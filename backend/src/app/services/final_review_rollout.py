from __future__ import annotations

"""结营回访全量开关：存 `app_settings`，后台可点。

关闭（默认）= 只跑白名单，空名单一单都不跑；
打开 = 到期自动跑，不再看名单。

这个开关只管"进不进候选圈"，每天发不发、补不补还是按26/27/29/30当天、
券29天起补到35天的老规则来。
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_setting import AppSetting

SETTING_KEY = "final_review_rollout"


async def is_full_enabled(session: AsyncSession) -> bool:
    """全量是否打开；没配过、配坏了都按关闭处理。"""
    setting = await session.get(AppSetting, SETTING_KEY)
    value = getattr(setting, "value_json", None)
    if not isinstance(value, dict):
        return False
    return value.get("full_enabled") is True


async def set_full_enabled(session: AsyncSession, enabled: bool) -> bool:
    """以后台动作为准落库；调用方负责提交事务。"""
    flag = bool(enabled)
    setting = await session.get(AppSetting, SETTING_KEY)
    if setting is None:
        session.add(AppSetting(key=SETTING_KEY, value_json={"full_enabled": flag}))
    else:
        setting.value_json = {"full_enabled": flag}
    await session.flush()
    return flag
