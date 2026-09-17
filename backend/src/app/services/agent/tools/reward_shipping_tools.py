from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Literal

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.checkin_reward import (
    CheckinRewardProgram,
    CheckinRewardQualification,
    CheckinRewardShipment,
)
from app.models.message import (
    Message,
    MessageDirection,
    MessagePurpose,
    MessageSenderType,
)
from app.models.service_case import ServiceCase
from app.services.agent.state import AgentRunContext
from app.services.checkin_rewards import (
    OPEN_SHIPMENT_STATUSES,
    _qualification_display,
    case_participates_in_current_round,
    reward_config_for_program,
    valid_reward_checkins,
)
from app.services.checkin_reward_settings import (
    CheckinRewardTierConfig,
    get_reward_tier_config,
)
from app.services.service_case_queries import select_active_service_cases


logger = logging.getLogger(__name__)

RewardShippingAction = Literal[
    "get_status",
    "ship_now",
    "accumulate",
    "save_shipping_info",
]

REWARD_PROGRAM_PAUSED_REPLY = "奖励规则正在讨论中，确认后会另行通知。"


class RewardShippingArgs(BaseModel):
    action: RewardShippingAction = Field(
        description=(
            "get_status=read a pending reward choice only to interpret an otherwise ambiguous bare reply such as "
            "1 or 2; never use it for an immediate-shipment request that is missing recipient fields; "
            "ship_now=user clearly chooses immediate shipment; "
            "accumulate=user clearly chooses to wait for a later tier; save_shipping_info=add or correct recipient details."
        )
    )
    recipient_name: str | None = Field(
        default=None,
        description=(
            "Recipient name explicitly supplied by the user; required together with phone and address for "
            "ship_now or save_shipping_info unless ship_now explicitly reuses a complete saved address."
        ),
    )
    recipient_phone: str | None = Field(
        default=None,
        description=(
            "Phone number explicitly supplied by the user; required together with recipient and address for "
            "ship_now or save_shipping_info unless ship_now explicitly reuses a complete saved address."
        ),
    )
    shipping_address: str | None = Field(
        default=None,
        description=(
            "Detailed shipping address explicitly supplied by the user; required together with recipient and "
            "phone for ship_now or save_shipping_info unless ship_now explicitly reuses a complete saved address."
        ),
    )
    reuse_saved_address: bool = Field(
        default=False,
        description="True only when the user explicitly asks to reuse their previously saved recipient information.",
    )


def _clean(value: str | None) -> str | None:
    cleaned = " ".join(str(value or "").split()).strip()
    return cleaned or None


def _paused_reward_payload() -> dict[str, Any]:
    return {
        "ok": True,
        "rewards_enabled": False,
        "program_paused": True,
        "reply_instruction": REWARD_PROGRAM_PAUSED_REPLY,
    }


async def _current_user_source(session, context: AgentRunContext) -> Message | None:
    if context.trigger_id is None:
        return None
    return await session.scalar(
        select(Message).where(
            Message.id == context.trigger_id,
            Message.service_case_id == context.service_case_id,
            Message.direction == MessageDirection.INBOUND,
            Message.purpose == MessagePurpose.CHAT,
            Message.sender_type == MessageSenderType.USER,
        )
    )


