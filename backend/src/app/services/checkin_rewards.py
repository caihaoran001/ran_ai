from __future__ import annotations

from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, time as time_of_day, timezone
import logging
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.checkin import Checkin
from app.models.checkin_reward import (
    CheckinRewardProgram,
    CheckinRewardProgressNotice,
    CheckinRewardQualification,
    CheckinRewardRound,
    CheckinRewardShipment,
)
from app.models.message import (
    Message,
    MessageDirection,
    MessagePurpose,
    MessageSenderType,
    MessageStatus,
)
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.services.channels import send_channel_text
from app.services.channels.base import ChannelSendResult
from app.services.checkin_completion import is_complete_checkin
from app.services.checkin_completion import complete_checkin_item_prefix
from app.services.checkin_reward_settings import (
    CheckinRewardTier,
    CheckinRewardTierConfig,
    get_reward_tier_config,
    parse_reward_tiers_config,
)
from app.services.service_case_queries import (
    SERVICE_CASE_SETTLEMENT_GRACE,
    checkin_first_day,
    select_active_service_cases,
)


logger = logging.getLogger(__name__)

PENDING_NOTICE_STATUSES = ("pending", "failed")
OPEN_SHIPMENT_STATUSES = ("missing_address", "pending_shipment")


@dataclass(frozen=True, slots=True)
class RewardReconciliation:
    complete_checkin_days: int
    valid_dates: tuple[date, ...]
    new_qualification_days: tuple[int, ...]
    new_progress_threshold: int | None


async def load_reward_program(
    session: AsyncSession,
) -> CheckinRewardTierConfig | None:
    """读奖励配置；非法配置按"本轮停用"处理并告警，不静默回落。"""
    try:
        return await get_reward_tier_config(session)
    except ValueError:
        logger.warning("打卡奖励档位配置非法，本轮不结算。", exc_info=True)
        return None


class _UnsetType:
    __slots__ = ()


_UNSET = _UnsetType()


def _ensure_utc(moment: datetime) -> datetime:
    """把可能为 naive 的时刻按 UTC 处理；带时区的统一换算到 UTC 再比较。"""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _participation_cutoff(
    open_round: CheckinRewardRound | None,
    config: CheckinRewardTierConfig,
) -> datetime | None:
    """本轮参与的起始时刻（UTC）。

    优先用当前未关闭轮次的 `created_at`——那正是总开关"关→开"的时刻；
    没有轮次记账（手工改库的旧配置）时回落到活动起始日当天 00:00（北京）。
    两者都没有时不设限，返回 None。
    """
    if open_round is not None:
        return _ensure_utc(open_round.created_at)
    if config.program_start_date is not None:
        return datetime.combine(
            config.program_start_date,
            time_of_day.min,
            tzinfo=ZoneInfo("Asia/Shanghai"),
        ).astimezone(timezone.utc)
    return None


async def _load_open_reward_round(session: AsyncSession) -> CheckinRewardRound | None:
    """当前未关闭的奖励轮次；正常情况下最多一条。"""
    return await session.scalar(
        select(CheckinRewardRound)
        .where(CheckinRewardRound.closed_at.is_(None))
        .order_by(CheckinRewardRound.id)
        .limit(1)
    )


async def case_participates_in_current_round(
    session: AsyncSession,
    *,
    service_case: ServiceCase,
    config: CheckinRewardTierConfig | None,
    open_round: CheckinRewardRound | None | _UnsetType = _UNSET,
) -> bool:
    """服务单是否参与本轮打卡奖励：只看建单时刻是否在开关开启之后。

    奖励是产品成本，只有本轮开启那一刻（含）之后新建的服务单才参与；开启前一秒
    建的单都不参与。`open_round` 可由调用方一次性预加载后传入，避免逐单查询。
    """
    if config is None or not config.enabled:
        return False
    if isinstance(open_round, _UnsetType):
        open_round = await _load_open_reward_round(session)
    cutoff = _participation_cutoff(open_round, config)
    if cutoff is None:
        return True
    return _ensure_utc(service_case.created_at) >= cutoff


async def _active_reward_tiers(
    session: AsyncSession,
    *,
    service_case_id: int,
) -> tuple[CheckinRewardTier, ...]:
    """这个用户当前生效的承诺档位；总开关关闭时一档都没有。"""
    config = await load_reward_program(session)
    if config is None or not config.enabled:
        return ()
    program = await session.scalar(
        select(CheckinRewardProgram).where(
            CheckinRewardProgram.service_case_id == service_case_id
        )
    )
    return reward_config_for_program(program, fallback=config).active_tiers()


