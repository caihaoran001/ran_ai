from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
from typing import Any, Protocol

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.inbound_event import InboundEvent
from app.models.message import (
    Message,
    MessageDirection,
    MessageSenderType,
    MessageStatus,
)
from app.models.service_case import ServiceCase
from app.services.agent.checkpoint import (
    build_checkpoint_thread_id,
    get_agent_checkpointer,
)
from app.services.channels.qiwe import QiWeHistoryPage
from app.services.content_policy import (
    disable_ai_reply_due_to_risk,
    get_ai_reply_state,
)
from app.services.qiwe_service.inbox import (
    persist_qiwe_spam_block_event,
    process_qiwe_spam_block_event,
    qiwe_spam_block_notice,
)
from app.services.service_case_queries import select_active_service_cases


logger = logging.getLogger("uvicorn.error")

HISTORY_LIMIT = 50
MATCH_WINDOW = timedelta(minutes=3)
ANCHOR_SEARCH_LIMIT = 200
HISTORY_MAX_PAGES = 5
HISTORY_RECHECK_DELAY_SECONDS = 5.0
AUTO_RETRY_BATCH_LIMIT = 3


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
    confirmed_not_sent: int = 0
    retry_queued: int = 0
    blocked: int = 0
    suppressed: int = 0
    not_found: int = 0
    ambiguous: int = 0
    skipped: int = 0
    history_pages: int = 0
    circuit_open: bool = False


@dataclass(frozen=True, slots=True)
class _HistoryWindow:
    items: list[dict[str, Any]]
    complete: bool
    pages_read: int


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
    analysis = message.analysis_json if isinstance(message.analysis_json, dict) else {}
    curated = analysis.get("curated_video") if isinstance(analysis.get("curated_video"), dict) else None
    if curated:
        msg_data = item.get("msgData") if isinstance(item.get("msgData"), dict) else {}
        if item.get("msgType") != 141:
            return False
        if str(msg_data.get("feedId") or "") != str(curated.get("feed_id") or ""):
            return False
    elif _history_content(item) != message.content:
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


def _history_seq(item: dict[str, Any]) -> int | None:
    try:
        value = int(item.get("seq"))
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _history_identity(item: dict[str, Any]) -> tuple[Any, ...]:
    identifiers = tuple(
        str(item.get(key) or "")
        for key in ("msgUniqueIdentifier", "msgServerId", "seq")
    )
    if any(identifiers):
        return identifiers
    return (
        str(item.get("senderId") or ""),
        str(item.get("fromRoomId") or ""),
        str(item.get("timestamp") or ""),
        str(item.get("msgType") or ""),
        _history_content(item),
    )


def _dedupe_history_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    result: list[dict[str, Any]] = []
    for item in items:
        identity = _history_identity(item)
        if identity in seen:
            continue
        seen.add(identity)
        result.append(item)
    return result


async def _read_history_window(
    channel: QiWeHistoryChannel,
    *,
    anchor_seq: int,
) -> _HistoryWindow:
    """Read bounded pages until the current history tail is covered."""
    cursor = anchor_seq
    items: list[dict[str, Any]] = []
    for page_number in range(1, HISTORY_MAX_PAGES + 1):
        page = await channel.sync_message_history(
            after_seq=cursor,
            limit=HISTORY_LIMIT,
        )
        items.extend(page.items)
        if not page.has_more:
            return _HistoryWindow(
                items=_dedupe_history_items(items),
                complete=True,
                pages_read=page_number,
            )
        next_cursor: int | None = None
        try:
            parsed_travel_key = int(page.travel_sync_key)
        except (TypeError, ValueError):
            parsed_travel_key = None
        if parsed_travel_key is not None and parsed_travel_key > cursor:
            next_cursor = parsed_travel_key
        page_seqs = [
            seq
            for item in page.items
            if (seq := _history_seq(item)) is not None and seq > cursor
        ]
        if next_cursor is None and page_seqs:
            next_cursor = max(page_seqs)
        if next_cursor is None:
            logger.error(
                "QiWe outbound history pagination stalled anchor_seq=%s cursor=%s page=%s",
                anchor_seq,
                cursor,
                page_number,
            )
            return _HistoryWindow(
                items=_dedupe_history_items(items),
                complete=False,
                pages_read=page_number,
            )
        cursor = next_cursor
    logger.error(
        "QiWe outbound history pagination exceeded max pages anchor_seq=%s pages=%s",
        anchor_seq,
        HISTORY_MAX_PAGES,
    )
    return _HistoryWindow(
        items=_dedupe_history_items(items),
        complete=False,
        pages_read=HISTORY_MAX_PAGES,
    )