async def _reward_status(
    session, *, service_case_id: int, config: CheckinRewardTierConfig
) -> dict[str, Any]:
    service_case = await session.scalar(
        select_active_service_cases(ServiceCase.id == service_case_id)
    )
    if service_case is None:
        raise ValueError("当前服务单不存在或已删除。")
    participating = await case_participates_in_current_round(
        session, service_case=service_case, config=config
    )
    program = await session.scalar(
        select(CheckinRewardProgram).where(
            CheckinRewardProgram.service_case_id == service_case_id
        )
    )
    reward_config = reward_config_for_program(program, fallback=config)
    qualifications = list(
        await session.scalars(
            select(CheckinRewardQualification)
            .where(CheckinRewardQualification.service_case_id == service_case_id)
            .order_by(CheckinRewardQualification.tier_days)
        )
    )
    shipments = list(
        await session.scalars(
            select(CheckinRewardShipment)
            .where(CheckinRewardShipment.service_case_id == service_case_id)
            .order_by(CheckinRewardShipment.id)
        )
    )
    shipped_ids = {row.id for row in shipments if row.status == "shipped"}
    unsent = [row for row in qualifications if row.shipment_id not in shipped_ids]
    active = next(
        (row for row in reversed(shipments) if row.status in OPEN_SHIPMENT_STATUSES),
        None,
    )
    active_items = (
        [row for row in qualifications if row.shipment_id == active.id]
        if active is not None
        else []
    )
    missing_fields = []
    if active is not None:
        if not _clean(active.recipient_name):
            missing_fields.append("收件人")
        if not _clean(active.recipient_phone):
            missing_fields.append("手机号")
        if not _clean(active.shipping_address):
            missing_fields.append("详细地址")
    complete_checkins = (
        await valid_reward_checkins(
            session,
            service_case=service_case,
        )
        if participating
        else []
    )
    return {
        "service_case": service_case,
        "complete_checkin_days": len(complete_checkins),
        "participating": participating,
        "program_start_date": reward_config.program_start_date,
        "program": program,
        "reward_config": reward_config,
        "qualifications": qualifications,
        "shipments": shipments,
        "unsent": unsent,
        "active_shipment": active,
        "active_items": active_items,
        "missing_fields": missing_fields,
    }


async def _load_reward_config(session) -> CheckinRewardTierConfig | None:
    """后台奖励配置；损坏时按"本轮停用"处理，不猜规则。"""
    try:
        return await get_reward_tier_config(session)
    except ValueError:
        logger.warning("打卡奖励档位配置非法，按本轮停用处理。", exc_info=True)
        return None


def _paused_write_result() -> dict[str, Any]:
    return {
        "ok": False,
        "rewards_enabled": False,
        "program_paused": True,
        "error": "奖励计划已暂停，不记录发货、累积或收件信息。",
        "reply_instruction": REWARD_PROGRAM_PAUSED_REPLY,
    }


def _closed_batch_write_result() -> dict[str, Any]:
    """建单早于本轮开启时刻的服务单：批次已关闭，拒绝一切自助写。

    与 `_paused_write_result` 是两条不同的路径：这里奖励开关是开着的，只是这个
    服务单不参与本轮，所以只拒绝写、不谎报计划暂停。
    """
    return {
        "ok": False,
        "rewards_enabled": True,
        "participating": False,
        "batch_closed": True,
        "error": "本批已结束，不能自助登记发货或继续累积。",
        "reply_instruction": (
            "本服务单所属批次已结束：不要记录收件信息或寄送/累积选择；"
            "不要说继续累积或“攒着”仍可办理，也不要让用户找班主任办理更多累积；"
            "如实告知已获得的奖励，并引导他联系班主任处理这些已得奖励的寄送。"
        ),
    }