def is_effective_reward_checkin(checkin: Checkin) -> bool:
    """向后兼容的奖励判定入口；权威口径已下沉到打卡模块。"""
    return is_complete_checkin(checkin)


async def valid_reward_checkin_dates(
    session: AsyncSession,
    *,
    service_case: ServiceCase,
    through_date: date | None = None,
    timezone_name: str | None = None,
) -> list[date]:
    return [
        row.checkin_date
        for row in await valid_reward_checkins(
            session,
            service_case=service_case,
            through_date=through_date,
            timezone_name=timezone_name,
        )
    ]


async def valid_reward_checkins(
    session: AsyncSession,
    *,
    service_case: ServiceCase,
    through_date: date | None = None,
    timezone_name: str | None = None,
) -> list[Checkin]:
    """返回该服务单奖励累计实际计入的完整打卡行。"""

    tz = ZoneInfo(timezone_name or get_settings().reminder_timezone)
    first_day = checkin_first_day(service_case, tz)
    if first_day is None:
        return []
    conditions = [
        Checkin.service_case_id == service_case.id,
        Checkin.checkin_date >= first_day,
        Checkin.deleted_at.is_(None),
    ]
    if through_date is not None:
        conditions.append(Checkin.checkin_date <= through_date)
    rows = list(
        await session.scalars(
            select(Checkin).where(*conditions).order_by(Checkin.checkin_date, Checkin.id)
        )
    )
    # 活跃记录在 DB 层已按服务单+日期唯一；仍按日期去重防御旧数据。
    by_date: dict[date, Checkin] = {}
    for row in rows:
        if is_complete_checkin(row):
            by_date.setdefault(row.checkin_date, row)
    return [by_date[checkin_date] for checkin_date in sorted(by_date)]


def reward_checkin_items(
    checkin: Checkin,
    *,
    labels_by_id: dict[str, str] | None = None,
) -> list[dict[str, object]]:
    """整理使某天完整的同一版五项答案，供运营核验。"""

    parsed = checkin.parsed_json if isinstance(checkin.parsed_json, dict) else {}
    prefix = complete_checkin_item_prefix(parsed)
    schedule = parsed.get("product_schedule")
    custom = parsed.get("custom_checkin")
    if prefix is None or not isinstance(schedule, dict) or not isinstance(custom, dict):
        return []
    labels = labels_by_id or {}

    product_item_id = f"{prefix}:1"
    items: list[dict[str, object]] = [
        {
            "order": 1,
            "item_id": product_item_id,
            # 第 1 项是全局固定产品执行项；旧版 default/profile 标签里的第 1 项
            # 可能仍是晨起体重，不能拿来覆盖当前统一口径。
            "question": "早C、午B、晚A",
            "answer": "；".join(
                (
                    f"早C：{str(schedule.get('morning_c') or '').strip()}",
                    f"午B：{str(schedule.get('noon_b') or '').strip()}",
                    f"晚A：{str(schedule.get('night_a') or '').strip()}",
                )
            ),
        }
    ]
    for index in range(2, 6):
        item_id = f"{prefix}:{index}"
        raw_item = custom.get(item_id)
        if not isinstance(raw_item, dict):
            return []
        answer = str(raw_item.get("answer") or "").strip()
        question = str(raw_item.get("question") or "").strip()
        if not answer:
            return []
        items.append(
            {
                "order": index,
                "item_id": item_id,
                "question": question or labels.get(item_id) or f"专属第 {index} 项",
                "answer": answer,
            }
        )
    return items


def reward_checkin_evidence_message_ids(checkin: Checkin) -> list[int]:
    """只收集落库时保存的用户证据 ID，不从邻近聊天猜测。"""

    parsed = checkin.parsed_json if isinstance(checkin.parsed_json, dict) else {}
    prefix = complete_checkin_item_prefix(parsed)
    custom = parsed.get("custom_checkin")
    values: list[object] = []
    meta = parsed.get("_meta")
    if isinstance(meta, dict):
        stored_ids = meta.get("evidence_message_ids")
        if isinstance(stored_ids, list):
            values.extend(stored_ids)
    if prefix is not None and isinstance(custom, dict):
        for index in range(1, 6):
            item = custom.get(f"{prefix}:{index}")
            if isinstance(item, dict):
                values.append(item.get("evidence_message_id"))
    # 旧数据只有 raw_message_id，它是当批最后一条用户消息，关联精度低。
    # 有逐项或新版精确证据时不再混入该兜底，避免展示同日无关闲聊。
    if not any(value is not None for value in values):
        values.append(checkin.raw_message_id)

    ids: list[int] = []
    for value in values:
        try:
            message_id = int(value) if value is not None else 0
        except (TypeError, ValueError):
            continue
        if message_id > 0 and message_id not in ids:
            ids.append(message_id)
    return sorted(ids)


