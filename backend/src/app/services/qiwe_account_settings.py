from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_setting import AppSetting

logger = logging.getLogger("uvicorn.error")

SETTING_KEY = "qiwe_account"

# app_settings["qiwe_account"].value_json 形状：
# {
#   "current_guid": "<guid>",
#   "accounts": {
#     "<guid>": {"guid","token","nickname","alias","avatar_url","real_name",
#                "saved_at","last_used_at"}
#   }
# }
# 兼容旧形状 {"guid","token"}：读取时迁移为单条 history。


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_account(guid: str, token: str) -> dict[str, Any]:
    return {
        "guid": guid,
        "token": token,
        "nickname": "",
        "alias": "",
        "avatar_url": "",
        "real_name": "",
        "note": "",
        "saved_at": _now_iso(),
        "last_used_at": None,
    }


def _seed_env_account(state: dict[str, Any]) -> bool:
    """把 env 配置的账号补录进 accounts（幂等）。

    保证「最初用 .env 配置、从未在后台保存过」的账号也作为一条正式历史记录存在，
    这样切换到别的账号后仍能在历史里点回来——否则初始账号会凭空消失。
    返回是否有改动（供调用方决定是否落库）。
    """
    from app.core.config import get_settings

    settings = get_settings()
    guid = (settings.qiwei_guid or "").strip()
    token = (settings.qiwei_token or "").strip()
    if not (guid and token):
        return False
    accounts = state.setdefault("accounts", {})
    changed = False
    if guid not in accounts:
        accounts[guid] = _empty_account(guid, token)
        changed = True
    if not state.get("current_guid"):
        state["current_guid"] = guid
        changed = True
    return changed


def _normalize_state(value: Any) -> dict[str, Any]:
    """把落库的 value_json 规整为 {current_guid, accounts} 形状，兼容旧格式。"""
    if not isinstance(value, dict):
        return {"current_guid": "", "accounts": {}}
    if "accounts" in value and isinstance(value["accounts"], dict):
        return {
            "current_guid": str(value.get("current_guid") or ""),
            "accounts": value["accounts"],
        }
    # 旧形状 {"guid","token"} → 迁移
    guid = str(value.get("guid") or "").strip()
    token = str(value.get("token") or "").strip()
    if guid and token:
        return {"current_guid": guid, "accounts": {guid: _empty_account(guid, token)}}
    return {"current_guid": "", "accounts": {}}


async def _load_state(session: AsyncSession | None) -> dict[str, Any]:
    if session is not None and hasattr(session, "get"):
        setting = await session.get(AppSetting, SETTING_KEY)
        if setting:
            return _normalize_state(setting.value_json)
    return {"current_guid": "", "accounts": {}}


async def _save_state(session: AsyncSession, state: dict[str, Any]) -> None:
    setting = await session.get(AppSetting, SETTING_KEY)
    if setting:
        setting.value_json = state
    else:
        session.add(AppSetting(key=SETTING_KEY, value_json=state))
    await session.commit()


async def get_qiwe_account(session: AsyncSession | None = None) -> dict[str, str]:
    """返回当前生效的企微账号凭证 {"guid","token"}。

    优先读 app_settings 里 current_guid 指向的账号；没有则回退 env/settings。
    供 lifespan 启动应用与任何需要当前凭证的地方使用。
    """
    state = await _load_state(session)
    current_guid = state.get("current_guid") or ""
    account = state.get("accounts", {}).get(current_guid)
    if account and account.get("guid") and account.get("token"):
        return {"guid": str(account["guid"]).strip(), "token": str(account["token"]).strip()}

    from app.core.config import get_settings

    settings = get_settings()
    return {
        "guid": (settings.qiwei_guid or "").strip(),
        "token": (settings.qiwei_token or "").strip(),
    }


def _public_entry(account: dict[str, Any], *, is_current: bool) -> dict[str, Any]:
    """脱敏后的账号视图（不含明文 token）。"""
    token = str(account.get("token") or "")
    return {
        "guid": str(account.get("guid") or ""),
        "nickname": str(account.get("nickname") or ""),
        "alias": str(account.get("alias") or ""),
        "avatar_url": str(account.get("avatar_url") or ""),
        "real_name": str(account.get("real_name") or ""),
        "note": str(account.get("note") or ""),
        "last_used_at": account.get("last_used_at"),
        "saved_at": account.get("saved_at"),
        "is_current": is_current,
        "token_masked": _mask(token),
        "token_set": bool(token),
    }


def _mask(token: str) -> str:
    token = (token or "").strip()
    if not token:
        return ""
    return "****" if len(token) <= 4 else f"****{token[-4:]}"