async def _public_payload(
    session,
    status: dict[str, Any],
) -> dict[str, Any]:
    program: CheckinRewardProgram | None = status["program"]
    active: CheckinRewardShipment | None = status["active_shipment"]
    qualifications: list[CheckinRewardQualification] = status["qualifications"]
    reward_config: CheckinRewardTierConfig = status["reward_config"]
    unsent: list[CheckinRewardQualification] = status["unsent"]
    shipments: list[CheckinRewardShipment] = status["shipments"]
    shipments_by_id = {row.id: row for row in shipments}
    qualifications_by_tier = {row.tier_days: row for row in qualifications}
    shipped_qualifications = [
        row
        for row in qualifications
        if row.shipment_id in shipments_by_id
        and shipments_by_id[row.shipment_id].status == "shipped"
    ]
    choice = program.current_choice if program is not None else None
    choice_text = {
        "pending_choice": "待选择现在发货或继续累积",
        "ship_now": "选择现在发货",
        "accumulate": "选择继续累积",
    }.get(choice, "尚未选择")
    if active is not None:
        operational_status = active.status
    elif unsent:
        operational_status = "accumulating" if choice == "accumulate" else "waiting_choice"
    elif any(row.status == "shipped" for row in shipments):
        operational_status = "shipped"
    else:
        operational_status = "no_reward"
    operational_status_text = {
        "missing_address": "已选择发货，收件信息未齐",
        "pending_shipment": "已选择发货，等待运营寄出",
        "accumulating": "已选择继续累积",
        "waiting_choice": "已有奖品，等待选择发货或继续累积",
        "shipped": "已发货",
        "no_reward": "尚未获得奖品",
    }[operational_status]
    if not status["participating"]:
        closed_batch_text = "本批已结束，自助登记已关闭，已得奖励需联系班主任"
        choice_text = closed_batch_text
        operational_status_text = closed_batch_text
    reward_tiers = []
    for tier in reward_config.tiers:
        qualification = qualifications_by_tier.get(tier.days)
        shipment = (
            shipments_by_id.get(qualification.shipment_id)
            if qualification is not None and qualification.shipment_id is not None
            else None
        )
        reward_tiers.append(
            {
                "required_complete_days": tier.days,
                "reward": tier.display,
                "earned": qualification is not None,
                "qualified_on": (
                    qualification.qualified_on.isoformat() if qualification is not None else None
                ),
                "shipment_status": shipment.status if shipment is not None else None,
            }
        )
    shipment_history = [
        {
            "shipment_id": shipment.id,
            "status": shipment.status,
            "status_text": {
                "missing_address": "收件信息未齐",
                "pending_shipment": "等待运营寄出",
                "shipped": "已发货",
                "cancelled": "已取消该寄送批次",
            }.get(shipment.status, shipment.status),
            "rewards": _qualification_display(
                [row for row in qualifications if row.shipment_id == shipment.id]
            ),
            "requested_at": shipment.requested_at.isoformat(),
            "shipped_at": shipment.shipped_at.isoformat() if shipment.shipped_at else None,
        }
        for shipment in reversed(shipments)
        if shipment.status != "cancelled"
    ]
    payload = {
        "ok": True,
        "rewards_enabled": True,
        "program_paused": False,
        "participating": status["participating"],
        "program_start_date": (
            status["program_start_date"].isoformat()
            if status["program_start_date"] is not None
            else None
        ),
        "complete_checkin_days": status["complete_checkin_days"],
        "reward_tiers": reward_tiers,
        "has_earned_rewards": bool(qualifications),
        "earned_rewards": _qualification_display(qualifications),
        "shipped_rewards": _qualification_display(shipped_qualifications),
        "unshipped_rewards": _qualification_display(unsent),
        "awaiting_choice": bool(
            program is not None
            and program.current_choice == "pending_choice"
            and unsent
            and active is None
        ),
        "current_choice": choice,
        "current_choice_text": choice_text,
        "choice_tier_days": program.choice_tier_days if program is not None else None,
        "operational_status": operational_status,
        "operational_status_text": operational_status_text,
        "active_shipment": active is not None,
        "shipment_status": active.status if active is not None else None,
        "shipment_rewards": _qualification_display(status["active_items"]),
        "shipment_history": shipment_history,
        "has_shipped_rewards": bool(shipped_qualifications),
        "all_earned_rewards_shipped": bool(qualifications) and not unsent,
        "missing_shipping_fields": status["missing_fields"],
        "has_saved_shipping_info": bool(
            program
            and _clean(program.recipient_name)
            and _clean(program.recipient_phone)
            and _clean(program.shipping_address)
        ),
    }
    if not status["participating"]:
        payload["reply_instruction"] = (
            "本服务单不参与本轮打卡奖励（本轮开启后新建的服务单才参与）；"
            "只告知已获得的奖励，不要承诺或计算新的奖励进度。"
            "若用户想寄送已获得的奖励，不要记录收件人、手机号、地址或任何选择，"
            "让他联系班主任处理寄送。"
            "不要说继续累积仍可办理、也不要说以后还能再攒。"
        )
    return payload