def _spam_block_notices(
    items: list[dict[str, Any]],
) -> dict[str, tuple[str, dict[str, Any]]]:
    notices: dict[str, tuple[str, dict[str, Any]]] = {}
    for item in items:
        parsed = qiwe_spam_block_notice(item)
        if parsed is None:
            continue
        channel_message_id, reason = parsed
        notices[channel_message_id] = (reason, item)
    return notices


def _history_item_is_blocked(
    item: dict[str, Any],
    notices: dict[str, tuple[str, dict[str, Any]]],
) -> str | None:
    for key in ("msgUniqueIdentifier", "msgServerId", "seq"):
        value = item.get(key)
        if value is None:
            continue
        notice = notices.get(str(value))
        if notice is not None:
            return notice[0]
    return None


async def _persist_history_spam_notices(
    notices: dict[str, tuple[str, dict[str, Any]]],
) -> list[tuple[int | None, str, str]]:
    persisted: list[tuple[int | None, str, str]] = []
    for channel_message_id, (reason, raw_payload) in notices.items():
        event_id = await persist_qiwe_spam_block_event(
            channel_message_id=channel_message_id,
            reason=reason,
            raw_payload=raw_payload,
        )
        persisted.append((event_id, channel_message_id, reason))
    return persisted


async def _process_history_spam_notices(
    notices: list[tuple[int | None, str, str]],
) -> None:
    if not notices:
        return
    handled_any = False
    for event_id, channel_message_id, reason in notices:
        handled = await process_qiwe_spam_block_event(
            event_id,
            channel_message_id,
            reason,
        )
        handled_any = handled_any or handled
    if not handled_any:
        # A valid 1011 notice from the current account is itself enough to stop
        # automatic retries, even if its original outbound row predates our DB.
        async with SessionLocal() as session:
            await disable_ai_reply_due_to_risk(
                notices[-1][2],
                session,
            )


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


def _mark_confirmed_not_sent_failed(
    row: Message,
    *,
    state: str,
    error: str,
    checked_at: datetime,
    anchor_seq: int,
) -> None:
    analysis = _with_reconciliation_metadata(
        row,
        state=state,
        checked_at=checked_at,
        anchor_seq=anchor_seq,
    )
    delivery = dict(analysis.get("_delivery") or {})
    delivery["delivery_uncertain"] = False
    analysis["_delivery"] = delivery
    row.analysis_json = analysis
    row.status = MessageStatus.FAILED
    row.error = error
    row.sent_at = None


