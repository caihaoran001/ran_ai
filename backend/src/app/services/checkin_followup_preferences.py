"""Durable, user-controlled switches for proactive check-in follow-ups."""

from __future__ import annotations

from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.checkin_followup_preference import CheckinFollowupPreference

FOLLOWUP_TOPICS: tuple[str, ...] = (
    "daily_checklist",
    "weight",
    "waist",
    "milestone_review",
    "contradiction",
    # 下面两项来自结营复盘的联系偏好（R6），不只管打卡追问：
    # product_recommendation 关掉后，承接消息不带产品与券；
    # proactive_contact 关掉后，一切主动触达（复盘节点、流失召回、打卡追问）全停。
    # 两项都**不影响风险相关提醒**——客户要的是少被推销，不是出事也没人管。
    "product_recommendation",
    "proactive_contact",
)

FOLLOWUP_TOPIC_LABELS: dict[str, str] = {
    "daily_checklist": "昨日五项打卡回顾",
    "weight": "体重",
    "waist": "腰围",
    "milestone_review": "次晨空腹打卡提醒",
    "contradiction": "打卡数据核对",
    "product_recommendation": "产品与权益推荐",
    "proactive_contact": "全部主动联系",
}


def normalize_followup_topics(topics: Iterable[str]) -> list[str]:
    """Validate and de-duplicate tool input without accepting arbitrary scheduler keys."""
    normalized: list[str] = []
    for raw in topics:
        topic = str(raw or "").strip()
        if topic not in FOLLOWUP_TOPICS:
            raise ValueError(f"不支持的追问主题：{topic or '空值'}")
        if topic not in normalized:
            normalized.append(topic)
    if not normalized:
        raise ValueError("请至少选择一个追问主题。")
    return normalized


async def get_followup_preference_states(
    session: AsyncSession, *, service_case_id: int
) -> dict[str, bool]:
    rows = list(
        await session.scalars(
            select(CheckinFollowupPreference).where(
                CheckinFollowupPreference.service_case_id == service_case_id
            )
        )
    )
    states = {topic: True for topic in FOLLOWUP_TOPICS}
    for row in rows:
        if row.topic in states:
            states[row.topic] = bool(row.enabled)
    return states


async def set_followup_preference_states(
    session: AsyncSession,
    *,
    service_case_id: int,
    topics: Iterable[str],
    enabled: bool,
    source_message_id: int | None,
) -> dict[str, bool]:
    selected = normalize_followup_topics(topics)
    rows = list(
        await session.scalars(
            select(CheckinFollowupPreference).where(
                CheckinFollowupPreference.service_case_id == service_case_id,
                CheckinFollowupPreference.topic.in_(selected),
            )
        )
    )
    by_topic = {row.topic: row for row in rows}
    for topic in selected:
        row = by_topic.get(topic)
        if row is None:
            session.add(
                CheckinFollowupPreference(
                    service_case_id=service_case_id,
                    topic=topic,
                    enabled=enabled,
                    source_message_id=source_message_id,
                )
            )
            continue
        row.enabled = enabled
        row.source_message_id = source_message_id
    await session.flush()
    return await get_followup_preference_states(session, service_case_id=service_case_id)