async def _program_for_locked_case(
    session: AsyncSession, *, service_case_id: int
) -> CheckinRewardProgram:
    program = await session.scalar(
        select(CheckinRewardProgram).where(
            CheckinRewardProgram.service_case_id == service_case_id
        )
    )
    if program is None:
        program = CheckinRewardProgram(service_case_id=service_case_id)
        session.add(program)
        await session.flush()
    return program


def reward_config_for_program(
    program: CheckinRewardProgram | None,
    *,
    fallback: CheckinRewardTierConfig,
) -> CheckinRewardTierConfig:
    """有个人承诺就读承诺；历史记录没有快照时才兼容读当前配置。"""
    if program is None or program.reward_config_snapshot is None:
        return fallback
    snapshot = parse_reward_tiers_config(program.reward_config_snapshot)
    if not snapshot.enabled or not snapshot.tiers:
        raise ValueError("用户奖励承诺快照必须包含已开启的有效档位。")
    return snapshot


def _lock_reward_config_snapshot(
    program: CheckinRewardProgram,
    config: CheckinRewardTierConfig,
) -> CheckinRewardTierConfig:
    if program.reward_config_snapshot is not None:
        return reward_config_for_program(program, fallback=config)
    program.reward_config_snapshot = config.to_dict()
    program.reward_terms_locked_at = datetime.now(timezone.utc)
    return config


async def lock_reward_config_for_case(
    session: AsyncSession,
    *,
    service_case_id: int,
) -> CheckinRewardTierConfig | None:
    """在奖励介绍发送前锁定这个用户的承诺，并返回同一份配置。"""
    service_case = await session.scalar(
        select_active_service_cases(ServiceCase.id == service_case_id).with_for_update()
    )
    if service_case is None:
        return None
    config = await load_reward_program(session)
    if not await case_participates_in_current_round(
        session,
        service_case=service_case,
        config=config,
    ):
        return None
    assert config is not None
    program = await _program_for_locked_case(session, service_case_id=service_case_id)
    return _lock_reward_config_snapshot(program, config)


async def _open_shipment(
    session: AsyncSession, *, service_case_id: int
) -> CheckinRewardShipment | None:
    return await session.scalar(
        select(CheckinRewardShipment)
        .where(
            CheckinRewardShipment.service_case_id == service_case_id,
            CheckinRewardShipment.status.in_(OPEN_SHIPMENT_STATUSES),
        )
        .order_by(CheckinRewardShipment.id.desc())
        .limit(1)
    )


