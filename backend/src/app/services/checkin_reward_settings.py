from __future__ import annotations

"""打卡实物奖励的档位配置：存 `app_settings`，后台可配。

设计三条硬规则：

1. **整份拒收。** 任何一档不合规就整份抛 ValueError，不写半份混合规则。
2. **总开关 + 单档开关只影响未来资格。** 关闭后不再产生新资格与新进度提醒，
   已经获得的资格不受影响（由调用方保证，这里只提供 `active_tiers()` 过滤）。
3. **奖励是一句话自由文本。** 如"1 管咖啡"，不拆数量与单位；天数 1..28
   可自定义、可增删，不锁死固定档位。

缺配置（库里没有这一行）时回落到默认四档全开。库里存着一份非法配置时
**不静默回落**，直接抛出让调用方看见（同 day28 券配置）。
"""

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_setting import AppSetting

SETTING_KEY = "checkin_reward_tiers"

MIN_DAYS = 1
MAX_DAYS = 28
MAX_TEXT_CHARS = 100


@dataclass(frozen=True)
class CheckinRewardTier:
    days: int
    reward_text: str
    enabled: bool = True

    @property
    def display(self) -> str:
        return self.reward_text

    def to_dict(self) -> dict[str, Any]:
        return {
            "days": self.days,
            "reward_text": self.reward_text,
            "enabled": self.enabled,
        }


@dataclass(frozen=True)
class CheckinRewardTierConfig:
    enabled: bool = True
    tiers: tuple[CheckinRewardTier, ...] = field(default_factory=tuple)

    def active_tiers(self) -> tuple[CheckinRewardTier, ...]:
        """只返回开关打开的档位；总开关关闭时一档都不产生。"""
        if not self.enabled:
            return ()
        return tuple(tier for tier in self.tiers if tier.enabled)

    def by_days(self, days: int) -> CheckinRewardTier | None:
        for tier in self.tiers:
            if tier.days == days:
                return tier
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "tiers": [tier.to_dict() for tier in self.tiers],
        }


# 默认四档全开，文案与历史展示完全一致（测试硬断言这些字符串）。
DEFAULT_REWARD_TIERS: tuple[CheckinRewardTier, ...] = (
    CheckinRewardTier(days=3, reward_text="1 管咖啡", enabled=True),
    CheckinRewardTier(days=7, reward_text="1 盒钥匙", enabled=True),
    CheckinRewardTier(days=14, reward_text="1 瓶C8", enabled=True),
    CheckinRewardTier(days=28, reward_text="3 管咖啡", enabled=True),
)


def _default_config() -> CheckinRewardTierConfig:
    return CheckinRewardTierConfig(enabled=True, tiers=DEFAULT_REWARD_TIERS)


def _clean_text(value: object, *, label: str, limit: int) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) > limit:
        raise ValueError(f"{label}不能超过 {limit} 个字符。")
    return text


def parse_reward_tiers_config(payload: object) -> CheckinRewardTierConfig:
    """校验并规范化整份档位配置；任何一档不合规就整份拒收。"""
    if payload in (None, ""):
        return _default_config()
    if not isinstance(payload, dict):
        raise ValueError("打卡奖励档位配置必须是对象。")
    enabled = bool(payload.get("enabled", True))
    raw_list = payload.get("tiers")
    if raw_list in (None, ""):
        return CheckinRewardTierConfig(enabled=enabled, tiers=DEFAULT_REWARD_TIERS)
    if not isinstance(raw_list, list):
        raise ValueError("tiers 必须是列表。")
    tiers: list[CheckinRewardTier] = []
    seen: set[int] = set()
    for raw in raw_list:
        if not isinstance(raw, dict):
            raise ValueError("每个档位必须是对象。")
        try:
            days = int(raw.get("days"))
        except (TypeError, ValueError) as exc:
            raise ValueError("档位天数必须是 1..28 的整数。") from exc
        if days < MIN_DAYS or days > MAX_DAYS:
            raise ValueError(f"档位天数超出范围：{days}，只允许 1..28。")
        if days in seen:
            raise ValueError(f"档位天数重复：{days}")
        seen.add(days)
        reward_text = _clean_text(
            raw.get("reward_text"), label="奖励", limit=MAX_TEXT_CHARS
        )
        if not reward_text:
            raise ValueError(f"{days} 天档缺少奖励内容。")
        tiers.append(
            CheckinRewardTier(
                days=days,
                reward_text=reward_text,
                enabled=bool(raw.get("enabled", True)),
            )
        )
    tiers.sort(key=lambda tier: tier.days)
    return CheckinRewardTierConfig(enabled=enabled, tiers=tuple(tiers))


# 别名：调用方按 parse/get/set 记忆，名称与 day28 的 parse_benefit_config 对齐。
parse_checkin_reward_tiers = parse_reward_tiers_config


async def get_reward_tier_config(session: AsyncSession) -> CheckinRewardTierConfig:
    """取当前档位配置；没配过就返回默认四档全开。"""
    setting = await session.get(AppSetting, SETTING_KEY)
    if setting is None:
        return _default_config()
    return parse_reward_tiers_config(setting.value_json)


async def set_reward_tier_config(
    session: AsyncSession, payload: object
) -> CheckinRewardTierConfig:
    config = parse_reward_tiers_config(payload)
    setting = await session.get(AppSetting, SETTING_KEY)
    if setting is None:
        session.add(AppSetting(key=SETTING_KEY, value_json=config.to_dict()))
    else:
        setting.value_json = config.to_dict()
    await session.flush()
    return config
