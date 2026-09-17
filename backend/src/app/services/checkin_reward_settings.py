from __future__ import annotations

"""打卡实物奖励的配置：存 `app_settings`，后台可配。

设计五条硬规则：

1. **整份拒收。** 任何一档不合规就整份抛 ValueError，不写半份混合规则。
2. **总开关只影响未来资格。** 关闭后不再产生新资格与新进度提醒，已经获得的
   资格不受影响（由调用方保证，这里只提供 `active_tiers()` 过滤）。
3. **奖励是一句话自由文本。** 如"1 管咖啡"，不拆数量与单位；天数 1..28
   可自定义、可增删，不锁死固定档位。
4. **档位只有"在"和"不在"。** 没有每档独立开关：要这档就加一条、不要就删掉，
   不存在"存着但不生效"的中间状态。历史上存过的每档启用标记在解析时被忽略
   （2026-09-17 取消，线上确认无残留），避免"配置里有、运营以为关着"的分歧。
5. **只有本轮开启那一刻之后新建的服务单参与。** 开关从"关"变"开"时自动记录
   活动起始日并开一轮；参与判定由 `checkin_rewards.case_participates_in_current_round`
   按轮次开启时刻（秒级）与建单时刻比较，开启前建的服务单不参与本轮，避免临近
   服务结束的用户被拉进来却注定拿不到奖励。关闭再打开就是新一轮，起始日更新为
   新的开启日。

缺配置（库里没有这一行）时默认总开关关闭、不产生任何新奖励。库里存着一份
非法配置时不静默回落，直接抛出让调用方看见（同 day28 券配置）。
"""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.app_setting import AppSetting
from app.models.checkin_reward import CheckinRewardRound

logger = logging.getLogger(__name__)

SETTING_KEY = "checkin_reward_tiers"

MIN_DAYS = 1
MAX_DAYS = 28
MAX_TEXT_CHARS = 100


@dataclass(frozen=True)
class CheckinRewardTier:
    days: int
    reward_text: str

    @property
    def display(self) -> str:
        return self.reward_text

    def to_dict(self) -> dict[str, Any]:
        return {"days": self.days, "reward_text": self.reward_text}


@dataclass(frozen=True)
class CheckinRewardTierConfig:
    enabled: bool = False
    program_start_date: date | None = None
    tiers: tuple[CheckinRewardTier, ...] = field(default_factory=tuple)

    def active_tiers(self) -> tuple[CheckinRewardTier, ...]:
        """当前生效的档位：总开关关闭时一档都不产生，否则就是配置里的全部档位。"""
        if not self.enabled:
            return ()
        return self.tiers

    def by_days(self, days: int) -> CheckinRewardTier | None:
        for tier in self.tiers:
            if tier.days == days:
                return tier
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "program_start_date": (
                self.program_start_date.isoformat() if self.program_start_date else None
            ),
            "tiers": [tier.to_dict() for tier in self.tiers],
        }


# 默认四档，文案与历史展示完全一致（测试硬断言这些字符串）；总开关默认关闭。
DEFAULT_REWARD_TIERS: tuple[CheckinRewardTier, ...] = (
    CheckinRewardTier(days=3, reward_text="1 管咖啡"),
    CheckinRewardTier(days=7, reward_text="1 盒钥匙"),
    CheckinRewardTier(days=14, reward_text="1 瓶C8"),
    CheckinRewardTier(days=28, reward_text="3 管咖啡"),
)


def _default_config() -> CheckinRewardTierConfig:
    return CheckinRewardTierConfig(enabled=False, tiers=DEFAULT_REWARD_TIERS)


def _clean_text(value: object, *, label: str, limit: int) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) > limit:
        raise ValueError(f"{label}不能超过 {limit} 个字符。")
    return text


def _clean_date(value: object) -> date | None:
    if value in (None, "", "null"):
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError("活动起始日必须是 YYYY-MM-DD 格式的日期。") from exc


def parse_reward_tiers_config(payload: object) -> CheckinRewardTierConfig:
    """校验并规范化整份档位配置；任何一档不合规就整份拒收。"""
    if payload in (None, ""):
        return _default_config()
    if not isinstance(payload, dict):
        raise ValueError("打卡奖励档位配置必须是对象。")
    enabled = bool(payload.get("enabled", False))
    program_start_date = _clean_date(payload.get("program_start_date"))
    raw_list = payload.get("tiers")
    if raw_list in (None, ""):
        return CheckinRewardTierConfig(
            enabled=enabled,
            program_start_date=program_start_date,
            tiers=DEFAULT_REWARD_TIERS,
        )
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
        # 每档没有独立开关：存下来的每一条都生效，不生效的档位直接删掉。
        tiers.append(CheckinRewardTier(days=days, reward_text=reward_text))
    tiers.sort(key=lambda tier: tier.days)
    return CheckinRewardTierConfig(
        enabled=enabled,
        program_start_date=program_start_date,
        tiers=tuple(tiers),
    )