async def reconcile_case_rewards(
    session: AsyncSession,
    *,
    service_case_id: int,
    through_date: date | None = None,
    timezone_name: str | None = None,
) -> RewardReconciliation:
    """按现有打卡账本补齐奖励资格和进度提醒。

    资格一旦获得就不因后续数据修订回收；同一服务单全程只有一轮。
    调用方必须与本事务一起 commit。
    """
    service_case = await session.scalar(
        select_active_service_cases(ServiceCase.id == service_case_id).with_for_update()
    )
    if service_case is None:
        raise ValueError("服务单不存在或已删除。")
    if (
        service_case.member_left_at is not None
        or service_case.status
        not in {ServiceCaseStatus.IN_SERVICE, ServiceCaseStatus.COMPLETED}
    ):
        return RewardReconciliation(0, (), (), None)

    config = await load_reward_program(session)
    if not await case_participates_in_current_round(
        session, service_case=service_case, config=config
    ):
        # 总开关关闭、配置非法，或服务单在本轮开启时刻之前创建：本轮不结算。
        return RewardReconciliation(0, (), (), None)

    assert config is not None
    program = await _program_for_locked_case(session, service_case_id=service_case_id)
    config = _lock_reward_config_snapshot(program, config)
    valid_dates = await valid_reward_checkin_dates(
        session,
        service_case=service_case,
        through_date=through_date,
        timezone_name=timezone_name,
    )
    complete_checkin_days = len(valid_dates)
    existing = {
        row.tier_days: row
        for row in await session.scalars(
            select(CheckinRewardQualification).where(
                CheckinRewardQualification.service_case_id == service_case_id
            )
        )
    }
    shipment = await _open_shipment(session, service_case_id=service_case_id)
    stale_progress = list(
        await session.scalars(
            select(CheckinRewardProgressNotice).where(
                CheckinRewardProgressNotice.service_case_id == service_case_id,
                CheckinRewardProgressNotice.next_tier_days <= complete_checkin_days,
                CheckinRewardProgressNotice.notification_status.in_(PENDING_NOTICE_STATUSES),
            )
        )
    )
    for row in stale_progress:
        row.notification_status = "skipped"
    new_rows: list[CheckinRewardQualification] = []
    active_tiers = config.active_tiers()
    for tier in active_tiers:
        if complete_checkin_days < tier.days or tier.days in existing:
            continue
        row = CheckinRewardQualification(
            service_case_id=service_case_id,
            tier_days=tier.days,
            # 新资格编码固定 day_N：奖励是一句话自由文本，整段存进 reward_name。
            reward_code=f"day_{tier.days}",
            reward_name=tier.reward_text,
            quantity=1,
            qualified_on=valid_dates[tier.days - 1],
            shipment_id=shipment.id if shipment is not None else None,
        )
        session.add(row)
        new_rows.append(row)
        existing[tier.days] = row

    if new_rows:
        if shipment is None:
            program.current_choice = "pending_choice"
        else:
            program.current_choice = "ship_now"
        program.choice_tier_days = max(row.tier_days for row in new_rows)
        program.choice_updated_at = datetime.now(timezone.utc)

    new_progress_threshold = None
    # 进度提醒只面向这个用户已经锁定的档位：threshold = tier.days - 1，
    # 即“再完成 1 天”。后台后来改档不会改变这里的个人承诺。
    active_days = {tier.days for tier in active_tiers}
    candidate_next = complete_checkin_days + 1
    next_tier_days = (
        candidate_next if candidate_next in active_days and candidate_next not in existing else None
    )
    if next_tier_days is not None and next_tier_days not in existing:
        # 同一服务单+同一门槛只有一条提醒（数据库唯一约束）。被作废（skipped）
        # 的那条在档位加回来、用户又正好差一天时重新生效，而不是另插一条。
        progress = await session.scalar(
            select(CheckinRewardProgressNotice).where(
                CheckinRewardProgressNotice.service_case_id == service_case_id,
                CheckinRewardProgressNotice.threshold_days == complete_checkin_days,
            )
        )
        if progress is None:
            session.add(
                CheckinRewardProgressNotice(
                    service_case_id=service_case_id,
                    threshold_days=complete_checkin_days,
                    next_tier_days=next_tier_days,
                )
            )
            new_progress_threshold = complete_checkin_days
        elif progress.notification_status == "skipped":
            progress.notification_status = "pending"
            progress.next_tier_days = next_tier_days
            progress.notification_message_id = None
            new_progress_threshold = complete_checkin_days

    await session.flush()
    return RewardReconciliation(
        complete_checkin_days=complete_checkin_days,
        valid_dates=tuple(valid_dates),
        new_qualification_days=tuple(row.tier_days for row in new_rows),
        new_progress_threshold=new_progress_threshold,
    )


_LEGACY_REWARD_UNITS = {
    "coffee_tube": "管",
    "key_box": "盒",
    "c8_bottle": "瓶",
}


