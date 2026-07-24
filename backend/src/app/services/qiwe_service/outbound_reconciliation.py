from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
from typing import Any, Protocol

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.message import (
    Message,
    MessageDirection,
    MessageSenderType,
    MessageStatus,
)
from app.services.channels.qiwe import QiWeHistoryPage


logger = logging.getLogger("uvicorn.error")

HISTORY_LIMIT = 50
MATCH_WINDOW = timedelta(minutes=3)
ANCHOR_SEARCH_LIMIT = 200


class QiWeHistoryChannel(Protocol):
    async def get_profile(self) -> dict[str, Any] | None: ...

    async def sync_message_history(
        self,
        *,
        after_seq: int,
        limit: int = HISTORY_LIMIT,
    ) -> QiWeHistoryPage: ...


@dataclass(frozen=True, slots=True)
class OutboundReconciliationResult:
    frozen_count: int
    eligible_count: int = 0
    api_called: bool = False
    anchor_seq: int | None = None
    confirmed_sent: int = 0
    not_found: int = 0
    ambiguous: int = 0
    skipped: int = 0


def _delivery(message: Message) -> dict[str, Any]:
    analysis = message.analysis_json if isinstance(message.analysis_json, dict) else {}
    value = analysis.get("_delivery")
    return value if isinstance(value, dict) else {}


def _provider_receipt(message: Message) -> dict[str, Any]:
    value = _delivery(message).get("provider_receipt")
    return value if isinstance(value, dict) else {}


def _receipt_seq(message: Message) -> int | None:
    value = _provider_receipt(message).get("seq")
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _request_started_at(message: Message) -> datetime:
    parsed = _parse_datetime(_delivery(message).get("request_started_at"))
    if parsed is not None:
        return parsed
    created_at = message.created_at
    if created_at.tzinfo is None:
        return created_at.replace(tzinfo=timezone.utc)
    return created_at.astimezone(timezone.utc)


def _history_timestamp(item: dict[str, Any]) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(item.get("timestamp")), timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _history_content(item: dict[str, Any]) -> str:
    msg_data = item.get("msgData")
    if isinstance(msg_data, dict):
        return str(msg_data.get("content") or "")
    return str(item.get("content") or "")


def _item_matches(
    message: Message,
    item: dict[str, Any],
    *,
    bot_user_id: str,
) -> bool:
    sent_at = _history_timestamp(item)
    if sent_at is None:
        return False
    if str(item.get("senderId") or "") != bot_user_id:
        return False
    if str(item.get("fromRoomId") or "") != str(message.group_id):
        return False
    if _history_content(item) != message.content:
        return False
    return abs(sent_at - _request_started_at(message)) <= MATCH_WINDOW


def _history_receipt(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item[key]
        for key in (
            "msgUniqueIdentifier",
            "msgServerId",
            "seq",
            "timestamp",
        )
        if item.get(key) is not None
    }


def _history_message_id(item: dict[str, Any]) -> str | None:
    for key in ("msgUniqueIdentifier", "msgServerId", "seq"):
        value = item.get(key)
        if value is not None:
            return str(value)
    return None


async def _load_batch_and_anchor(
    frozen_ids: list[int],
) -> tuple[list[Message], int | None]:
    async with SessionLocal() as session:
        candidates = list(
            await session.scalars(
                select(Message)
                .where(
                    Message.id.in_(frozen_ids),
                    Message.direction == MessageDirection.OUTBOUND,
                    Message.sender_type == MessageSenderType.BOT,
                    Message.status == MessageStatus.UNKNOWN,
                )
                .order_by(Message.id.asc())
            )
        )
        if not candidates:
            return [], None

        prior_rows = list(
            await session.scalars(
                select(Message)
                .where(
                    Message.id < candidates[0].id,
                    Message.direction == MessageDirection.OUTBOUND,
                    Message.status == MessageStatus.SENT,
                )
                .order_by(Message.id.desc())
                .limit(ANCHOR_SEARCH_LIMIT)
            )
        )
        anchor_seq = next(
            (seq for row in prior_rows if (seq := _receipt_seq(row)) is not None),
            None,
        )
        if anchor_seq is None:
            candidate_seqs = [
                seq
                for row in candidates
                if (seq := _receipt_seq(row)) is not None
            ]
            if candidate_seqs:
                anchor_seq = max(0, min(candidate_seqs) - 1)
        return candidates, anchor_seq


def _with_reconciliation_metadata(
    message: Message,
    *,
    state: str,
    checked_at: datetime,
    anchor_seq: int,
) -> dict[str, Any]:
    analysis = dict(message.analysis_json or {})
    delivery = dict(analysis.get("_delivery") or {})
    delivery["reconciliation"] = state
    delivery["reconciled_at"] = checked_at.isoformat()
    delivery["history_anchor_seq"] = anchor_seq
    delivery["history_limit"] = HISTORY_LIMIT
    analysis["_delivery"] = delivery
    return analysis