# 别名：调用方按 parse/get/set 记忆，名称与 day28 的 parse_benefit_config 对齐。
parse_checkin_reward_tiers = parse_reward_tiers_config


def build_reward_receipt_paragraph(config: CheckinRewardTierConfig | None) -> str:
    """按当前档位配置生成入营回执里的奖励介绍段；关闭或无有效档位时返回空串。

    文案与历史默认四档保持同一种说法，档位增删改后自动跟着变：
    多档时“满3、7天，依次可获得A、再得B”；只有一档时“满X天，可获得Y”。
    奖励文字按后台配置原样输出。
    """
    if config is None or not config.enabled:
        return ""
    tiers = config.active_tiers()
    if not tiers:
        return ""
    days_part = "、".join(str(tier.days) for tier in tiers)
    if len(tiers) == 1:
        rewards_part = f"可获得{tiers[0].reward_text}"
    else:
        rewards_part = f"依次可获得{tiers[0].reward_text}" + "".join(
            f"、再得{tier.reward_text}" for tier in tiers[1:]
        )
    return (
        "从服务第1天起，每天完成专属五项打卡。"
        f"累计完整打卡满{days_part}天，{rewards_part}；奖励可以叠加，不要求连续。"
    )


async def get_reward_tier_config(session: AsyncSession) -> CheckinRewardTierConfig:
    """取当前档位配置；没配过就返回默认（总开关关闭、四档待用）。"""
    setting = await session.get(AppSetting, SETTING_KEY)
    if setting is None:
        return _default_config()
    return parse_reward_tiers_config(setting.value_json)


def _today() -> date:
    return datetime.now(ZoneInfo(get_settings().reminder_timezone)).date()


async def set_reward_tier_config(
    session: AsyncSession, payload: object
) -> CheckinRewardTierConfig:
    """保存配置；开关从"关"变"开"时自动记录/刷新活动起始日。

    活动起始日只在首次开启或关闭后再开启时写入；开关保持打开时改奖品、改档位
    不动它（不能意外清掉正在参与的服务单）。调用方显式给出起始日时以调用方为准。
    """
    config = parse_reward_tiers_config(payload)
    setting = await session.get(AppSetting, SETTING_KEY)
    previous: CheckinRewardTierConfig | None = None
    if setting is not None:
        try:
            previous = parse_reward_tiers_config(setting.value_json)
        except ValueError:
            logger.warning("原奖励配置无法解析，本次保存按首次开启处理。", exc_info=True)

    program_start_date = config.program_start_date
    if program_start_date is None:
        if config.enabled and (previous is None or not previous.enabled):
            program_start_date = _today()
        elif previous is not None:
            program_start_date = previous.program_start_date

    stored = CheckinRewardTierConfig(
        enabled=config.enabled,
        program_start_date=program_start_date,
        tiers=config.tiers,
    )
    if setting is None:
        session.add(AppSetting(key=SETTING_KEY, value_json=stored.to_dict()))
    else:
        setting.value_json = stored.to_dict()
    await session.flush()

    # 轮次记账：只在开关状态真正翻转时写，改奖品/改档位不动轮次。
    was_enabled = previous is not None and previous.enabled
    if config.enabled and not was_enabled:
        # 关→开：开一轮，日期取本次落库的活动起始日（没有就用当天）。
        # 已存在未关闭的轮次时不重复开，避免出现两条"当前轮"。
        if await _open_round(session) is None:
            session.add(
                CheckinRewardRound(started_on=stored.program_start_date or _today())
            )
            await session.flush()
    elif was_enabled and not config.enabled:
        # 开→关：给当前未关闭的轮次补上关闭时刻；没有就跳过。
        open_round = await _open_round(session)
        if open_round is not None:
            open_round.closed_at = datetime.now(timezone.utc)
            await session.flush()
    return stored


async def _open_round(session: AsyncSession) -> CheckinRewardRound | None:
    """当前未关闭的奖励轮次；正常情况下最多一条。"""
    return await session.scalar(
        select(CheckinRewardRound)
        .where(CheckinRewardRound.closed_at.is_(None))
        .order_by(CheckinRewardRound.id)
        .limit(1)
    )