async def _queue_confirmed_not_sent_retries(
    *,
    message_ids: list[int],
    checked_at: datetime,
    anchor_seq: int,
    channel_name: str,
) -> tuple[int, int, bool]:
    """Queue only a tiny, current, still-relevant batch for Graph recovery."""
    if not message_ids:
        return 0, 0, False

    retry_queued = 0
    suppressed = 0
    circuit_open = False
    circuit_reason = ""
    async with SessionLocal() as session:
        rows = list(
            await session.scalars(
                select(Message)
                .where(Message.id.in_(message_ids))
                .order_by(Message.id.asc())
                .with_for_update()
            )
        )
        rows = [row for row in rows if row.status == MessageStatus.UNKNOWN]
        if not rows:
            return 0, 0, False

        latest_by_group: dict[str, Message] = {}
        for row in rows:
            latest_by_group[row.group_id] = row

        global_reply_state = await get_ai_reply_state(session)
        global_enabled = bool(global_reply_state.get("enabled", True))
        candidates: list[tuple[Message, Message]] = []

        for row in rows:
            if latest_by_group.get(row.group_id) is not row:
                _mark_confirmed_not_sent_failed(
                    row,
                    state="history_confirmed_not_sent_superseded",
                    error="confirmed not sent; superseded by a newer uncertain reply in the same group",
                    checked_at=checked_at,
                    anchor_seq=anchor_seq,
                )
                suppressed += 1
                continue
            if not global_enabled:
                _mark_confirmed_not_sent_failed(
                    row,
                    state="history_confirmed_not_sent_auto_reply_disabled",
                    error="confirmed not sent; automatic retry suppressed because global AI reply is disabled",
                    checked_at=checked_at,
                    anchor_seq=anchor_seq,
                )
                suppressed += 1
                continue
            if row.trigger_id is None:
                _mark_confirmed_not_sent_failed(
                    row,
                    state="history_confirmed_not_sent_no_trigger",
                    error="confirmed not sent; no inbound trigger is available for Graph recovery",
                    checked_at=checked_at,
                    anchor_seq=anchor_seq,
                )
                suppressed += 1
                continue

            inbound = await session.get(Message, row.trigger_id)
            if (
                inbound is None
                or inbound.direction != MessageDirection.INBOUND
                or inbound.service_case_id is None
            ):
                _mark_confirmed_not_sent_failed(
                    row,
                    state="history_confirmed_not_sent_invalid_trigger",
                    error="confirmed not sent; inbound trigger is missing or no longer recoverable",
                    checked_at=checked_at,
                    anchor_seq=anchor_seq,
                )
                suppressed += 1
                continue

            service_case = await session.scalar(
                select_active_service_cases(
                    ServiceCase.id == inbound.service_case_id
                )
            )
            if service_case is None or not service_case.ai_auto_reply_enabled:
                _mark_confirmed_not_sent_failed(
                    row,
                    state="history_confirmed_not_sent_case_disabled",
                    error="confirmed not sent; automatic retry suppressed because the service case is inactive or silenced",
                    checked_at=checked_at,
                    anchor_seq=anchor_seq,
                )
                suppressed += 1
                continue

            newer_inbound = await session.scalar(
                select(Message.id)
                .where(
                    Message.service_case_id == inbound.service_case_id,
                    Message.id > inbound.id,
                    Message.direction == MessageDirection.INBOUND,
                )
                .limit(1)
            )
            newer_pending_event = await session.scalar(
                select(InboundEvent.id)
                .where(
                    InboundEvent.status == InboundEvent.STATUS_PENDING,
                    InboundEvent.event_type == InboundEvent.EVENT_MESSAGE,
                    InboundEvent.chat_id == row.group_id,
                    InboundEvent.created_at > row.created_at,
                )
                .limit(1)
            )
            if newer_inbound or newer_pending_event:
                _mark_confirmed_not_sent_failed(
                    row,
                    state="history_confirmed_not_sent_stale",
                    error="confirmed not sent; automatic retry suppressed because a newer inbound message exists",
                    checked_at=checked_at,
                    anchor_seq=anchor_seq,
                )
                suppressed += 1
                continue
            candidates.append((row, inbound))

        if len(candidates) > AUTO_RETRY_BATCH_LIMIT:
            circuit_open = True
            circuit_reason = (
                "QiWe startup recovery circuit opened: "
                f"{len(candidates)} confirmed-unsent replies exceeds "
                f"the automatic retry limit {AUTO_RETRY_BATCH_LIMIT}"
            )
            for row, _inbound in candidates:
                _mark_confirmed_not_sent_failed(
                    row,
                    state="history_confirmed_not_sent_batch_circuit",
                    error=circuit_reason,
                    checked_at=checked_at,
                    anchor_seq=anchor_seq,
                )
                suppressed += 1
        else:
            from app.services.ranren_ai.engine import (
                _REPLY_STATUS_RETRYABLE_FAILURE,
                _with_reply_processing_status,
            )
            from app.services.ranren_ai.engine_graph import (
                _checkpoint_fallback_key,
            )

            for row, inbound in candidates:
                checkpointer = get_agent_checkpointer()
                if checkpointer is not None:
                    inbound_analysis = (
                        inbound.analysis_json
                        if isinstance(inbound.analysis_json, dict)
                        else {}
                    )
                    meta = (
                        inbound_analysis.get("_qiwe_meta")
                        if isinstance(inbound_analysis.get("_qiwe_meta"), dict)
                        else {}
                    )
                    fallback = _checkpoint_fallback_key(
                        user_id=str(meta.get("sender_id") or ""),
                        timestamp=int(
                            meta.get("timestamp")
                            or int(inbound.created_at.timestamp())
                        ),
                        message_type=inbound.message_type,
                        text=inbound.content,
                    )
                    thread_id = build_checkpoint_thread_id(
                        workflow="reply",
                        channel=channel_name,
                        chat_id=inbound.group_id,
                        message_id=inbound.wecom_message_id,
                        fallback_key=fallback,
                    )
                    try:
                        # A checkpoint can already be positioned after reply_send
                        # (for example at finalize). Reset the same stable thread
                        # before retrying, otherwise Graph would complete without
                        # executing the newly queued send.
                        await checkpointer.adelete_thread(thread_id)
                    except Exception:
                        logger.exception(
                            "QiWe confirmed-unsent checkpoint reset failed message_id=%s thread_id=%s",
                            row.id,
                            thread_id,
                        )
                        _mark_confirmed_not_sent_failed(
                            row,
                            state="history_confirmed_not_sent_checkpoint_reset_failed",
                            error="confirmed not sent; Graph checkpoint could not be reset safely",
                            checked_at=checked_at,
                            anchor_seq=anchor_seq,
                        )
                        suppressed += 1
                        continue

                analysis = _with_reconciliation_metadata(
                    row,
                    state="history_confirmed_not_sent_retry_pending",
                    checked_at=checked_at,
                    anchor_seq=anchor_seq,
                )
                delivery = dict(analysis.get("_delivery") or {})
                delivery["delivery_uncertain"] = False
                delivery["retry_queued_at"] = checked_at.isoformat()
                analysis["_delivery"] = delivery
                row.analysis_json = analysis
                row.status = MessageStatus.PENDING
                row.error = None
                row.sent_at = None

                inbound_analysis = (
                    dict(inbound.analysis_json)
                    if isinstance(inbound.analysis_json, dict)
                    else {}
                )
                inbound_analysis.pop("_recovery_claim", None)
                inbound.analysis_json = _with_reply_processing_status(
                    inbound_analysis,
                    status=_REPLY_STATUS_RETRYABLE_FAILURE,
                    reason="outbound_confirmed_not_sent",
                )
                retry_queued += 1
        await session.commit()

    if circuit_open:
        async with SessionLocal() as session:
            await disable_ai_reply_due_to_risk(circuit_reason, session)
        logger.error(circuit_reason)
    return retry_queued, suppressed, circuit_open