async def reconcile_frozen_unknown_sends(
    channel: QiWeHistoryChannel,
    frozen_ids: list[int],
) -> OutboundReconciliationResult:
    """Reconcile only the immutable startup snapshot against one history page."""
    ids = sorted({int(message_id) for message_id in frozen_ids if message_id})
    if not ids:
        return OutboundReconciliationResult(frozen_count=0)

    candidates, anchor_seq = await _load_batch_and_anchor(ids)
    base = OutboundReconciliationResult(
        frozen_count=len(ids),
        eligible_count=len(candidates),
        anchor_seq=anchor_seq,
    )
    if not candidates:
        return base
    if anchor_seq is None:
        logger.warning(
            "QiWe outbound reconciliation skipped: no known seq before frozen ids=%s",
            ids,
        )
        return OutboundReconciliationResult(
            frozen_count=base.frozen_count,
            eligible_count=base.eligible_count,
            anchor_seq=base.anchor_seq,
            skipped=len(candidates),
        )

    try:
        profile = await channel.get_profile()
        bot_user_id = str((profile or {}).get("userId") or "").strip()
        if not bot_user_id:
            raise RuntimeError("QiWe profile did not include userId")
        page = await channel.sync_message_history(
            after_seq=anchor_seq,
            limit=HISTORY_LIMIT,
        )
    except Exception:
        logger.exception(
            "QiWe outbound reconciliation history read failed frozen_ids=%s anchor_seq=%s",
            ids,
            anchor_seq,
        )
        return OutboundReconciliationResult(
            frozen_count=base.frozen_count,
            eligible_count=base.eligible_count,
            api_called=True,
            anchor_seq=base.anchor_seq,
            skipped=len(candidates),
        )

    matches_by_message: dict[int, list[int]] = {}
    item_claim_counts: dict[int, int] = {}
    for message in candidates:
        matches = [
            index
            for index, item in enumerate(page.items)
            if _item_matches(message, item, bot_user_id=bot_user_id)
        ]
        matches_by_message[message.id] = matches
        for index in matches:
            item_claim_counts[index] = item_claim_counts.get(index, 0) + 1

    confirmed = 0
    not_found = 0
    ambiguous = 0
    checked_at = datetime.now(timezone.utc)
    async with SessionLocal() as session:
        rows = {
            row.id: row
            for row in await session.scalars(
                select(Message)
                .where(Message.id.in_(ids))
                .with_for_update()
            )
        }
        for candidate in candidates:
            row = rows.get(candidate.id)
            if row is None or row.status != MessageStatus.UNKNOWN:
                continue
            matches = matches_by_message.get(candidate.id, [])
            if not matches:
                not_found += 1
                row.analysis_json = _with_reconciliation_metadata(
                    row,
                    state="history_not_found",
                    checked_at=checked_at,
                    anchor_seq=anchor_seq,
                )
                continue
            if len(matches) != 1 or item_claim_counts.get(matches[0], 0) != 1:
                ambiguous += 1
                row.analysis_json = _with_reconciliation_metadata(
                    row,
                    state="history_ambiguous",
                    checked_at=checked_at,
                    anchor_seq=anchor_seq,
                )
                continue

            item = page.items[matches[0]]
            analysis = _with_reconciliation_metadata(
                row,
                state="history_confirmed_sent",
                checked_at=checked_at,
                anchor_seq=anchor_seq,
            )
            delivery = dict(analysis.get("_delivery") or {})
            receipt = _history_receipt(item)
            if receipt:
                delivery["provider_receipt"] = receipt
            delivery["delivery_uncertain"] = False
            analysis["_delivery"] = delivery
            row.analysis_json = analysis
            row.wecom_message_id = _history_message_id(item)
            row.status = MessageStatus.SENT
            row.error = None
            row.sent_at = _history_timestamp(item) or checked_at
            confirmed += 1
        await session.commit()

    logger.info(
        "QiWe outbound reconciliation complete frozen=%d eligible=%d confirmed=%d not_found=%d ambiguous=%d anchor_seq=%s returned=%d has_more=%s",
        len(ids),
        len(candidates),
        confirmed,
        not_found,
        ambiguous,
        anchor_seq,
        len(page.items),
        page.has_more,
    )
    return OutboundReconciliationResult(
        frozen_count=len(ids),
        eligible_count=len(candidates),
        api_called=True,
        anchor_seq=anchor_seq,
        confirmed_sent=confirmed,
        not_found=not_found,
        ambiguous=ambiguous,
    )
