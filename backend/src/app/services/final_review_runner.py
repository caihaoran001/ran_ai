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
    ASK_INTENT_CONTINUE,
    ASK_INTENT_PAUSE,
    ASK_OTHER_HEALTH_NEED,
    FINAL_REVIEW_NODES,
    NODE_BENEFIT,
    NODE_GOAL,
    NODE_HANDOFF,
    NODE_LABELS,
    NODE_RESULTS,
    Q_NEXT_GOAL,
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


# 入营那道「最希望 28 天后看到哪些变化」在中英模板里是同一道题、同一个 key，
# 但标题文字不同，而 key 是模板生成的随机串不能写死；两组关键词都试一遍。
_FOCUS_TITLE_PARTS: tuple[tuple[str, ...], ...] = (
    ("最希望", "28"),
    ("hope", "28 days"),
)
# 关注项要嵌进题干念出来，太长会把整句撑散。中文选项最长约 30 字，同一选项的
# 英文写法约 60 字，留一倍余量给自填内容；只用来挡住"把一整段贴进来"。
_FOCUS_ITEM_MAX_CHARS = 120
# 只勾了"其他"却没写内容时它不携带任何信息，念出来是句废话，直接丢掉。
_EMPTY_FOCUS_VALUES = frozenset({"其他", "Other", "other"})


def _focus_answer_values(
    fields: list[dict], answers: dict, title_parts: tuple[str, ...]
) -> list[str]:
    """按题目标题取这道题的答案原文，**不做统计折叠**。

    这里刻意不复用 `intake_facts.structured_answer_values`：那个函数服务于人群
    统计，凡是对不上标准选项的答案（英文问卷存的英文选项、客户在"其他"里自填的
    数字目标）一律归成同一个桶名。2026-09-05 灰度实测时，客户勾的两项因此被压成
    「其他（用户补充）」念进了群。复盘要还给客户的是他当时写下的原话，桶名对他
    毫无意义。统计那边的折叠是对的，不要顺手改。
    """
    for field in fields:
        key = str(field.get("key") or field.get("id") or "")
        title = str(field.get("title") or field.get("label") or "").casefold()
        if key not in answers:
            continue
        if not all(part.casefold() in title for part in title_parts):
            continue
        raw = answers.get(key)
        if isinstance(raw, list):
            values = [str(item).strip() for item in raw]
        else:
            text = str(raw or "").strip()
            # 多选历史上用 `||` 分隔；中文顿号是选项文字的一部分，不能当分隔符。
            values = [item.strip() for item in text.split("||")] if text else []
        picked: list[str] = []
        for value in values:
            if not value or value in _EMPTY_FOCUS_VALUES or value in picked:
                continue
            if len(value) > _FOCUS_ITEM_MAX_CHARS:
                value = value[: _FOCUS_ITEM_MAX_CHARS - 1] + "…"
            picked.append(value)
        return picked
    return []


async def focus_items_for(
    session: AsyncSession, *, service_case: ServiceCase
) -> list[str]:
    """客户入营时最希望 28 天后看到的变化，最多 3 项（R1 的 `{关注项}`）。

    取不到就返回空列表，由渲染侧整句降级；绝不编一个"体重"塞进去。
    """
    from app.models.questionnaire import QuestionnaireSubmission
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
    for title_parts in _FOCUS_TITLE_PARTS:
        values = _focus_answer_values(fields, answers, title_parts)
        if values:
            return values[:3]
    return []


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
    allow_product_recommendation: bool = True,
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
        if (
            not allow_product_recommendation
            and str(question.get("id") or "") == Q_SERVICE_INTENT
        ):
            # 客户说过"暂不接收产品推荐"，就不该再问他想不想继续买。券照发——
            # 那是走完 28 天挣来的权益，不是推荐。
            continue
        if await question_already_sent(
            session, service_case_id=service_case.id, question=question
        ):
            # 助教已经在客户答完那一轮当场问过了（见 followup_prompt_block）。
            # 客户还没答不代表要再问一遍——问过两次比没问更像没在听。
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


# 条件题的触发题：这道题该不该问，取决于客户对触发题的回答，所以只有触发题
# 已经问出去了，它才有可能轮到。
_TRIGGER_QUESTION: dict[str, str] = {
    ASK_OTHER_HEALTH_NEED: Q_NEXT_GOAL,
    ASK_INTENT_CONTINUE: Q_SERVICE_INTENT,
    ASK_INTENT_PAUSE: Q_SERVICE_INTENT,
}

