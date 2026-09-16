from __future__ import annotations

from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
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
    DEFAULT_REWARD_TIERS,
    CheckinRewardTier,
    CheckinRewardTierConfig,
    get_reward_tier_config,
)
from app.services.service_case_queries import (
    SERVICE_CASE_SETTLEMENT_GRACE,
    checkin_first_day,
    select_active_service_cases,
    service_continuation_active,
    service_phase_end_date,
    service_settlement_ended,
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


async def _configured_reward_tiers(
    session: AsyncSession,
) -> tuple[CheckinRewardTier, ...]:
    """展示用：读全部档位（含关闭的）；非法时回落到默认四档。"""
    config = await load_reward_program(session)
    return config.tiers if config is not None else DEFAULT_REWARD_TIERS


async def _active_reward_tiers(
    session: AsyncSession,
) -> tuple[CheckinRewardTier, ...]:
    """展示用：只返回开关打开的档位；非法配置按"全部关闭"处理。"""
    config = await load_reward_program(session)
    return config.active_tiers() if config is not None else ()


async def _reward_tier_map(session: AsyncSession) -> dict[int, CheckinRewardTier]:
    return {tier.days: tier for tier in await _configured_reward_tiers(session)}


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
    if not service_continuation_active(service_case):
        phase_end = service_phase_end_date(service_case, tz)
        if phase_end is not None:
            conditions.append(Checkin.checkin_date <= phase_end)
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
    allow_completed_settlement: bool = False,
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
    allowed_statuses = {ServiceCaseStatus.IN_SERVICE}
    if allow_completed_settlement:
        allowed_statuses.add(ServiceCaseStatus.COMPLETED)
    if service_case.status not in allowed_statuses:
        return RewardReconciliation(0, (), (), None)

    config = await load_reward_program(session)
    tz = ZoneInfo(timezone_name or get_settings().reminder_timezone)
    if config is None or not config.participates(checkin_first_day(service_case, tz)):
        # 总开关关闭、配置非法，或服务单在活动起始日之前开始：本轮不结算。
        return RewardReconciliation(0, (), (), None)

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
    program = await _program_for_locked_case(session, service_case_id=service_case_id)
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
    # 进度提醒只面向仍开着的档位：threshold = tier.days - 1，即"再完成 1 天"。
    # 已产生的进度提醒不受开关影响（stale 逻辑在上文只按天数收敛）。
    active_days = {tier.days for tier in active_tiers}
    candidate_next = complete_checkin_days + 1
    next_tier_days = (
        candidate_next if candidate_next in active_days and candidate_next not in existing else None
    )
    if next_tier_days is not None and next_tier_days not in existing:
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
    """老资格按旧格式聚合展示（如 3 管咖啡）；v2 资格直接展示配置的一句话奖励。"""

    legacy_quantities: dict[str, int] = defaultdict(int)
    legacy_templates: dict[str, CheckinRewardQualification] = {}
    legacy_first_days: dict[str, int] = {}
    modern: list[tuple[int, str]] = []
    seen_texts: set[str] = set()
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
        if row.quantity > 1:
            text = f"{text}×{row.quantity}"
        if not text or text in seen_texts:
            continue
        seen_texts.add(text)
        modern.append((row.tier_days, text))
    parts: list[tuple[int, str]] = [
        (
            legacy_first_days[code],
            f"{quantity} {_LEGACY_REWARD_UNITS[code]}{legacy_templates[code].reward_name}",
        )
        for code, quantity in legacy_quantities.items()
    ]
    parts.extend(modern)
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
    active_tiers = await _active_reward_tiers(session)
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


async def render_progress_notice(
    session: AsyncSession, row: CheckinRewardProgressNotice
) -> str:
    tier_map = await _reward_tier_map(session)
    tier = tier_map.get(row.next_tier_days)
    if tier is None:
        return (
            f"你已经累计完成 {row.threshold_days} 天完整打卡，"
            "再完成 1 天，就能获得下一档奖励～"
        )
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
            content = await render_progress_notice(session, row)
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
        settlement_cutoff = local_now.astimezone(timezone.utc) - SERVICE_CASE_SETTLEMENT_GRACE
        case_rows = list(
            await session.scalars(
                select(ServiceCase).where(
                    ServiceCase.deleted_at.is_(None),
                    # 客户已退群的服务单不再结算奖励、也不往群里发奖励通知：
                    # 群位可能已经换成新客户了。
                    ServiceCase.member_left_at.is_(None),
                    (
                        (ServiceCase.status == ServiceCaseStatus.IN_SERVICE)
                        | (
                            (ServiceCase.status == ServiceCaseStatus.COMPLETED)
                            & ServiceCase.ended_at.is_not(None)
                            & (ServiceCase.ended_at >= settlement_cutoff)
                        )
                    ),
                )
            )
        )
    case_rows = [
        row
        for row in case_rows
        if row.status != ServiceCaseStatus.COMPLETED
        or not service_settlement_ended(row, local_now, tz)
    ]
    case_ids = [row.id for row in case_rows]
    completed_case_ids = {
        row.id for row in case_rows if row.status == ServiceCaseStatus.COMPLETED
    }
    for service_case_id in case_ids:
        async with SessionLocal() as session:
            await reconcile_case_rewards(
                session,
                service_case_id=service_case_id,
                through_date=local_now.date(),
                timezone_name=settings.reminder_timezone,
                allow_completed_settlement=service_case_id in completed_case_ids,
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