def _qualification_display(rows: list[CheckinRewardQualification]) -> str:
    """老资格按单位聚合；新版同名资格合并数量，但不能把不同档位直接去重。"""

    legacy_quantities: dict[str, int] = defaultdict(int)
    legacy_templates: dict[str, CheckinRewardQualification] = {}
    legacy_first_days: dict[str, int] = {}
    modern_quantities: dict[str, int] = defaultdict(int)
    modern_first_days: dict[str, int] = {}
    for row in sorted(rows, key=lambda item: item.tier_days):
        code = row.reward_code or ""
        if code in _LEGACY_REWARD_UNITS:
            legacy_quantities[code] += row.quantity
            legacy_templates.setdefault(code, row)
            legacy_first_days[code] = min(
                legacy_first_days.get(code, row.tier_days), row.tier_days
            )
            continue
        text = row.reward_name or ""
        if not text:
            continue
        modern_quantities[text] += row.quantity
        modern_first_days[text] = min(
            modern_first_days.get(text, row.tier_days), row.tier_days
        )
    parts: list[tuple[int, str]] = [
        (
            legacy_first_days[code],
            f"{quantity} {_LEGACY_REWARD_UNITS[code]}{legacy_templates[code].reward_name}",
        )
        for code, quantity in legacy_quantities.items()
    ]
    parts.extend(
        (
            modern_first_days[text],
            text if quantity == 1 else f"{text}×{quantity}",
        )
        for text, quantity in modern_quantities.items()
    )
    parts.sort(key=lambda item: item[0])
    return "、".join(text for _, text in parts) or "暂无"


async def render_qualification_notice(
    session: AsyncSession,
    *,
    service_case_id: int,
    newly_qualified: list[CheckinRewardQualification],
) -> str:
    all_rows = list(
        await session.scalars(
            select(CheckinRewardQualification)
            .where(CheckinRewardQualification.service_case_id == service_case_id)
            .order_by(CheckinRewardQualification.tier_days)
        )
    )
    shipped_ids = set(
        await session.scalars(
            select(CheckinRewardShipment.id).where(
                CheckinRewardShipment.service_case_id == service_case_id,
                CheckinRewardShipment.status == "shipped",
            )
        )
    )
    unsent = [row for row in all_rows if row.shipment_id not in shipped_ids]
    active_shipment = await _open_shipment(session, service_case_id=service_case_id)
    highest = max(row.tier_days for row in newly_qualified)
    active_tiers = await _active_reward_tiers(
        session,
        service_case_id=service_case_id,
    )
    max_active_days = (
        max(tier.days for tier in active_tiers) if active_tiers else highest
    )
    lines = [
        f"🎉 恭喜你累计完成 {highest} 天完整打卡！",
        f"本次新获得：{_qualification_display(newly_qualified)}",
        f"累计已获得：{_qualification_display(all_rows)}",
        f"目前尚未寄出：{_qualification_display(unsent)}",
    ]
    if active_shipment is not None:
        lines.append("新奖励已加入你当前待寄出的包裹，由班主任统一安排。")
    elif highest < max_active_days:
        lines.extend(
            [
                "请选择奖励怎么寄：",
                "1 现在寄出（请把收件人、手机号、详细地址发在群里）",
                "2 继续累积，等下一阶段一起寄出",
            ]
        )
    else:
        lines.append(
            "已达到最后一档，请把收件人、手机号和详细地址发在群里，"
            "我们把尚未寄出的奖励一起寄出。"
        )
    return "\n".join(lines)


def render_progress_notice(
    row: CheckinRewardProgressNotice, tier: CheckinRewardTier
) -> str:
    """差一天提醒。奖品名由调用方按这个用户锁定的承诺传入。"""
    return (
        f"你已经累计完成 {row.threshold_days} 天完整打卡，"
        f"再完成 1 天，就能获得 {tier.display}～"
    )


async def _begin_notice_send(
    session: AsyncSession,
    *,
    service_case: ServiceCase,
    content: str,
    analysis_json: dict[str, Any],
) -> Message:
    message = Message(
        service_case_id=service_case.id,
        group_id=service_case.group_id,
        direction=MessageDirection.OUTBOUND,
        sender_type=MessageSenderType.SYSTEM,
        message_type="markdown",
        content=content,
        purpose=MessagePurpose.REMINDER,
        status=MessageStatus.SENDING,
        analysis_json=analysis_json,
    )
    session.add(message)
    await session.flush()
    return message


async def _finalize_notice_message(
    *,
    message_id: int,
    result: ChannelSendResult | None,
    error: BaseException | None = None,
) -> MessageStatus:
    async with SessionLocal() as session:
        message = await session.get(Message, message_id, with_for_update=True)
        if message is None:
            return MessageStatus.UNKNOWN
        now = datetime.now(timezone.utc)
        if error is not None:
            status = MessageStatus.UNKNOWN
            message.error = f"send raised: {type(error).__name__}: {error}"
        elif result is not None and result.success:
            status = MessageStatus.SENT
            message.wecom_message_id = result.message_id
            message.sent_at = now
            message.error = None
        elif result is not None and not result.delivery_uncertain:
            status = MessageStatus.FAILED
            message.error = result.error or "channel send failed"
        else:
            status = MessageStatus.UNKNOWN
            message.error = (result.error if result else None) or "channel result uncertain"
        message.status = status
        await session.commit()
        return status


