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
)

FOLLOWUP_TOPIC_LABELS: dict[str, str] = {
    "daily_checklist": "昨日五项打卡回顾",
    "weight": "体重",
    "waist": "腰围",
    "milestone_review": "次晨空腹打卡提醒",
    "contradiction": "打卡数据核对",
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