def build_reward_shipping_tools(
    *,
    context: AgentRunContext,
) -> list[BaseTool]:
    async def get_checkin_reward_status() -> dict[str, Any]:
        """Read the current user's complete-checkin and reward/shipping status."""
        async with SessionLocal() as session:
            config = await _load_reward_config(session)
            if config is None or not config.enabled:
                return _paused_reward_payload()
            service_case = await session.scalar(
                select_active_service_cases(ServiceCase.id == context.service_case_id)
            )
            if service_case is None:
                return {"ok": False, "error": "当前服务单不存在或已删除。"}
            return await _public_payload(
                session,
                await _reward_status(
                    session, service_case_id=service_case.id, config=config
                ),
            )

    async def manage_reward_shipping(
        action: RewardShippingAction,
        recipient_name: str | None = None,
        recipient_phone: str | None = None,
        shipping_address: str | None = None,
        reuse_saved_address: bool = False,
    ) -> dict[str, Any]:
        """Read or update the current service user's earned-reward shipping decision."""
        now = datetime.now(timezone.utc)
        async with SessionLocal() as session:
            config = await _load_reward_config(session)
            if config is None or not config.enabled:
                if action == "get_status":
                    return _paused_reward_payload()
                return _paused_write_result()
            service_case = await session.scalar(
                select_active_service_cases(
                    ServiceCase.id == context.service_case_id
                ).with_for_update()
            )
            if service_case is None:
                return {"ok": False, "error": "当前服务单不存在或已删除。"}

            status = await _reward_status(
                session, service_case_id=service_case.id, config=config
            )
            if action == "get_status":
                return await _public_payload(session, status)
            # 关闭批次硬闸门：非读动作一律在此拒绝，未参与本轮的服务单不得自助写入。
            if not status["participating"]:
                return _closed_batch_write_result()

            source = await _current_user_source(session, context)
            if source is None:
                return {
                    "ok": False,
                    "error": "只能根据当前群里这位用户本人的明确回复记录寄送选择或地址。",
                }
            if not status["qualifications"] or not status["unsent"]:
                return {
                    "ok": False,
                    "error": "当前没有已获得且尚未寄出的打卡奖励，不记录这次选择。",
                }
            program: CheckinRewardProgram | None = status["program"]
            if program is None:
                return {
                    "ok": False,
                    "error": "奖励账本尚未就绪，请稍后再试或联系班主任。",
                }

            if action == "accumulate":
                active: CheckinRewardShipment | None = status["active_shipment"]
                if active is not None:
                    for qualification in status["active_items"]:
                        qualification.shipment_id = None
                    active.status = "cancelled"
                program.current_choice = "accumulate"
                program.choice_updated_at = now
                await session.commit()
                return {
                    **await _public_payload(
                        session,
                        await _reward_status(
                            session, service_case_id=service_case.id, config=config
                        ),
                    ),
                    "recorded": "accumulate",
                    "reply_instruction": "已记下继续累积，告诉用户等下一阶段达标后再一起确认寄送。",
                }

            cleaned_name = _clean(recipient_name)
            cleaned_phone = _clean(recipient_phone)
            cleaned_address = _clean(shipping_address)
            supplied_fields = {
                "收件人": cleaned_name,
                "手机号": cleaned_phone,
                "详细地址": cleaned_address,
            }
            if reuse_saved_address and action != "ship_now":
                return {
                    "ok": False,
                    "error": "只有明确申请立即寄出时才能沿用已保存的完整收件信息。",
                }
            if reuse_saved_address and any(supplied_fields.values()):
                return {
                    "ok": False,
                    "error": "沿用已保存地址和本条提供新地址只能二选一；如需换地址，请一次写全收件人、手机号和详细地址。",
                }
            if not reuse_saved_address:
                missing_input = [
                    label for label, value in supplied_fields.items() if value is None
                ]
                if missing_input:
                    return {
                        "ok": False,
                        "missing_shipping_fields": missing_input,
                        "error": (
                            "收件信息必须一次提供完整的收件人、手机号和详细地址；本次缺少："
                            + "、".join(missing_input)
                        ),
                    }
            source_text = _clean(source.content) or ""
            ungrounded = [
                label
                for label, value in (
                    ("收件人", cleaned_name),
                    ("手机号", cleaned_phone),
                    ("详细地址", cleaned_address),
                )
                if value is not None and value not in source_text
            ]
            if ungrounded:
                return {
                    "ok": False,
                    "error": (
                        "这些信息没有原样出现在用户当前消息里，不能代填："
                        + "、".join(ungrounded)
                    ),
                }
            active = status["active_shipment"]
            if action == "save_shipping_info":
                program.recipient_name = cleaned_name
                program.recipient_phone = cleaned_phone
                program.shipping_address = cleaned_address
                if active is not None:
                    active.recipient_name = cleaned_name
                    active.recipient_phone = cleaned_phone
                    active.shipping_address = cleaned_address
                    active.status = "pending_shipment"
                await session.commit()
                refreshed = await _reward_status(
                    session, service_case_id=service_case.id, config=config
                )
                return {
                    **await _public_payload(session, refreshed),
                    "recorded": "save_shipping_info",
                    "reply_instruction": "完整收件信息已保存；未明确要求立即寄出时，不要声称已经申请发货。",
                }

            if reuse_saved_address:
                saved_fields = {
                    "收件人": _clean(program.recipient_name),
                    "手机号": _clean(program.recipient_phone),
                    "详细地址": _clean(program.shipping_address),
                }
                missing_saved = [
                    label for label, value in saved_fields.items() if value is None
                ]
                if missing_saved:
                    return {
                        "ok": False,
                        "missing_shipping_fields": missing_saved,
                        "error": (
                            "当前没有可沿用的完整收件信息；请一次提供收件人、手机号和详细地址。"
                        ),
                    }
                cleaned_name = saved_fields["收件人"]
                cleaned_phone = saved_fields["手机号"]
                cleaned_address = saved_fields["详细地址"]

            if active is None:
                active = CheckinRewardShipment(
                    service_case_id=service_case.id,
                    status="missing_address",
                    requested_at=now,
                )
                session.add(active)
                await session.flush()
                for qualification in status["unsent"]:
                    if qualification.shipment_id is None:
                        qualification.shipment_id = active.id

            active.recipient_name = cleaned_name
            active.recipient_phone = cleaned_phone
            active.shipping_address = cleaned_address
            program.recipient_name = cleaned_name
            program.recipient_phone = cleaned_phone
            program.shipping_address = cleaned_address
            active.status = "pending_shipment"
            program.current_choice = "ship_now"
            program.choice_updated_at = now
            await session.commit()
            refreshed = await _reward_status(
                session, service_case_id=service_case.id, config=config
            )
            return {
                **await _public_payload(session, refreshed),
                "recorded": "ship_now",
                "reply_instruction": "收件信息已齐，告诉用户已记下，等待班主任安排寄出。",
            }

    return [
        StructuredTool.from_function(
            coroutine=manage_reward_shipping,
            name="manage_reward_shipping",
            description=(
                "Record THIS user's cumulative check-in reward shipping decision. Follow exactly one branch for an "
                "immediate-shipment request: (1) call ship_now only when the current message supplies recipient "
                "name, phone, and detailed address together; (2) call ship_now with reuse_saved_address=true only "
                "when the user explicitly asks to reuse previously saved details; (3) otherwise call no reward "
                "tool and ask only for the missing recipient fields. Never call get_status merely because recipient "
                "fields are missing. get_status is reserved for interpreting an otherwise ambiguous bare reply such "
                "as 1 or 2 to a pending choice; explicit reward or shipping status questions use "
                "get_checkin_reward_status. Call accumulate only if this user's current message clearly answers a "
                "pending reward choice. ship_now and save_shipping_info require all three recipient fields together. "
                "The tool never calculates awards, "
                "changes quantities, or marks a shipment as shipped. When the program is paused, do not write "
                "anything and reply only with reply_instruction."
            ),
            args_schema=RewardShippingArgs,
        ),
        StructuredTool.from_function(
            coroutine=get_checkin_reward_status,
            name="get_checkin_reward_status",
            description=(
                "Read THIS user's current configured reward tiers, cumulative complete-check-in days, earned "
                "and unshipped rewards, and shipping status/history. Use the returned values as the source of "
                "truth; never infer check-in totals from earned tiers. If program_paused=true, reply only with "
                "reply_instruction. Read-only."
            ),
        ),
    ]