async def _send_claimed_notice(
    *,
    message_id: int,
    group_id: str,
    content: str,
    send_text: Callable[[str, str], Awaitable[ChannelSendResult]],
) -> MessageStatus:
    try:
        result = await send_text(group_id, content)
    except Exception as exc:
        logger.exception("Check-in reward notice send raised: message_id=%s", message_id)
        return await _finalize_notice_message(message_id=message_id, result=None, error=exc)
    return await _finalize_notice_message(message_id=message_id, result=result)


async def recover_interrupted_reward_notices() -> None:
    """把业务通知状态与已经由启动对账收敛的 Message 终态对齐。"""
    async with SessionLocal() as session:
        changed = False
        programs = list(
            await session.scalars(
                select(CheckinRewardProgram).where(
                    CheckinRewardProgram.intro_status == "sending",
                    CheckinRewardProgram.intro_message_id.is_not(None),
                )
            )
        )
        for program in programs:
            message = await session.get(Message, program.intro_message_id)
            if message is None or message.status == MessageStatus.SENDING:
                continue
            program.intro_status = message.status.value
            changed = True
        for model in (
            CheckinRewardQualification,
            CheckinRewardProgressNotice,
            CheckinRewardShipment,
        ):
            rows = list(
                await session.scalars(
                    select(model).where(
                        model.notification_status == "sending",
                        model.notification_message_id.is_not(None),
                    )
                )
            )
            for row in rows:
                message = await session.get(Message, row.notification_message_id)
                if message is None or message.status == MessageStatus.SENDING:
                    continue
                row.notification_status = message.status.value
                changed = True
        if changed:
            await session.commit()


async def _deliver_pending_qualification_notice(
    *,
    service_case_id: int,
    send_text: Callable[[str, str], Awaitable[ChannelSendResult]],
) -> bool:
    async with SessionLocal() as session:
        service_case = await session.scalar(
            select_active_service_cases(ServiceCase.id == service_case_id).with_for_update()
        )
        if service_case is None:
            return False
        rows = list(
            await session.scalars(
                select(CheckinRewardQualification)
                .where(
                    CheckinRewardQualification.service_case_id == service_case_id,
                    CheckinRewardQualification.notification_status.in_(PENDING_NOTICE_STATUSES),
                )
                .order_by(CheckinRewardQualification.tier_days)
                .with_for_update()
            )
        )
        if not rows:
            return False
        content = await render_qualification_notice(
            session,
            service_case_id=service_case_id,
            newly_qualified=rows,
        )
        message = await _begin_notice_send(
            session,
            service_case=service_case,
            content=content,
            analysis_json={
                "checkin_reward": {
                    "kind": "qualification",
                    "tier_days": [row.tier_days for row in rows],
                }
            },
        )
        for row in rows:
            row.notification_status = "sending"
            row.notification_message_id = message.id
        await session.commit()
        group_id = service_case.group_id
        message_id = message.id

    status = await _send_claimed_notice(
        message_id=message_id,
        group_id=group_id,
        content=content,
        send_text=send_text,
    )
    async with SessionLocal() as session:
        claimed = list(
            await session.scalars(
                select(CheckinRewardQualification).where(
                    CheckinRewardQualification.notification_message_id == message_id
                )
            )
        )
        for row in claimed:
            row.notification_status = status.value
        await session.commit()
    return status == MessageStatus.SENT


