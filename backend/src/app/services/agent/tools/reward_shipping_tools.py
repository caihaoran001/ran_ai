from __future__ import annotations

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
    REWARD_TIERS,
    _qualification_display,
    valid_reward_checkins,
)
from app.services.service_case_queries import select_active_service_cases


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
            "get_status=read current reward/shipping context; ship_now=user clearly chooses immediate shipment; "
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


async def _reward_status(session, *, service_case_id: int) -> dict[str, Any]:
    service_case = await session.scalar(
        select_active_service_cases(ServiceCase.id == service_case_id)
    )
    if service_case is None:
        raise ValueError("当前服务单不存在或已删除。")
    program = await session.scalar(
        select(CheckinRewardProgram).where(
            CheckinRewardProgram.service_case_id == service_case_id
        )
    )
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
    complete_checkins = await valid_reward_checkins(
        session,
        service_case=service_case,
    )
    return {
        "service_case": service_case,
        "complete_checkin_days": len(complete_checkins),
        "program": program,
        "qualifications": qualifications,
        "shipments": shipments,
        "unsent": unsent,
        "active_shipment": active,
        "active_items": active_items,
        "missing_fields": missing_fields,
    }


def _public_payload(status: dict[str, Any]) -> dict[str, Any]:
    program: CheckinRewardProgram | None = status["program"]
    active: CheckinRewardShipment | None = status["active_shipment"]
    qualifications: list[CheckinRewardQualification] = status["qualifications"]
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
    reward_tiers = []
    for tier in REWARD_TIERS:
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
    return payload


def build_reward_shipping_tools(
    *,
    context: AgentRunContext,
    rewards_enabled: bool = True,
) -> list[BaseTool]:
    async def get_checkin_reward_status() -> dict[str, Any]:
        """Read the current user's complete-checkin and reward/shipping status."""
        if not rewards_enabled:
            return _paused_reward_payload()
        async with SessionLocal() as session:
            service_case = await session.scalar(
                select_active_service_cases(ServiceCase.id == context.service_case_id)
            )
            if service_case is None:
                return {"ok": False, "error": "当前服务单不存在或已删除。"}
            return _public_payload(
                await _reward_status(session, service_case_id=service_case.id)
            )

    async def manage_reward_shipping(
        action: RewardShippingAction,
        recipient_name: str | None = None,
        recipient_phone: str | None = None,
        shipping_address: str | None = None,
        reuse_saved_address: bool = False,
    ) -> dict[str, Any]:
        """Read or update the current service user's earned-reward shipping decision."""
        if not rewards_enabled:
            if action == "get_status":
                return _paused_reward_payload()
            return {
                "ok": False,
                "rewards_enabled": False,
                "program_paused": True,
                "error": "奖励计划已暂停，不记录发货、累积或收件信息。",
                "reply_instruction": REWARD_PROGRAM_PAUSED_REPLY,
            }
        now = datetime.now(timezone.utc)
        async with SessionLocal() as session:
            service_case = await session.scalar(
                select_active_service_cases(
                    ServiceCase.id == context.service_case_id
                ).with_for_update()
            )
            if service_case is None:
                return {"ok": False, "error": "当前服务单不存在或已删除。"}

            status = await _reward_status(session, service_case_id=service_case.id)
            if action == "get_status":
                return _public_payload(status)

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
                    **_public_payload(
                        await _reward_status(session, service_case_id=service_case.id)
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
                    session, service_case_id=service_case.id
                )
                return {
                    **_public_payload(refreshed),
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
            refreshed = await _reward_status(session, service_case_id=service_case.id)
            return {
                **_public_payload(refreshed),
                "recorded": "ship_now",
                "reply_instruction": "收件信息已齐，告诉用户已记下，等待班主任安排寄出。",
            }

    return [
        StructuredTool.from_function(
            coroutine=manage_reward_shipping,
            name="manage_reward_shipping",
            description=(
                "Read and record THIS user's cumulative check-in reward shipping context. All ordinary replies, "
                "including a bare 1 or 2, still go through the Agent: first call get_status when context is unclear. "
                "Call ship_now/accumulate only if this user's current message clearly answers a pending reward "
                "shipping choice. ship_now and save_shipping_info require recipient name, phone, and detailed "
                "address together; ship_now may instead reuse prior data only on an explicit request and only when "
                "all three saved fields exist. The tool never calculates awards, "
                "changes quantities, or marks a shipment as shipped. When the program is paused, do not write "
                "anything and reply only with reply_instruction."
            ),
            args_schema=RewardShippingArgs,
        ),
        StructuredTool.from_function(
            coroutine=get_checkin_reward_status,
            name="get_checkin_reward_status",
            description=(
                "Read THIS user's authoritative cumulative complete-checkin days, all 3/7/14/28-day reward "
                "rules, earned and unshipped rewards, current ship-now/accumulate choice, and actual shipment "
                "history/status. Use this for every customer question about check-in totals or rewards; never "
                "derive an exact total from the highest earned tier. If program_paused=true, disclose no old "
                "reward rules or history and reply only with reply_instruction. This tool is read-only."
            ),
        ),
    ]
