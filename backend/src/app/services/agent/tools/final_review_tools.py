from __future__ import annotations

"""结营复盘的两个只读工具：问答进度与当前可发的券。

两个都刻意**只读**。答案入库走档案官的确定性落库（和打卡投影同一条路），
不给助教写库的工具——助教侧的写工具目前只有明确 @ 触发的配置类动作，
让它顺手写业务答案会让"客户到底说了什么"变成模型自述，无从对账。

券的部分只回配置里真有的字段。取不到配置时返回的是"没有可发的券"，
而不是空模板：模型看到空字段会自己填一个像样的面额，那是直接的钱和信任损失。
"""

from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

from app.db.session import SessionLocal
from app.services.agent.state import AgentRunContext
from app.services.day28_benefit_grants import benefits_to_grant
from app.services.day28_benefit_settings import describe_benefit, get_benefit_config
from app.services.final_review_answers import load_progress
from app.services.final_review_scripts import FINAL_REVIEW_QUESTION_IDS


def build_final_review_tools(*, context: AgentRunContext) -> list[BaseTool]:
    async def get_final_review_progress() -> dict[str, Any]:
        """Read which final-review questions this customer has already answered."""
        async with SessionLocal() as session:
            progress = await load_progress(
                session, service_case_id=context.service_case_id
            )
        answered: list[dict[str, Any]] = []
        for question_id in FINAL_REVIEW_QUESTION_IDS:
            row = progress.answers.get(question_id)
            if row is None:
                continue
            answered.append(
                {
                    "question_id": question_id,
                    "status": row.answer_status,
                    "raw_text": row.raw_text,
                    "selected": progress.selected_keys(question_id),
                }
            )
        return {
            "ok": True,
            "answered": answered,
            "unresolved_question_ids": [
                question_id
                for question_id in FINAL_REVIEW_QUESTION_IDS
                if not progress.is_resolved(question_id)
            ],
            "note": (
                "status 为 skipped/refused 的题算问完了，不要再问。"
                "unresolved 里的题也只在调度器点名当前节点时问。"
            ),
        }

    async def get_day28_benefits() -> dict[str, Any]:
        """Read the day-28 benefits this customer may still be told about."""
        async with SessionLocal() as session:
            progress = await load_progress(
                session, service_case_id=context.service_case_id
            )
            try:
                config = await get_benefit_config(session)
            except ValueError as exc:
                # 配置被改坏时不猜、不回落默认：让助教走"班主任稍后安排"。
                return {
                    "ok": False,
                    "benefits": [],
                    "error": f"券配置无法读取（{exc}），不要提券，改说班主任稍后安排。",
                }
            pending = await benefits_to_grant(
                session,
                service_case_id=context.service_case_id,
                config=config,
                cross_product_intent=progress.cross_product_intent,
            )
        if not pending:
            return {
                "ok": True,
                "benefits": [],
                "note": (
                    "当前没有可发的券（未配置、缺领取链接，或这位客户已经拿过）。"
                    "一个字都不要提券，改说班主任稍后安排。"
                ),
            }
        return {
            "ok": True,
            "benefits": [
                {
                    "code": benefit.code,
                    "text": describe_benefit(benefit, language=context.language),
                }
                for benefit in pending
            ],
            "note": "只念 text 原文，不要改面额、有效期或链接，也不要补充没写的条件。",
        }

    return [
        StructuredTool.from_function(
            coroutine=get_final_review_progress,
            name="get_final_review_progress",
            description=(
                "Read the final-review (day 26-30) question progress for THIS service case: "
                "which questions were answered, skipped or refused, and what the customer said. "
                "Call before asking a final-review question so nothing gets asked twice."
            ),
        ),
        StructuredTool.from_function(
            coroutine=get_day28_benefits,
            name="get_day28_benefits",
            description=(
                "Read the day-28 benefits (coupons/gifts) that may still be offered to THIS "
                "customer, already filtered by configuration, eligibility and what was granted "
                "before. An empty list means say nothing about coupons at all. "
                "Never state a face value, validity or claim link that this tool did not return."
            ),
        ),
    ]