async def _deliver_pending_progress_notices(
    *,
    service_case_id: int,
    send_text: Callable[[str, str], Awaitable[ChannelSendResult]],
) -> int:
    sent = 0
    while True:
        async with SessionLocal() as session:
            service_case = await session.scalar(
                select_active_service_cases(ServiceCase.id == service_case_id).with_for_update()
            )
            if service_case is None:
                return sent
            row = await session.scalar(
                select(CheckinRewardProgressNotice)
                .where(
                    CheckinRewardProgressNotice.service_case_id == service_case_id,
                    CheckinRewardProgressNotice.notification_status.in_(PENDING_NOTICE_STATUSES),
                )
                .order_by(CheckinRewardProgressNotice.threshold_days)
                .limit(1)
                .with_for_update()
            )
            if row is None:
                return sent
            # 总开关关闭后，排队中的提醒不再发；保持开启时后台删改档位不影响
            # 这个用户已经锁定的承诺。
            active_tier_map = {
                tier.days: tier
                for tier in await _active_reward_tiers(
                    session,
                    service_case_id=service_case_id,
                )
            }
            tier = active_tier_map.get(row.next_tier_days)
            if tier is None:
                row.notification_status = "skipped"
                await session.commit()
                continue
            content = render_progress_notice(row, tier)
            message = await _begin_notice_send(
                session,
                service_case=service_case,
                content=content,
                analysis_json={
                    "checkin_reward": {
                        "kind": "progress",
                        "threshold_days": row.threshold_days,
                        "next_tier_days": row.next_tier_days,
                    }
                },
            )
            row.notification_status = "sending"
            row.notification_message_id = message.id
            await session.commit()
            row_id = row.id
            message_id = message.id
            group_id = service_case.group_id
        status = await _send_claimed_notice(
            message_id=message_id,
            group_id=group_id,
            content=content,
            send_text=send_text,
        )
        async with SessionLocal() as session:
            claimed = await session.get(CheckinRewardProgressNotice, row_id)
            if claimed is not None:
                claimed.notification_status = status.value
            await session.commit()
        if status != MessageStatus.SENT:
            return sent
        sent += 1


async def deliver_pending_shipment_notices(
    *,
    send_text: Callable[[str, str], Awaitable[ChannelSendResult]] = send_channel_text,
    shipment_ids: list[int] | None = None,
) -> int:
    sent = 0
    async with SessionLocal() as session:
        pending_ids = list(
            await session.scalars(
                select(CheckinRewardShipment.id).where(
                    CheckinRewardShipment.status == "shipped",
                    CheckinRewardShipment.notification_status.in_(PENDING_NOTICE_STATUSES),
                    *(
                        [CheckinRewardShipment.id.in_(shipment_ids)]
                        if shipment_ids is not None
                        else []
                    ),
                )
            )
        )
    for shipment_id in pending_ids:
        async with SessionLocal() as session:
            shipment = await session.get(CheckinRewardShipment, shipment_id, with_for_update=True)
            if shipment is None or shipment.notification_status not in PENDING_NOTICE_STATUSES:
                continue
            service_case = await session.scalar(
                select_active_service_cases(ServiceCase.id == shipment.service_case_id).with_for_update()
            )
            if service_case is None:
                continue
            if service_case.member_left_at is not None:
                # 人已经退群了，这条「奖励已寄出」发进去只会打扰群里现在的新客户。
                # 记成 skipped 终态，运营在后台看得到，不再每轮重试。
                shipment.notification_status = "skipped_member_left"
                await session.commit()
                continue
            content = "你的打卡奖励已寄出，请留意查收～"
            message = await _begin_notice_send(
                session,
                service_case=service_case,
                content=content,
                analysis_json={
                    "checkin_reward": {"kind": "shipped", "shipment_id": shipment.id}
                },
            )
            shipment.notification_status = "sending"
            shipment.notification_message_id = message.id
            await session.commit()
            message_id = message.id
            group_id = service_case.group_id
        status = await _send_claimed_notice(
            message_id=message_id,
            group_id=group_id,
            content=content,
            send_text=send_text,
        )
        async with SessionLocal() as session:
            claimed = await session.get(CheckinRewardShipment, shipment_id)
            if claimed is not None:
                claimed.notification_status = status.value
            await session.commit()
        sent += int(status == MessageStatus.SENT)
    return sent


def _highest_promised_tier_earned(
    program: CheckinRewardProgram | None,
    *,
    qualified_days: set[int],
) -> bool:
    """已结营用户拿满个人承诺的最高档后，不再重复统计打卡。"""

    if program is None or program.reward_config_snapshot is None:
        return False
    try:
        snapshot = parse_reward_tiers_config(program.reward_config_snapshot)
    except ValueError:
        logger.warning(
            "用户奖励承诺快照无法解析，保留结算任务以暴露错误 service_case_id=%s",
            program.service_case_id,
            exc_info=True,
        )
        return False
    tiers = snapshot.active_tiers()
    return bool(tiers) and max(tier.days for tier in tiers) in qualified_days


