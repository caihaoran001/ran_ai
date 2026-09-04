from __future__ import annotations

"""结营复盘的到点触发：第 26 天起按节点唤起助教开口。

三条设计边界，改这里之前先读：

1. **只在白名单服务单上跑。** 开关打开、白名单为空 = 一单都不跑。这条链路会向真实
   客户群发消息并发出券的领取入口，灰度不是可选项。
2. **一天一个节点。** 功能中途开启时，第 26-28 天的节点可能同时待发；一次全发出去
   等于当天连问四轮。所以每个服务单每天只推进一个节点。
3. **券的链接由确定性代码兜底。** 助教负责说人话，但每一张已占名额的券，其领取
   链接必须原样出现在发出去的文本里。模型漏了或改了就由代码补一段——面额和链接
   写错是直接的钱和信任损失，不能只靠提示词约束。
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.day28_benefit_grant import Day28BenefitGrant
from app.models.reminder import ReminderEvent
from app.models.service_case import ServiceCase
from app.services.day28_benefit_grants import benefits_to_grant, reserve_grant
from app.services.day28_benefit_settings import (
    Day28Benefit,
    describe_benefits,
    get_benefit_config,
)
from app.services.final_review_answers import (
    FinalReviewProgress,
    load_progress,
    pending_questions,
)
from app.services.final_review_scripts import (
    FINAL_REVIEW_NODES,
    NODE_BENEFIT,
    NODE_GOAL,
    NODE_HANDOFF,
    NODE_LABELS,
    NODE_RESULTS,
    Q_SERVICE_INTENT,
    FinalReviewScript,
    format_question_for_send,
    get_active_script,
)

logger = logging.getLogger(__name__)

# 各节点最早可发的服务天数。benefit 定在第 29 天是因为第 28 天当天要发阶段报告，
# 同一天再发权益与追问会把报告淹掉。
NODE_EARLIEST_DAY: dict[str, int] = {
    NODE_RESULTS: 26,
    NODE_GOAL: 27,
    NODE_BENEFIT: 29,
    NODE_HANDOFF: 30,
}

# 整轮复盘的最后一天。过了就不再追——客户已经离开服务语境，迟到的问卷只是打扰。
FINAL_REVIEW_LAST_DAY = 35

# 统一时间窗；窗口内的具体时刻按 (服务单, 日期, 节点) 稳定散列错峰。
FINAL_REVIEW_WINDOW: tuple[time, time] = (time(10, 0), time(11, 0))

# 这几天让位给结营复盘，不再发每日五项清单追问——一天两问会让客户只回一个。
CHECKLIST_QUIET_DAYS = frozenset({26, 27, 28, 29})

# 连续这么多个已发出的节点没有任何回答就整轮停止。
SILENT_NODES_BEFORE_PAUSE = 2

_RULE_PREFIX = "final_review"


def rule_id_for(node: str) -> str:
    return f"{_RULE_PREFIX}:{node}"


def final_review_case_ids(settings: Settings) -> set[int]:
    from app.services.reminder_service import _parse_int_list

    return set(_parse_int_list(settings.final_review_case_ids or ""))


def final_review_active_for(settings: Settings, service_case_id: int) -> bool:
    """这个服务单现在要不要跑结营复盘。

    白名单为空时返回 False——"开关开了但没填白名单"必须是"一单都不跑"，
    而不是"全量放开"，否则一次误配就是给所有在服客户群发券。
    """
    if not settings.final_review_enabled:
        return False
    return service_case_id in final_review_case_ids(settings)


def node_question_ids(script: FinalReviewScript, node: str) -> list[str]:
    return [
        str(question.get("id") or "")
        for question in script.questions_for(node)
        if str(question.get("id") or "")
    ]


def trailing_silent_nodes(
    script: FinalReviewScript,
    *,
    asked_nodes: list[str],
    progress: FinalReviewProgress,
) -> int:
    """已发出的节点里，从最近往前数有几个一个字都没回。"""
    silent = 0
    for node in reversed([node for node in FINAL_REVIEW_NODES if node in asked_nodes]):
        if any(
            question_id in progress.answers
            for question_id in node_question_ids(script, node)
        ):
            break
        silent += 1
    return silent


def select_node(
    script: FinalReviewScript,
    *,
    day: int,
    asked_nodes: list[str],
    progress: FinalReviewProgress,
) -> str | None:
    """今天该推进哪个节点；没有就返回 None。

    按顺序取第一个"到期、没发过、且还有题要问"的节点，一天只推一个。
    """
    if day < NODE_EARLIEST_DAY[NODE_RESULTS] or day > FINAL_REVIEW_LAST_DAY:
        return None
    if trailing_silent_nodes(
        script, asked_nodes=asked_nodes, progress=progress
    ) >= SILENT_NODES_BEFORE_PAUSE:
        return None
    for node in FINAL_REVIEW_NODES:
        if node in asked_nodes or day < NODE_EARLIEST_DAY[node]:
            continue
        if node == NODE_HANDOFF and not progress.is_resolved(Q_SERVICE_INTENT):
            # 承接必须建立在他表达过的意愿上；没答就等下一天，不替他假设。
            continue
        if not pending_questions(script.questions_for(node), progress):
            # 整个节点的题都已问完（例如客户提前在别的节点一并答了），跳过并记账。
            continue
        return node
    return None


async def asked_nodes_for(
    session: AsyncSession, *, service_case_id: int
) -> list[str]:
    rows = await session.scalars(
        select(ReminderEvent.rule_id).where(
            ReminderEvent.service_case_id == service_case_id,
            ReminderEvent.rule_id.startswith(f"{_RULE_PREFIX}:"),
        )
    )
    return [str(rule_id).split(":", 1)[1] for rule_id in rows.all()]


async def focus_items_for(
    session: AsyncSession, *, service_case: ServiceCase
) -> list[str]:
    """客户入营时最希望 28 天后看到的变化，最多 3 项（R1 的 `{关注项}`）。

    取不到就返回空列表，由渲染侧整句降级；绝不编一个"体重"塞进去。
    """
    from app.models.questionnaire import QuestionnaireSubmission
    from app.services.intake_facts import structured_answer_values
    from app.services.questionnaire_template import get_active_questionnaire_fields

    submission = await session.scalar(
        select(QuestionnaireSubmission)
        .where(QuestionnaireSubmission.service_case_id == service_case.id)
        .order_by(QuestionnaireSubmission.id.desc())
        .limit(1)
    )
    answers = getattr(submission, "answers_json", None)
    if not isinstance(answers, dict):
        return []
    language = getattr(service_case, "language", None)
    fields = await get_active_questionnaire_fields(
        session, "intake", language=language or "zh"
    )
    values = structured_answer_values(fields, answers, "最希望 28 天后")
    return [value for value in values if value][:3]


@dataclass
class NodePlan:
    """一个节点到点后要发的东西。"""

    node: str
    text: str
    reserved_benefits: list[Day28Benefit]
    # 已占名额的台账行本身：发送失败时调用方要把它们连同天闸一起撤掉，
    # 否则会留下"账上已发出、客户手里没有、而且明天不再重试"的死账。
    reserved_grants: list[Day28BenefitGrant]
    benefits_text: str


async def plan_node(
    session: AsyncSession,
    *,
    service_case: ServiceCase,
    script: FinalReviewScript,
    node: str,
    progress: FinalReviewProgress,
) -> NodePlan | None:
    """把节点渲染成待发内容，并为权益节点先占下发放名额。

    先占名额、后发消息是刻意的：反过来会在"发送成功但记账前崩溃"时重复发券。
    发送失败由调用方回滚，名额随之释放。
    """
    language = getattr(service_case, "language", None) or "zh"
    focus_items = await focus_items_for(session, service_case=service_case)
    reserved: list[Day28Benefit] = []
    reserved_grants: list[Day28BenefitGrant] = []
    benefits_text = ""
    if node == NODE_BENEFIT:
        try:
            config = await get_benefit_config(session)
        except ValueError as exc:
            # 券配置被改坏：不猜、不发半句券，整个节点这轮不发，等运营修好。
            logger.warning(
                "Final review benefit node skipped, coupon config is invalid case_id=%s: %s",
                service_case.id,
                exc,
            )
            return None
        candidates = await benefits_to_grant(
            session,
            service_case_id=service_case.id,
            config=config,
            cross_product_intent=progress.cross_product_intent,
        )
        for benefit in candidates:
            grant = await reserve_grant(
                session, service_case_id=service_case.id, benefit_code=benefit.code
            )
            if grant is not None:
                reserved.append(benefit)
                reserved_grants.append(grant)
        benefits_text = describe_benefits(reserved, language=language)

    lines: list[str] = []
    for question in pending_questions(script.questions_for(node), progress):
        if not benefits_text and _needs_benefits(question):
            # 没有可发的券就不念权益句：与"改说班主任稍后安排"的口径一致。
            continue
        lines.append(
            format_question_for_send(
                question,
                focus_items=focus_items,
                benefits_text=benefits_text,
                language=language,
            )
        )
    if not lines:
        return None
    return NodePlan(
        node=node,
        text="\n\n".join(lines),
        reserved_benefits=reserved,
        reserved_grants=reserved_grants,
        benefits_text=benefits_text,
    )


def _needs_benefits(question: dict[str, Any]) -> bool:
    from app.services.final_review_scripts import BENEFIT_PLACEHOLDERS

    text = str(question.get("text") or "")
    return any(placeholder in text for placeholder in BENEFIT_PLACEHOLDERS)


def build_node_instruction(
    plan: NodePlan, *, language: str = "zh", allow_product_recommendation: bool = True
) -> str:
    """给助教的唤起指令：办哪件事、逐字念什么。"""
    label = NODE_LABELS.get(plan.node, plan.node)
    no_product = "" if allow_product_recommendation else (
        "\nThis customer asked not to receive product recommendations: do not mention any "
        "product or coupon in your own words; close with health guidance only."
        if language == "en"
        else "\n这位客户说过暂不接收产品推荐：不要自己提任何产品或券，只做健康收尾。"
    )
    if language == "en":
        return (
            "[Final review task] Current node: "
            f"{plan.node} — {label}\n"
            "First call skill(\"final_review\") and follow that skill exactly.\n"
            "Say the following to the customer word for word — do not reword the questions, "
            "do not add or drop options, do not merge in questions from another node. "
            "You may add a short natural lead-in and closing.\n"
            "---\n"
            f"{plan.text}\n"
            "---\n"
            "Call get_final_review_progress() first so nothing already answered gets asked again."
            + no_product
        )
    return (
        f"【结营复盘任务】当前节点：{plan.node} — {label}\n"
        "先调用 skill(\"final_review\") 读完这项技能，严格照它办。\n"
        "下面这些内容要逐字念给客户，题干不许改写、选项不许增删、不许把别的节点的题提前问。"
        "过渡语和收尾可以自己说，自然一点。\n"
        "---\n"
        f"{plan.text}\n"
        "---\n"
        "开口前先调 get_final_review_progress()，已经答过的题不要再问。" + no_product
    )


def ensure_benefit_links(text: str, plan: NodePlan, *, language: str = "zh") -> str:
    """兜底：已占名额的券，其领取链接必须原样在发出去的文本里。

    模型漏掉或改写了链接，客户就领不到已经记在账上的券——账上写着已发出、
    客户手里什么都没有，事后无从对账。所以这里不改模型的措辞，只在缺链接时
    把权益清单原文补在末尾。
    """
    missing = [
        benefit
        for benefit in plan.reserved_benefits
        if benefit.claim_url and benefit.claim_url not in text
    ]
    if not missing:
        return text
    logger.warning(
        "Final review reply dropped %d benefit link(s); appending them verbatim",
        len(missing),
    )
    header = "Your benefits:" if language == "en" else "你的权益："
    return "\n".join([text.rstrip(), "", header, describe_benefits(missing, language=language)])


def should_skip_daily_checklist(settings: Settings, *, service_case_id: int, day: int) -> bool:
    """结营复盘期间让位每日五项清单追问。"""
    return (
        final_review_active_for(settings, service_case_id)
        and day in CHECKLIST_QUIET_DAYS
    )


async def load_case_state(
    session: AsyncSession, *, service_case: ServiceCase
) -> tuple[FinalReviewScript, FinalReviewProgress, list[str]]:
    language = getattr(service_case, "language", None) or "zh"
    script = await get_active_script(session, language=str(language))
    progress = await load_progress(session, service_case_id=service_case.id)
    asked = await asked_nodes_for(session, service_case_id=service_case.id)
    return script, progress, asked


def deterministic_send_time(service_case_id: int, day: date, node: str) -> time:
    from app.services.reminder_service import _deterministic_followup_time

    start, end = FINAL_REVIEW_WINDOW
    return _deterministic_followup_time(service_case_id, day, node, start, end)


def within_send_window(now: datetime, *, service_case_id: int, node: str) -> bool:
    start, end = FINAL_REVIEW_WINDOW
    if not (start <= now.time() < end):
        return False
    return now.time() >= deterministic_send_time(service_case_id, now.date(), node)


async def answers_for_report(
    session: AsyncSession, *, service_case: ServiceCase
) -> list[dict[str, Any]]:
    """已收到的结营复盘回答，整理成第28天报告能读的形式。

    只给已经问过并有记录的题：报告要按客户"已经明确表达过"的方向写下一阶段，
    没表达过必须写待确认，所以这里绝不补全、不推断、不给默认值。
    """
    script = await get_active_script(
        session,
        language=str(
            getattr(
                getattr(service_case, "language", "zh") or "zh", "value", None
            )
            or getattr(service_case, "language", "zh")
            or "zh"
        ),
    )
    progress = await load_progress(session, service_case_id=service_case.id)
    rows: list[dict[str, Any]] = []
    for question_id, answer in progress.answers.items():
        question = script.question(question_id)
        if question is None:
            continue
        selected = [
            script.option_text(question_id, key, default=key)
            for key in progress.selected_keys(question_id)
        ]
        rows.append(
            {
                "question_id": question_id,
                "question": str(question.get("text") or ""),
                "status": answer.answer_status,
                "selected": selected,
                "raw_text": answer.raw_text or "",
            }
        )
    return rows