async def get_qiwe_account_state(session: AsyncSession | None = None) -> dict[str, Any]:
    """返回给前端的账号状态：当前账号 + 历史账号列表（均脱敏）。

    当前账号若还没有昵称（例如 env 配置但从未在后台保存过），会 best-effort
    调一次 getProfile 补出真实昵称/头像；若该账号已落库则顺带缓存，下次直接命中。
    """
    state = await _load_state(session)
    # 幂等补录 env 账号，保证初始账号也是一条可切回的历史记录
    changed = _seed_env_account(state)
    accounts: dict[str, Any] = state.get("accounts", {})
    current_guid = state.get("current_guid") or ""
    if not accounts:
        return {"current": None, "history": []}

    current_raw = accounts.get(current_guid)
    if current_raw and not (current_raw.get("nickname") or current_raw.get("alias")):
        fields = _profile_fields(
            await fetch_account_profile(
                str(current_raw.get("guid") or ""),
                str(current_raw.get("token") or ""),
            )
        )
        if fields.get("nickname") or fields.get("alias"):
            current_raw.update(fields)
            changed = True

    if changed and session is not None and hasattr(session, "get"):
        await _save_state(session, state)

    history = [
        _public_entry(acc, is_current=(guid == current_guid))
        for guid, acc in accounts.items()
    ]
    # 最近使用在前
    history.sort(key=lambda e: (e["is_current"], e.get("last_used_at") or e.get("saved_at") or ""), reverse=True)
    current = next((e for e in history if e["is_current"]), None)
    return {"current": current, "history": history}


async def fetch_account_profile(guid: str, token: str) -> dict[str, Any] | None:
    """用给定凭证调用 QiWe /user/getProfile，成功返回账号资料，失败返回 None。"""
    from app.services.channels.qiwe import QiWeChannel

    channel = QiWeChannel()
    channel.guid = (guid or "").strip()
    channel.token = (token or "").strip()
    return await channel.get_profile()


def _profile_fields(profile: dict[str, Any] | None) -> dict[str, str]:
    if not isinstance(profile, dict):
        return {}
    return {
        "nickname": str(profile.get("nickname") or ""),
        "alias": str(profile.get("alias") or ""),
        "avatar_url": str(profile.get("avatarUrl") or ""),
        "real_name": str(profile.get("realName") or ""),
    }


async def add_and_switch_account(
    guid: str,
    token: str,
    session: AsyncSession | None = None,
) -> dict[str, Any]:
    """保存新账号（或更新已有 guid 的 token）并热切换到它。

    返回 {"state": <脱敏状态>, "warning": <可能为空的提示>}。
    校验为 fail-open：getProfile 失败仍允许切换，但回填 warning 供前端提示。
    """
    guid = (guid or "").strip()
    token = (token or "").strip()
    if not guid or not token:
        raise ValueError("GUID 和 Token 均不能为空")

    warning = ""
    profile = await fetch_account_profile(guid, token)
    fields = _profile_fields(profile)
    if profile is None:
        warning = "已保存并切换，但未能获取该账号资料（可能设备未上线或凭证异常），请确认收发是否正常。"

    if session is not None and hasattr(session, "get"):
        state = await _load_state(session)
        _seed_env_account(state)  # 保证切走前，原初始账号已落库、之后能切回
        accounts = state.setdefault("accounts", {})
        existing = accounts.get(guid) or _empty_account(guid, token)
        existing.update({"guid": guid, "token": token})
        if fields.get("nickname") or fields.get("alias"):
            existing.update(fields)
        existing["last_used_at"] = _now_iso()
        accounts[guid] = existing
        state["current_guid"] = guid
        await _save_state(session, state)

    apply_to_live_channel(guid, token)
    result = await get_qiwe_account_state(session)
    return {"state": result, "warning": warning}


async def switch_to_account(
    guid: str,
    session: AsyncSession | None = None,
) -> dict[str, Any]:
    """切换到历史里已存在的账号（token 从服务端取，不经浏览器）。"""
    guid = (guid or "").strip()
    if not guid:
        raise ValueError("guid 不能为空")
    if session is None or not hasattr(session, "get"):
        raise ValueError("无法访问账号存储")

    state = await _load_state(session)
    _seed_env_account(state)  # 保证初始账号也在历史里，可被切回
    accounts = state.get("accounts", {})
    account = accounts.get(guid)
    if not account or not account.get("token"):
        raise LookupError("该历史账号不存在或缺少凭证")

    token = str(account["token"]).strip()
    warning = ""
    # 切换时顺带校验+刷新昵称（best-effort，不阻断切换）
    profile = await fetch_account_profile(guid, token)
    fields = _profile_fields(profile)
    if profile is None:
        warning = "已切换，但未能获取该账号资料（可能设备未上线或凭证异常），请确认收发是否正常。"
    elif fields.get("nickname") or fields.get("alias"):
        account.update(fields)
    account["last_used_at"] = _now_iso()
    state["current_guid"] = guid
    await _save_state(session, state)

    apply_to_live_channel(guid, token)
    result = await get_qiwe_account_state(session)
    return {"state": result, "warning": warning}


async def set_account_note(
    guid: str,
    note: str,
    session: AsyncSession | None = None,
) -> dict[str, Any]:
    """给历史里已存在的账号设置/更新备注（运营自定义记忆）。不影响收发。"""
    guid = (guid or "").strip()
    note = (note or "").strip()
    if not guid:
        raise ValueError("guid 不能为空")
    if session is None or not hasattr(session, "get"):
        raise ValueError("无法访问账号存储")

    state = await _load_state(session)
    _seed_env_account(state)  # 保证初始账号也在历史里，可被备注
    accounts = state.get("accounts", {})
    account = accounts.get(guid)
    if not account:
        raise LookupError("该历史账号不存在")

    account["note"] = note
    await _save_state(session, state)
    result = await get_qiwe_account_state(session)
    return {"state": result, "warning": ""}


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