async def _reward_case_ids(
    session: AsyncSession,
    *,
    local_now: datetime,
) -> tuple[list[int], list[int]]:
    """返回（需要通知的服务单，需要重新结算的服务单）。

    在服用户沿用原扫描范围；已结营用户只有锁定过奖励承诺才长期参与。刚结营但
    尚未生成承诺行的服务单仍保留一天兼容窗口，避免结营调度与首次结算竞态。
    """

    recent_cutoff = local_now.astimezone(timezone.utc) - SERVICE_CASE_SETTLEMENT_GRACE
    rows = list(
        (
            await session.execute(
                select(ServiceCase, CheckinRewardProgram)
                .outerjoin(
                    CheckinRewardProgram,
                    CheckinRewardProgram.service_case_id == ServiceCase.id,
                )
                .where(
                    ServiceCase.deleted_at.is_(None),
                    ServiceCase.member_left_at.is_(None),
                    (ServiceCase.status == ServiceCaseStatus.IN_SERVICE)
                    | (
                        (ServiceCase.status == ServiceCaseStatus.COMPLETED)
                        & (
                            CheckinRewardProgram.reward_config_snapshot.is_not(None)
                            | (
                                ServiceCase.ended_at.is_not(None)
                                & (ServiceCase.ended_at >= recent_cutoff)
                            )
                        )
                    ),
                )
            )
        ).all()
    )
    if not rows:
        return [], []

    case_ids = [service_case.id for service_case, _ in rows]
    qualified_by_case: dict[int, set[int]] = defaultdict(set)
    for service_case_id, tier_days in (
        await session.execute(
            select(
                CheckinRewardQualification.service_case_id,
                CheckinRewardQualification.tier_days,
            ).where(CheckinRewardQualification.service_case_id.in_(case_ids))
        )
    ).all():
        qualified_by_case[service_case_id].add(tier_days)

    pending_notice_case_ids = set(
        await session.scalars(
            select(CheckinRewardQualification.service_case_id)
            .where(
                CheckinRewardQualification.service_case_id.in_(case_ids),
                CheckinRewardQualification.notification_status.in_(
                    PENDING_NOTICE_STATUSES
                ),
            )
            .distinct()
        )
    )
    pending_notice_case_ids.update(
        await session.scalars(
            select(CheckinRewardProgressNotice.service_case_id)
            .where(
                CheckinRewardProgressNotice.service_case_id.in_(case_ids),
                CheckinRewardProgressNotice.notification_status.in_(
                    PENDING_NOTICE_STATUSES
                ),
            )
            .distinct()
        )
    )

    notice_ids: list[int] = []
    reconcile_ids: list[int] = []
    for service_case, program in rows:
        has_finished = (
            service_case.status == ServiceCaseStatus.COMPLETED
            and _highest_promised_tier_earned(
                program,
                qualified_days=qualified_by_case[service_case.id],
            )
        )
        if not has_finished:
            reconcile_ids.append(service_case.id)
        if not has_finished or service_case.id in pending_notice_case_ids:
            notice_ids.append(service_case.id)
    return notice_ids, reconcile_ids


async def check_checkin_rewards_once(
    *,
    now: datetime | None = None,
    send_text: Callable[[str, str], Awaitable[ChannelSendResult]] = send_channel_text,
) -> dict[str, int]:
    settings = get_settings()
    tz = ZoneInfo(settings.reminder_timezone)
    local_now = now.astimezone(tz) if now is not None else datetime.now(tz)
    await recover_interrupted_reward_notices()
    async with SessionLocal() as session:
        case_ids, reconciliation_case_ids = await _reward_case_ids(
            session,
            local_now=local_now,
        )
    for service_case_id in reconciliation_case_ids:
        async with SessionLocal() as session:
            await reconcile_case_rewards(
                session,
                service_case_id=service_case_id,
                through_date=local_now.date(),
                timezone_name=settings.reminder_timezone,
            )
            await session.commit()

    qualification_sent = 0
    progress_sent = 0
    for service_case_id in case_ids:
        qualification_sent += int(
            await _deliver_pending_qualification_notice(
                service_case_id=service_case_id,
                send_text=send_text,
            )
        )
        progress_sent += await _deliver_pending_progress_notices(
            service_case_id=service_case_id,
            send_text=send_text,
        )
    shipment_sent = await deliver_pending_shipment_notices(send_text=send_text)
    return {
        "cases": len(case_ids),
        "qualification_notices_sent": qualification_sent,
        "progress_notices_sent": progress_sent,
        "shipment_notices_sent": shipment_sent,
    }
