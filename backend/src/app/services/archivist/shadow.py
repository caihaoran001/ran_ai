"""成效/画像收编的影子对账（P0.5，只观察不切换）。

档案官批量抽出的 outcome events 与热路径（engine._run_outcome_analysis 逐条写入）
已落的 outcome_events 做事件级 diff，落 archivist_shadow_outcomes 表 + 结构化日志，
供 SQL 统计一致率。**不写** outcome_events/user_state。

对账粒度：按 (field_key, event_type) 集合比对同一批 message_id 的热/影结果——只回答
"这批增量里，批量抽取和逐条抽取是否surface出了同一类成效信号"，不追究 message_id 级归属，
也不复刻 milestone/followup/snapshot（apply_* 是共享确定性代码，真正切换时复用即可）。

画像 profile_signals 合并进 user_state JSONB、无逐条审计行，本轮只**记录**不严格 diff。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.archivist_shadow_outcome import ArchivistShadowOutcome
from app.models.outcome import OutcomeEvent

logger = logging.getLogger(__name__)


def _key(field_key: object, event_type: object) -> tuple[str, str]:
    return (str(field_key or "").strip(), str(event_type or "").strip())


async def reconcile_outcome(
    session: AsyncSession,
    *,
    service_case_id: int,
    delta_message_ids: list[int],
    shadow_events: list[dict[str, Any]],
    shadow_signals: list[dict[str, Any]],
    watermark_from: int | None,
    watermark_to: int | None,
    now: datetime | None = None,
) -> ArchivistShadowOutcome | None:
    """对账并落影子表。返回落库的行（无增量则返回 None，不建行）。不 commit（由调用方持锁事务提交）。"""
    if not delta_message_ids:
        return None
    now = now or datetime.now(timezone.utc)

    # 热路径已写的 outcome_events（同一批 message_id）。
    hot_rows = (
        await session.execute(
            select(OutcomeEvent).where(
                OutcomeEvent.service_case_id == service_case_id,
                OutcomeEvent.message_id.in_(delta_message_ids),
            )
        )
    ).scalars().all()
    hot_events = [
        {
            "field_key": r.field_key,
            "event_type": r.event_type,
            "value": r.value_text,
            "message_id": r.message_id,
        }
        for r in hot_rows
    ]

    hot_keys = {_key(e["field_key"], e["event_type"]) for e in hot_events}
    shadow_keys = {_key(e.get("field_key"), e.get("event_type")) for e in shadow_events}

    matched = sorted(hot_keys & shadow_keys)
    hot_only = sorted(hot_keys - shadow_keys)
    shadow_only = sorted(shadow_keys - hot_keys)
    diff = {
        "counts": {
            "matched": len(matched),
            "hot_only": len(hot_only),
            "shadow_only": len(shadow_only),
        },
        "matched": [{"field_key": k, "event_type": t} for k, t in matched],
        "hot_only": [{"field_key": k, "event_type": t} for k, t in hot_only],
        "shadow_only": [{"field_key": k, "event_type": t} for k, t in shadow_only],
    }

    row = ArchivistShadowOutcome(
        service_case_id=service_case_id,
        run_at=now,
        watermark_from=watermark_from,
        watermark_to=watermark_to,
        shadow_events=shadow_events,
        shadow_signals=shadow_signals,
        hot_events=hot_events,
        diff=diff,
    )
    session.add(row)

    logger.info(
        "Archivist shadow reconcile service_case_id=%s delta_msgs=%s "
        "matched=%s hot_only=%s shadow_only=%s signals=%s",
        service_case_id,
        len(delta_message_ids),
        diff["counts"]["matched"],
        diff["counts"]["hot_only"],
        diff["counts"]["shadow_only"],
        len(shadow_signals),
    )
    return row
