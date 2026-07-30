from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import Settings
from app.db.session import SessionLocal
from app.services.agent.state import AgentRunContext
from app.services.checkin_recording import (
    CheckinRecordValidationError,
    record_checkin_from_message,
)


class ProductSchedulePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    morning_c: str | None = Field(
        default=None, description="早C的明确完成情况；保留用户表达，不确定则留空。"
    )
    noon_b: str | None = Field(
        default=None, description="午B的明确完成情况；保留用户表达，不确定则留空。"
    )
    night_a: str | None = Field(
        default=None, description="晚A的明确完成情况；保留用户表达，不确定则留空。"
    )
    summary: str | None = Field(
        default=None, description="用户对产品节律的明确汇总原话；没有则留空。"
    )


class ChecklistAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(
        description="必须逐字使用【当前五项打卡】里给出的稳定项目 ID。"
    )
    answer: str = Field(
        min_length=1, description="本条消息对该项目给出的明确答案，保留原话口径。"
    )
    evidence_type: Literal["text", "image", "mixed"] = Field(
        default="text",
        description="答案证据来自本轮文字、图片描述，或两者结合。",
    )


class RecordCheckinArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_date: str = Field(
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description=(
            "事实所属的北京时间自然日 YYYY-MM-DD。无日期表达时用当前北京时间日期；"
            "明确说昨天/某日时换算到对应日期；日期畸形或有歧义时不要调用工具。"
        ),
    )
    product_schedule: ProductSchedulePatch | None = None
    water: str | None = Field(default=None, description="明确上报的喝水量。")
    sleep: str | None = Field(default=None, description="明确上报的睡眠时长或作息。")
    mood: str | None = Field(default=None, description="明确上报的情绪或状态。")
    exercise: str | None = Field(default=None, description="明确上报的运动。")
    weight: str | None = Field(default=None, description="明确上报的体重，保留原单位。")
    waist: str | None = Field(default=None, description="明确上报的腰围或围度。")
    diet: str | None = Field(default=None, description="明确上报的饮食，含可确认的餐食图片事实。")
    bowel: str | None = Field(default=None, description="明确上报的排便情况。")
    hunger: str | None = Field(default=None, description="明确上报的饥饿感。")
    sensation: str | None = Field(default=None, description="明确上报的身体感受。")
    discomfort: str | None = Field(default=None, description="明确上报的不适。")
    tomorrow_focus: str | None = Field(default=None, description="用户明确说的明日重点。")
    checklist_answers: list[ChecklistAnswer] | None = Field(
        default=None,
        description=(
            "本轮明确回答的当前五项，可只填一项，也可多项；不得补齐用户没说的项目。"
        ),
    )


def _dump_nested(value: Any) -> Any:
    return value.model_dump(exclude_none=True) if isinstance(value, BaseModel) else value


def build_checkin_recording_tools(
    *, context: AgentRunContext, settings: Settings
) -> list[BaseTool]:
    async def record_checkin(
        target_date: str,
        product_schedule: ProductSchedulePatch | dict[str, str] | None = None,
        water: str | None = None,
        sleep: str | None = None,
        mood: str | None = None,
        exercise: str | None = None,
        weight: str | None = None,
        waist: str | None = None,
        diet: str | None = None,
        bowel: str | None = None,
        hunger: str | None = None,
        sensation: str | None = None,
        discomfort: str | None = None,
        tomorrow_focus: str | None = None,
        checklist_answers: list[ChecklistAnswer | dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        if context.trigger_id is None:
            return {"ok": False, "error": "当前没有可绑定的用户来源消息，不能记录打卡。"}
        try:
            parsed_date = date.fromisoformat(target_date)
        except ValueError:
            return {"ok": False, "error": "target_date 不是有效的自然日，请先向用户确认日期。"}
        now = datetime.now(ZoneInfo(settings.reminder_timezone))
        if parsed_date > now.date():
            return {"ok": False, "error": "不能把尚未发生的未来日期记录为已完成打卡。"}

        fields = {
            "product_schedule": _dump_nested(product_schedule),
            "water": water,
            "sleep": sleep,
            "mood": mood,
            "exercise": exercise,
            "weight": weight,
            "waist": waist,
            "diet": diet,
            "bowel": bowel,
            "hunger": hunger,
            "sensation": sensation,
            "discomfort": discomfort,
            "tomorrow_focus": tomorrow_focus,
        }
        answers = [_dump_nested(item) for item in (checklist_answers or [])]
        try:
            async with SessionLocal() as session:
                result = await record_checkin_from_message(
                    session,
                    service_case_id=context.service_case_id,
                    source_message_id=context.trigger_id,
                    target_date=parsed_date,
                    fields=fields,
                    checklist_answers=answers,
                    channel=context.channel,
                    group_id=context.group_id,
                )
                await session.commit()
            return result
        except CheckinRecordValidationError as exc:
            return {"ok": False, "error": str(exc)}

    return [
        StructuredTool.from_function(
            coroutine=record_checkin,
            name="record_checkin",
            description=(
                "Persist check-in facts explicitly provided by the current service user in THIS inbound turn. "
                "Use for sparse single-item or multi-item text/image check-ins, including explicit backfill dates. "
                "Runtime binds the case, group, source message, channel, and active checklist; never ask for or pass "
                "those IDs. Submit only facts supported by the current message/image description, and use exact "
                "item_id values from the injected current checklist. This is the only successful-recording path: "
                "do not tell the user a check-in was recorded until this tool returns ok=true."
            ),
            args_schema=RecordCheckinArgs,
        )
    ]