_CONDITION_HINT: dict[str, dict[str, str]] = {
    "zh": {
        ASK_OTHER_HEALTH_NEED: "仅当客户表示想先改善其他健康困扰时问这道",
        ASK_INTENT_CONTINUE: "仅当客户倾向继续原方案、或想看其他产品时问这道",
        ASK_INTENT_PAUSE: "仅当客户倾向先用完现有的、暂不购买或不需要后续时问这道",
    },
    "en": {
        ASK_OTHER_HEALTH_NEED: "Ask only if the customer wants to work on another health concern first",
        ASK_INTENT_CONTINUE: "Ask only if the customer leans towards continuing or exploring other products",
        ASK_INTENT_PAUSE: "Ask only if the customer leans towards finishing stock, not buying for now, or no follow-up",
    },
}

# 题干指纹长度。太短会撞上别的题，太长会被一个改写字毁掉整段匹配。
_FINGERPRINT_CHARS = 20


def question_fingerprint(text: str) -> str:
    """题干里用来判断"这题发出过没"的一段稳定原文。

    占位符（关注项、权益清单）渲染后每位客户都不同，所以指纹只取占位符之外
    最长的那段连续原文。
    """
    import re

    stripped = re.sub(r"「?\{[^}]*\}」?", "\n", str(text or ""))
    segments = [item.strip() for item in stripped.split("\n") if item.strip()]
    if not segments:
        return ""
    return max(segments, key=len)[:_FINGERPRINT_CHARS]


async def question_already_sent(
    session: AsyncSession, *, service_case_id: int, question: dict[str, Any]
) -> bool:
    """这道题的题干在这个群里发出过吗。

    刻意以"发出去的消息"为事实来源，而不是另记一本"已问"账：记了却没发出去
    （生成成功、发送失败）会让客户永远等不到这道题，而账上显示问过了。
    """
    from app.models.message import Message, MessageDirection

    fingerprint = question_fingerprint(question.get("text"))
    if not fingerprint:
        return False
    hit = await session.scalar(
        select(Message.id)
        .where(
            Message.service_case_id == service_case_id,
            Message.direction == MessageDirection.OUTBOUND,
            Message.content.contains(fingerprint),
        )
        .limit(1)
    )
    return hit is not None


async def followup_prompt_block(
    session: AsyncSession,
    *,
    settings: Settings,
    service_case: ServiceCase,
    language: str = "zh",
) -> str:
    """回复提示词里的【结营复盘待问】段；没有该问的题就返回空串。

    为什么要注入而不是让助教自己调工具查：客户回一个"3"就等于选了「先改善其他
    健康困扰」，紧接着那句"想改善哪方面"必须**当场**问出来，隔天再问这话已经凉了。
    而"必须发生"的动作不能建立在"它会想起来去查"之上——2026-09-07 的复盘就是
    这么漏的：那道追问题的条件要等答案入库才成立，可它所属的节点当天就已经关门，
    结果一次也没问出去，还不报错。注入把它的活儿从"想起来+找到题+念对"压成"念对"。

    触发条件（客户这句到底是不是在答上一题）只能由模型判断，所以条件说明一并给它。
    """
    if not final_review_active_for(settings, service_case.id):
        return ""
    script = await get_active_script(session, language=str(language))
    progress = await load_progress(session, service_case_id=service_case.id)
    hints = _CONDITION_HINT.get(language, _CONDITION_HINT["zh"])
    blocks: list[str] = []
    for node in FINAL_REVIEW_NODES:
        for question in script.questions_for(node):
            ask_when = str(question.get("ask_when") or "")
            trigger_id = _TRIGGER_QUESTION.get(ask_when)
            if trigger_id is None:
                # 无条件题由节点自己按天发，不在这里抢跑。
                continue
            question_id = str(question.get("id") or "")
            if progress.is_resolved(question_id):
                continue
            trigger = script.question(trigger_id)
            if trigger is None:
                continue
            if not await question_already_sent(
                session, service_case_id=service_case.id, question=trigger
            ):
                # 触发题还没问出口，这道题无从谈起。
                continue
            if await question_already_sent(
                session, service_case_id=service_case.id, question=question
            ):
                continue
            blocks.append(
                f"- {hints.get(ask_when, '')}：\n"
                + format_question_for_send(question, language=language)
            )
    if not blocks:
        return ""
    if (language or "").strip().lower() == "en":
        return (
            "The customer may be answering a final-review question in this message. "
            "If they did, and the condition below holds, ask the matching question in "
            "this same reply, word for word, options included:\n" + "\n".join(blocks)
        )
    return (
        "客户这条消息可能是在回答结营复盘的题。如果是，且下面某道题的条件成立，"
        "就在本轮回复里把那道题一字不改地问出来（连编号选项一起）：\n"
        + "\n".join(blocks)
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
