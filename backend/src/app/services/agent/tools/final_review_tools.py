from __future__ import annotations

"""结营复盘的两个只读工具：问答进度与当前可发的券。

两个都刻意**只读**。答案入库走档案官的确定性落库（和打卡投影同一条路），
不给助教写库的工具——助教侧的写工具目前只有明确 @ 触发的配置类动作，
让它顺手写业务答案会让"客户到底说了什么"变成模型自述，无从对账。

券的部分只回配置里真有的字段。取不到配置时返回的是"没有可发的券"，
而不是空模板：模型看到空字段会自己填一个像样的面额，那是直接的钱和信任损失。

**券的领取入口一律不从这里出去。** 这个工具每一轮普通聊天都挂在助教手上，
而技能文件里那些纪律只在它主动读技能时才看得见——能力常驻、纪律按需读。
2026-09-07 灰度实测就是这么坏的：客户刚说想看日常产品，助教顺手查了券、
把入口提前两天念进群，账上没记这次发放（第29天的权益消息还会再发一次），
链接开头的井号也被抄丢了。入口只由权益节点发出，那条路径有台账占位和
链接原文校验。
"""

from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

from app.db.session import SessionLocal
from app.services.agent.state import AgentRunContext
from app.services.day28_benefit_grants import benefits_to_grant, granted_codes
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
        """Read the day-28 benefit rules, for answering questions about them."""
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
            sent_codes = await granted_codes(
                session, service_case_id=context.service_case_id
            )
        # 领取入口一律不出现在这里，`describe_benefit` 的注释写了为什么。
        already_sent = [
            {
                "code": code,
                "text": describe_benefit(
                    benefit, language=context.language, include_claim_url=False
                ),
            }
            for code in sorted(sent_codes)
            if (benefit := config.by_code(code)) is not None
        ]
        upcoming = [
            {
                "code": benefit.code,
                "text": describe_benefit(
                    benefit, language=context.language, include_claim_url=False
                ),
            }
            for benefit in pending
        ]
        if not upcoming and not already_sent:
            return {
                "ok": True,
                "benefits": [],
                "already_sent": [],
                "note": (
                    "当前没有券可谈（未配置或缺领取链接）。"
                    "一个字都不要提券，改说班主任稍后安排。"
                ),
            }
        return {
            "ok": True,
            # 还没发出去的券：只能用来答规则，不能主动告知、不能说"这是给你的"。
            "benefits": upcoming,
            # 已经在权益消息里发出去的券：客户追问规则时照 text 回答。
            "already_sent": already_sent,
            "note": (
                "这里没有领取入口，你也不许给出任何领取链接或口令——"
                "入口只由第28天报告之后的权益消息统一发出，那条消息会记账。"
                "benefits 里的券还没告知，只能在客户主动问起时回答规则，"
                "不要主动提、不要说「这是给你的券」、不要暗示他现在能领。"
                "already_sent 里的券已经发过入口，客户说没看到就让他往上翻，"
                "或请班主任补发，不要自己贴链接。"
                "面额、有效期、适用范围、门槛只照 text 原文说，不补充没写的条件。"
            ),
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
                "Read the day-28 benefit RULES (face value, validity, scope, threshold) so you "
                "can answer questions about them. It returns NO claim link and you must never "
                "produce one: claim links are sent only by the scheduled day-28 benefit message, "
                "which records the grant. An empty result means say nothing about coupons at all. "
                "Never state a face value, validity or condition this tool did not return."
            ),
        ),
    ]
