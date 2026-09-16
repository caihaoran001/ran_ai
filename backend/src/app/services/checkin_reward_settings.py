from __future__ import annotations

"""打卡实物奖励的档位配置：存 `app_settings`，后台可配开关。

照抄 `day28_benefit_settings.py` 的校验风格：

1. **整份拒收。** 任何一档不合规就整份抛 ValueError，不写半份混合规则。
2. **长度限制。** 自由文本统一走 `_clean_text` 限长。
3. **天数/编码不可编辑。** `days` 只允许 3/7/14/28，
   `reward_code` 只允许 coffee_tube/key_box/c8_bottle，不在集合直接 ValueError。
4. **开关只影响未来资格。** `enabled=False` 的档位不再产生新资格与进度提醒，
   但已经获得的资格不受影响（由调用方保证，这里只提供 `active_tiers()` 过滤）。

缺配置（库里没有这一行）时回落到默认四档全开，与现有 `REWARD_TIERS` 一致。
库里存着一份非法配置时不静默回落，直接抛出让调用方看见（同 day28）。
"""

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_setting import AppSetting

SETTING_KEY = "checkin_reward_tiers"

ALLOWED_DAYS: tuple[int, ...] = (3, 7, 14, 28)
ALLOWED_REWARD_CODES: tuple[str, ...] = ("coffee_tube", "key_box", "c8_bottle")

MAX_NAME_CHARS = 100
MAX_UNIT_CHARS = 10
MAX_CODE_CHARS = 40


@dataclass(frozen=True)
class CheckinRewardTier:
    days: int
    reward_code: str
    reward_name: str
    quantity: int
    unit: str
    enabled: bool = True

    @property
    def display(self) -> str:
        return f"{self.quantity} {self.unit}{self.reward_name}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "days": self.days,
            "reward_code": self.reward_code,
            "reward_name": self.reward_name,
            "quantity": self.quantity,
            "unit": self.unit,
            "enabled": self.enabled,
        }


@dataclass(frozen=True)
class CheckinRewardTierConfig:
    tiers: tuple[CheckinRewardTier, ...] = field(default_factory=tuple)

    def active_tiers(self) -> tuple[CheckinRewardTier, ...]:
        """只返回开关打开的档位；已获资格的展示由调用方用全量 tiers。"""
        return tuple(tier for tier in self.tiers if tier.enabled)

    def by_days(self, days: int) -> CheckinRewardTier | None:
        for tier in self.tiers:
            if tier.days == days:
                return tier
        return None

    def to_dict(self) -> dict[str, Any]:
        return {"tiers": [tier.to_dict() for tier in self.tiers]}


# 默认沿用现有 REWARD_TIERS 四档，全开。
DEFAULT_REWARD_TIERS: tuple[CheckinRewardTier, ...] = (
    CheckinRewardTier(days=3, reward_code="coffee_tube", reward_name="咖啡", quantity=1, unit="管", enabled=True),
    CheckinRewardTier(days=7, reward_code="key_box", reward_name="钥匙", quantity=1, unit="盒", enabled=True),
    CheckinRewardTier(days=14, reward_code="c8_bottle", reward_name="C8", quantity=1, unit="瓶", enabled=True),
    CheckinRewardTier(days=28, reward_code="coffee_tube", reward_name="咖啡", quantity=3, unit="管", enabled=True),
)


def _clean_text(value: object, *, label: str, limit: int) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) > limit:
        raise ValueError(f"{label}不能超过 {limit} 个字符。")
    return text


def _clean_quantity(value: object, *, days: int) -> int:
    try:
        quantity = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{days} 天档的数量必须是整数。") from exc
    if quantity <= 0 or quantity > 99:
        raise ValueError(f"{days} 天档的数量必须在 1 到 99 之间。")
    return quantity


def parse_reward_tiers_config(payload: object) -> CheckinRewardTierConfig:
    """校验并规范化整份档位配置；任何一档不合规就整份拒收。"""
    if payload in (None, ""):
        return CheckinRewardTierConfig(tiers=DEFAULT_REWARD_TIERS)
    if not isinstance(payload, dict):
        raise ValueError("打卡奖励档位配置必须是对象。")
    raw_list = payload.get("tiers")
    if raw_list in (None, ""):
        return CheckinRewardTierConfig(tiers=DEFAULT_REWARD_TIERS)
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
            raise ValueError("档位天数必须是 3/7/14/28 中的一个。") from exc
        if days not in ALLOWED_DAYS:
            raise ValueError(f"档位天数不支持：{days}，只允许 3/7/14/28。")
        if days in seen:
            raise ValueError(f"档位天数重复：{days}")
        seen.add(days)
        code = _clean_text(raw.get("reward_code"), label="奖励编码", limit=MAX_CODE_CHARS)
        if code not in ALLOWED_REWARD_CODES:
            raise ValueError(f"{days} 天档的奖励编码不支持：{code or '空'}。")
        name = _clean_text(raw.get("reward_name"), label="奖励名称", limit=MAX_NAME_CHARS)
        if not name:
            raise ValueError(f"{days} 天档缺少奖励名称。")
        unit = _clean_text(raw.get("unit"), label="奖励单位", limit=MAX_UNIT_CHARS)
        if not unit:
            raise ValueError(f"{days} 天档缺少奖励单位。")
        tiers.append(
            CheckinRewardTier(
                days=days,
                reward_code=code,
                reward_name=name,
                quantity=_clean_quantity(raw.get("quantity"), days=days),
                unit=unit,
                enabled=bool(raw.get("enabled", True)),
            )
        )
    tiers.sort(key=lambda tier: tier.days)
    return CheckinRewardTierConfig(tiers=tuple(tiers))


# 别名：调用方按 parse/get/set 记忆，名称与 day28 的 parse_benefit_config 对齐。
parse_checkin_reward_tiers = parse_reward_tiers_config


async def get_reward_tier_config(session: AsyncSession) -> CheckinRewardTierConfig:
    """取当前档位配置；没配过就返回默认四档全开。"""
    setting = await session.get(AppSetting, SETTING_KEY)
    if setting is None:
        return CheckinRewardTierConfig(tiers=DEFAULT_REWARD_TIERS)
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
