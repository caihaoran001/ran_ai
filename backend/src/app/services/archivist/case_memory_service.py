"""档案官 case_memory：为每个服务单维护自由文本滚动小结。

定位:**记忆产物**,不是权威事实源——供 Chat Agent 了解用户背景(重要经历/阶段转折)。
因此对归档主流程是**尽力而为**:由 trigger 在归档事务成功提交后、用独立 session 调用;
本模块所有异常内部吞掉只记日志,绝不回滚归档水位线。

- summary:仅当本轮**有新沉淀**(打卡/成效/画像/纠正落库)时,才追加一次轻量 LLM 调用刷新;
  纯闲聊轮不刷,控制成本。摘要文本 LLM 产出后原样落 archivist_case_memory,不写领域事实表。
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.db.session import SessionLocal
from app.models.archivist_case_memory import ArchivistCaseMemory
from app.models.message import Message, MessagePurpose, MessageSenderType

logger = logging.getLogger(__name__)

_SUMMARY_MAX_CHARS = 800
_CONVO_MSG_LIMIT = 25
_CONVO_MSG_CHARS = 200

_SUMMARY_SYSTEM = (
    "你是燃人AI档案官的记忆整理器(不是聊天回复)。请把这位用户在服务过程中的**重要经历、"
    "阶段转折、当前状态**浓缩成一段简洁的中文滚动小结(不超过 200 字),供聊天助教快速了解背景。\n"
    "【纪律】只依据给定材料,不编造、不给医疗建议、不写疗效承诺;若已有小结,请在其基础上融合"
    "新信息、保留仍然成立的要点、覆盖已过时的内容;只输出小结正文,不要解释、不要列表标题。"
)


def _had_precipitation(run_result: Any) -> bool:
    """本轮是否有新沉淀落库(打卡填充/成效/画像/纠正),决定要不要刷新 summary。"""
    if getattr(run_result, "outcome_written", 0):
        return True
    if getattr(run_result, "profile_written", 0):
        return True
    if getattr(run_result, "personal_corrections_written", 0):
        return True
    if getattr(run_result, "qa_drafts_created", 0):
        return True
    upserts = getattr(run_result, "upserts", None) or []
    return any(isinstance(u, dict) and u.get("filled") for u in upserts)


def _snippet(text: str, limit: int) -> str:
    cleaned = " ".join((text or "").split())
    return cleaned[:limit]


def _extract_text(response: Any) -> str:
    content = getattr(response, "content", None)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(str(item) for item in content).strip()
    return ""


async def refresh_case_memory(
    *,
    service_case_id: int,
    run_result: Any,
    watermark_to: int | None,
    chat_model: object | None = None,
    settings: Settings | None = None,
) -> None:
    """归档成功后刷新该单的 case_memory 滚动小结(尽力而为,独立事务)。

    summary 仅在本轮有新沉淀时经 LLM 刷新;纯闲聊轮直接跳过(不建空行、不烧 LLM)。
    任何异常只记日志,不向上抛(不影响已提交的归档)。
    """
    if not _had_precipitation(run_result):
        return
    try:
        settings = settings or get_settings()
        async with SessionLocal() as session:
            memory = await session.scalar(
                select(ArchivistCaseMemory).where(
                    ArchivistCaseMemory.service_case_id == service_case_id
                )
            )
            if memory is None:
                memory = ArchivistCaseMemory(service_case_id=service_case_id)
                session.add(memory)

            new_summary = await _generate_summary(
                session,
                service_case_id=service_case_id,
                watermark_to=watermark_to,
                existing_summary=memory.summary,
                chat_model=chat_model,
                settings=settings,
            )
            if new_summary:
                memory.summary = new_summary[:_SUMMARY_MAX_CHARS]
                memory.summary_watermark_message_id = watermark_to

            await session.commit()
    except Exception:
        logger.exception(
            "case_memory refresh failed service_case_id=%s", service_case_id
        )


async def _generate_summary(
    session,
    *,
    service_case_id: int,
    watermark_to: int | None,
    existing_summary: str | None,
    chat_model: object | None,
    settings: Settings,
) -> str:
    """用最近对话摘录 + 已有小结,让 LLM 产出更新后的滚动小结;失败返回空串。"""
    convo = await _recent_conversation(session, service_case_id, watermark_to)
    if not convo.strip():
        return ""

    parts: list[str] = []
    if existing_summary:
        parts.append(f"已有小结：\n{existing_summary}")
    parts.append(f"近期对话摘录：\n{convo}")
    human = "\n\n".join(parts) + "\n\n请据此输出更新后的滚动小结。"

    try:
        model = chat_model
        if model is None:
            from app.services.agent.model import create_agent_chat_model

            model = create_agent_chat_model(settings)
        response = await model.ainvoke(
            [SystemMessage(content=_SUMMARY_SYSTEM), HumanMessage(content=human)]
        )
        return _extract_text(response)
    except Exception:
        logger.exception(
            "case_memory summary generation failed service_case_id=%s", service_case_id
        )
        return ""


async def _recent_conversation(
    session, service_case_id: int, watermark_to: int | None
) -> str:
    conditions = [
        Message.service_case_id == service_case_id,
        Message.sender_type.in_([MessageSenderType.USER, MessageSenderType.BOT]),
        Message.purpose == MessagePurpose.CHAT,
        Message.content != "",
    ]
    if watermark_to:
        conditions.append(Message.id <= watermark_to)
    rows = (
        await session.execute(
            select(Message)
            .where(*conditions)
            .order_by(Message.id.desc())
            .limit(_CONVO_MSG_LIMIT)
        )
    ).scalars().all()
    role = {MessageSenderType.USER: "用户", MessageSenderType.BOT: "助教"}
    lines = [
        f"{role.get(m.sender_type, '')}：{_snippet(m.content or '', _CONVO_MSG_CHARS)}"
        for m in reversed(rows)  # 恢复时间正序
    ]
    return "\n".join(line for line in lines if line.strip("："))