async def reconcile_frozen_unknown_sends(
    channel: QiWeHistoryChannel,
    frozen_ids: list[int],
    *,
    recheck_delay_seconds: float = HISTORY_RECHECK_DELAY_SECONDS,
) -> OutboundReconciliationResult:
    """Reconcile one immutable startup batch, then safely queue proven misses."""
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
        first_window = await _read_history_window(
            channel,
            anchor_seq=anchor_seq,
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

    first_matches = {
        message.id: [
            item
            for item in first_window.items
            if _item_matches(message, item, bot_user_id=bot_user_id)
        ]
        for message in candidates
    }
    needs_recheck = [
        message
        for message in candidates
        if not first_matches.get(message.id)
    ]
    second_window: _HistoryWindow | None = None
    if needs_recheck:
        if recheck_delay_seconds > 0:
            await asyncio.sleep(recheck_delay_seconds)
        try:
            second_window = await _read_history_window(
                channel,
                anchor_seq=anchor_seq,
            )
        except Exception:
            logger.exception(
                "QiWe outbound reconciliation second history read failed frozen_ids=%s anchor_seq=%s",
                ids,
                anchor_seq,
            )

    all_items = _dedupe_history_items(
        first_window.items
        + (second_window.items if second_window is not None else [])
    )
    notices = _spam_block_notices(all_items)
    persisted_notices = await _persist_history_spam_notices(notices)

    matches_by_message: dict[int, list[int]] = {}
    item_claim_counts: dict[int, int] = {}
    for message in candidates:
        matches = [
            index
            for index, item in enumerate(all_items)
            if _item_matches(message, item, bot_user_id=bot_user_id)
        ]
        matches_by_message[message.id] = matches
        for index in matches:
            item_claim_counts[index] = item_claim_counts.get(index, 0) + 1

    confirmed = 0
    confirmed_not_sent_ids: list[int] = []
    blocked = 0
    not_found = 0
    unconfirmed_not_found = 0
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
                history_complete_twice = (
                    first_window.complete
                    and second_window is not None
                    and second_window.complete
                )
                row.analysis_json = _with_reconciliation_metadata(
                    row,
                    state=(
                        "history_confirmed_not_sent"
                        if history_complete_twice
                        else "history_not_found_unconfirmed"
                    ),
                    checked_at=checked_at,
                    anchor_seq=anchor_seq,
                )
                if history_complete_twice:
                    confirmed_not_sent_ids.append(row.id)
                else:
                    unconfirmed_not_found += 1
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

            item = all_items[matches[0]]
            blocked_reason = _history_item_is_blocked(item, notices)
            analysis = _with_reconciliation_metadata(
                row,
                state=(
                    "history_confirmed_blocked"
                    if blocked_reason
                    else "history_confirmed_sent"
                ),
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
            if blocked_reason:
                row.status = MessageStatus.FAILED
                row.error = blocked_reason
                row.sent_at = None
                blocked += 1
            else:
                row.status = MessageStatus.SENT
                row.error = None
                row.sent_at = _history_timestamp(item) or checked_at
                confirmed += 1
        await session.commit()

    await _process_history_spam_notices(persisted_notices)
    retry_queued, suppressed, circuit_open = (
        await _queue_confirmed_not_sent_retries(
            message_ids=confirmed_not_sent_ids,
            checked_at=checked_at,
            anchor_seq=anchor_seq,
            channel_name=str(getattr(channel, "name", None) or "qiwe"),
        )
    )
    history_pages = first_window.pages_read + (
        second_window.pages_read if second_window is not None else 0
    )
    logger.info(
        "QiWe outbound reconciliation complete frozen=%d eligible=%d sent=%d not_sent=%d retry_queued=%d blocked=%d suppressed=%d not_found=%d ambiguous=%d circuit_open=%s anchor_seq=%s returned=%d pages=%d",
        len(ids),
        len(candidates),
        confirmed,
        len(confirmed_not_sent_ids),
        retry_queued,
        blocked,
        suppressed,
        not_found,
        ambiguous,
        circuit_open,
        anchor_seq,
        len(all_items),
        history_pages,
    )
    return OutboundReconciliationResult(
        frozen_count=len(ids),
        eligible_count=len(candidates),
        api_called=True,
        anchor_seq=anchor_seq,
        confirmed_sent=confirmed,
        confirmed_not_sent=len(confirmed_not_sent_ids),
        retry_queued=retry_queued,
        blocked=blocked,
        suppressed=suppressed,
        not_found=not_found,
        ambiguous=ambiguous,
        skipped=unconfirmed_not_found,
        history_pages=history_pages,
        circuit_open=circuit_open,
    )
