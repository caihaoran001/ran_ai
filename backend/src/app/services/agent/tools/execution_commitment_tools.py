from __future__ import annotations

from typing import Any, Literal

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.message import Message, MessageDirection, MessageSenderType
from app.models.service_case import ServiceCase
from app.services.agent.state import AgentRunContext
from app.services.execution_commitment import apply_commitment_choice, effective_target
from app.services.service_case_queries import select_active_service_cases


class ConfirmExecutionCommitmentArgs(BaseModel):
    choice: Literal["high_commitment", "standard", "current_pace"] = Field(
        description="用户明确选择：高承诺档、标准档或先按当前节奏。"
    )


def build_execution_commitment_tools(*, context: AgentRunContext) -> list[BaseTool]:
    async def confirm_execution_commitment(
        choice: Literal["high_commitment", "standard", "current_pace"],
    ) -> dict[str, Any]:
        if context.trigger_id is None:
            return {"ok": False, "error": "当前没有可审计的用户回复，不能确认执行档位。"}
        async with SessionLocal() as session:
            source = await session.scalar(
                select(Message).where(
                    Message.id == context.trigger_id,
                    Message.service_case_id == context.service_case_id,
                    Message.direction == MessageDirection.INBOUND,
                    Message.sender_type == MessageSenderType.USER,
                )
            )
            if source is None:
                return {"ok": False, "error": "只能依据当前服务用户本人的明确回复确认。"}
            service_case = await session.scalar(
                select_active_service_cases(
                    ServiceCase.id == context.service_case_id
                ).with_for_update()
            )
            if service_case is None or not isinstance(
                service_case.execution_commitment, dict
            ):
                return {"ok": False, "error": "当前还没有可确认的执行约定。"}
            state = dict(service_case.execution_commitment)
            invitation = state.get("invitation")
            if not isinstance(invitation, dict) or invitation.get("status") != "sent":
                return {"ok": False, "error": "执行邀请尚未成功发出，暂不能确认。"}
            meta = (
                source.analysis_json.get("_qiwe_meta", {})
                if isinstance(source.analysis_json, dict)
                else {}
            )
            try:
                updated = apply_commitment_choice(
                    state,
                    choice=choice,
                    source_message_id=source.id,
                    source_text=source.content or "",
                    occurred_at=source.created_at,
                    sender_id=str(meta.get("sender_id") or "") or None,
                    sender_name=str(meta.get("sender_name") or "") or None,
                )
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            service_case.execution_commitment = updated
            await session.commit()
        return {
            "ok": True,
            "choice": choice,
            "execution_path": updated.get("execution_path"),
            "status": updated.get("status"),
            "effective_target": effective_target(updated),
        }

    return [
        StructuredTool.from_function(
            coroutine=confirm_execution_commitment,
            name="confirm_execution_commitment",
            description=(
                "Save THIS user's explicit reply to the execution invitation. Call only when the current message "
                "clearly chooses high commitment, standard, or current pace. Ambiguous replies such as 好的/可以 "
                "must be clarified first and must not call this tool."
            ),
            args_schema=ConfirmExecutionCommitmentArgs,
        )
    ]
