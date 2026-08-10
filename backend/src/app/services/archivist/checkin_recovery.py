"""Recover check-ins dropped by the historical numeric evidence-ID schema bug.

The recovery is deliberately narrower than an archivist replay: it reads the original
``report_checkin`` calls from extraction traces, restores only the lost check-in fields
and answers, and never replays outcomes, profiles, summaries, FAQs, or case drafts.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_run import AgentRun
from app.models.archivist_extract_trace import ArchivistExtractTrace
from app.models.archivist_progress import ArchivistProgress
from app.models.checkin import Checkin
from app.models.message import Message, MessagePurpose, MessageSenderType, MessageStatus
from app.models.service_case import ServiceCase
from app.services.archivist.checkin_projection import upsert_checkin
from app.services.archivist.followup import mark_followup_answers
from app.services.archivist.graph import _sh_date
from app.services.archivist.report_tools import REPORT_CHECKIN, ReportCheckinArgs
from app.services.checkin_presentation import load_personalized_checkin_labels
from app.services.checkin_service import CHECKIN_KEYS


RECOVERY_MARKER = "numeric_evidence_id_recovery_v1"
_PRODUCT_KEYS = ("morning_c", "noon_b", "night_a", "summary")


def _is_empty(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, dict):
        return all(_is_empty(item) for item in value.values())
    return False


def _bad_args_count(trace: ArchivistExtractTrace) -> int:
    return sum(
        1
        for item in (trace.dropped_json or [])
        if isinstance(item, dict)
        and item.get("bucket") == "checkin"
        and item.get("reason") == "bad_args"
    )


def _contains_numeric_evidence_id(args: dict[str, Any]) -> bool:
    return any(
        isinstance(answer.get("evidence_message_id"), int)
        and not isinstance(answer.get("evidence_message_id"), bool)
        for answer_key in ("followup_answers", "checklist_answers")
        for answer in (args.get(answer_key) or [])
        if isinstance(answer, dict)
    )


def _checkin_fields(validated: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for key in CHECKIN_KEYS:
        value = validated.get(key)
        if key == "product_schedule":
            if isinstance(value, dict):
                schedule = {
                    sub_key: str(value.get(sub_key)).strip()
                    for sub_key in _PRODUCT_KEYS
                    if value.get(sub_key) not in (None, "")
                }
                if schedule:
                    fields[key] = schedule
        elif value not in (None, "") and str(value).strip():
            fields[key] = str(value).strip()
    return fields


def _merge_fields(target: dict[str, Any], incoming: dict[str, Any]) -> None:
    for key, value in incoming.items():
        if key == "product_schedule" and isinstance(value, dict):
            target.setdefault(key, {}).update(value)
        else:
            target[key] = value


def _only_missing_fields(
    incoming: dict[str, Any], existing: dict[str, Any]
) -> dict[str, Any]:
    missing: dict[str, Any] = {}
    for key, value in incoming.items():
        if key != "product_schedule":
            if _is_empty(existing.get(key)):
                missing[key] = value
            continue
        current_schedule = existing.get(key)
        current_schedule = current_schedule if isinstance(current_schedule, dict) else {}
        schedule = {
            sub_key: sub_value
            for sub_key, sub_value in value.items()
            if _is_empty(current_schedule.get(sub_key))
        }
        if schedule:
            missing[key] = schedule
    return missing


async def _allowed_followup_answers(
    session: AsyncSession, *, service_case_id: int
) -> set[tuple[str, str, str]]:
    reminders = list(
        await session.scalars(
            select(Message).where(
                Message.service_case_id == service_case_id,
                Message.purpose == MessagePurpose.REMINDER,
                Message.sender_type == MessageSenderType.SYSTEM,
                Message.status == MessageStatus.SENT,
            )
        )
    )
    allowed: set[tuple[str, str, str]] = set()
    for reminder in reminders:
        metadata = (
            reminder.analysis_json if isinstance(reminder.analysis_json, dict) else {}
        )
        context = metadata.get("checkin_followup") if isinstance(metadata, dict) else None
        if not isinstance(context, dict):
            continue
        for item in context.get("items") or []:
            if not isinstance(item, dict):
                continue
            allowed.add(
                (
                    str(item.get("item_id") or ""),
                    str(item.get("cycle_date") or ""),
                    str(item.get("target_date") or ""),
                )
            )
    return allowed


async def recover_numeric_evidence_id_checkins(
    session: AsyncSession,
    *,
    apply: bool = False,
    trace_ids: set[int] | None = None,
    since: datetime | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Preview or apply the narrow historical repair.

    Existing non-empty check-in fields and answers are protected. Among multiple lost
    reports for the same day, the latest recovered value wins only for a field that is
    still empty in the authoritative check-in row.
    """
    stmt = (
        select(ArchivistExtractTrace, AgentRun)
        .outerjoin(AgentRun, AgentRun.id == ArchivistExtractTrace.agent_run_id)
        .order_by(ArchivistExtractTrace.created_at.asc(), ArchivistExtractTrace.id.asc())
    )
    if trace_ids:
        stmt = stmt.where(ArchivistExtractTrace.id.in_(trace_ids))
    if since is not None:
        stmt = stmt.where(ArchivistExtractTrace.created_at >= since)
    rows = (await session.execute(stmt)).all()

    affected = [(trace, run) for trace, run in rows if _bad_args_count(trace) > 0]
    if limit is not None:
        affected = affected[: max(0, limit)]

    stats: Counter[str] = Counter(
        scanned_traces=len(rows),
        affected_traces=len(affected),
    )
    payloads: dict[tuple[int, date], dict[str, Any]] = {}
    exact_followups: set[tuple[int, date, str]] = set()
    repairable_trace_ids: set[int] = set()
    trace_by_id: dict[int, ArchivistExtractTrace] = {}
    followup_allowed_cache: dict[int, set[tuple[str, str, str]]] = {}

    for trace, run in affected:
        trace_by_id[trace.id] = trace
        marker = (trace.kept_json or {}).get(RECOVERY_MARKER)
        if isinstance(marker, dict) and marker.get("status") == "applied":
            stats["already_repaired_traces"] += 1
            continue

        input_ids = {
            int(value)
            for value in (
                run.metadata_json.get("input_message_ids", [])
                if run is not None and isinstance(run.metadata_json, dict)
                else []
            )
            if str(value).isdigit()
        }
        raw_calls = [
            call
            for call in (trace.emitted_json or [])
            if isinstance(call, dict)
            and call.get("name") == REPORT_CHECKIN
            and isinstance(call.get("args"), dict)
            and _contains_numeric_evidence_id(call["args"])
        ]
        if not raw_calls:
            stats["unmatched_traces"] += 1
            continue

        evidence_ids = {
            int(answer["evidence_message_id"])
            for call in raw_calls
            for answer_key in ("followup_answers", "checklist_answers")
            for answer in (call["args"].get(answer_key) or [])
            if isinstance(answer, dict)
            and isinstance(answer.get("evidence_message_id"), int)
            and not isinstance(answer.get("evidence_message_id"), bool)
        }
        message_ids = input_ids | evidence_ids
        messages = (
            list(
                await session.scalars(
                    select(Message).where(Message.id.in_(message_ids))
                )
            )
            if message_ids
            else []
        )
        message_by_id = {message.id: message for message in messages}
        allowed_dates = {
            _sh_date(message.created_at).isoformat()
            for message in messages
            if message.id in input_ids and message.created_at is not None
        }
        if trace.target_date is not None:
            allowed_dates.add(trace.target_date.isoformat())

        for call in raw_calls[: _bad_args_count(trace)]:
            raw_args = call["args"]
            try:
                validated = ReportCheckinArgs.model_validate(raw_args).model_dump()
                target_date = date.fromisoformat(str(validated["target_date"]))
            except (ValidationError, ValueError, TypeError):
                stats["still_invalid_calls"] += 1
                continue
            if target_date.isoformat() not in allowed_dates:
                stats["date_mismatch_calls"] += 1
                continue

            fields = _checkin_fields(validated)
            if fields:
                payload = payloads.setdefault(
                    (trace.service_case_id, target_date),
                    {"fields": {}, "answers": {}, "evidence_ids": set(), "trace_ids": set()},
                )
                _merge_fields(payload["fields"], fields)
                payload["trace_ids"].add(trace.id)
                repairable_trace_ids.add(trace.id)

            answer_groups = (
                ("checklist", validated.get("checklist_answers") or []),
                ("followup", validated.get("followup_answers") or []),
            )
            for answer_kind, answers in answer_groups:
                if (
                    answer_kind == "followup"
                    and trace.service_case_id not in followup_allowed_cache
                ):
                    followup_allowed_cache[
                        trace.service_case_id
                    ] = await _allowed_followup_answers(
                        session,
                        service_case_id=trace.service_case_id,
                    )
                for answer in answers:
                    answer_date = date.fromisoformat(str(answer["target_date"]))
                    evidence_id = int(answer["evidence_message_id"])
                    evidence = message_by_id.get(evidence_id)
                    if (
                        evidence is None
                        or evidence.service_case_id != trace.service_case_id
                        or evidence.sender_type != MessageSenderType.USER
                        or (input_ids and evidence_id not in input_ids)
                    ):
                        stats["invalid_evidence_answers"] += 1
                        continue
                    if (
                        answer_kind == "checklist"
                        and answer_date.isoformat() not in allowed_dates
                    ):
                        stats["date_mismatch_answers"] += 1
                        continue

                    item_id = str(answer["item_id"])
                    if answer_kind == "checklist":
                        labels = await load_personalized_checkin_labels(
                            session,
                            service_case_id=trace.service_case_id,
                            item_ids=[item_id],
                        )
                        if item_id not in labels:
                            stats["unknown_checklist_answers"] += 1
                            continue
                    else:
                        cycle_date = str(answer["cycle_date"])
                        allowed_key = (item_id, cycle_date, answer_date.isoformat())
                        if (
                            allowed_key
                            not in followup_allowed_cache[trace.service_case_id]
                        ):
                            stats["unknown_followup_answers"] += 1
                            continue
                        exact_followups.add(
                            (
                                trace.service_case_id,
                                date.fromisoformat(cycle_date),
                                item_id,
                            )
                        )
                        if not item_id.startswith("document:"):
                            repairable_trace_ids.add(trace.id)
                            continue

                    payload = payloads.setdefault(
                        (trace.service_case_id, answer_date),
                        {
                            "fields": {},
                            "answers": {},
                            "evidence_ids": set(),
                            "trace_ids": set(),
                        },
                    )
                    payload["answers"][item_id] = answer
                    payload["evidence_ids"].add(evidence_id)
                    payload["trace_ids"].add(trace.id)
                    repairable_trace_ids.add(trace.id)
                    stats["repairable_answers"] += 1
            stats["repairable_calls"] += 1

    case_ids = sorted(
        {case_id for case_id, _ in payloads}
        | {item[0] for item in exact_followups}
    )
    if apply and case_ids:
        locked_ids = set(
            await session.scalars(
                select(ArchivistProgress.service_case_id)
                .where(ArchivistProgress.service_case_id.in_(case_ids))
                .order_by(ArchivistProgress.service_case_id.asc())
                .with_for_update()
            )
        )
        missing_locks = set(case_ids) - locked_ids
        if missing_locks:
            raise RuntimeError(
                f"Missing archivist progress rows for service cases: {sorted(missing_locks)}"
            )

    details: list[dict[str, Any]] = []
    for (service_case_id, target_date), payload in sorted(payloads.items()):
        service_case = await session.get(ServiceCase, service_case_id)
        if service_case is None:
            stats["missing_service_cases"] += 1
            continue
        existing = await session.scalar(
            select(Checkin).where(
                Checkin.service_case_id == service_case_id,
                Checkin.checkin_date == target_date,
                Checkin.deleted_at.is_(None),
            )
        )
        parsed = (
            existing.parsed_json
            if existing is not None and isinstance(existing.parsed_json, dict)
            else {}
        )
        fields = _only_missing_fields(payload["fields"], parsed)
        existing_answers = parsed.get("custom_checkin") if isinstance(parsed, dict) else {}
        existing_answers = existing_answers if isinstance(existing_answers, dict) else {}
        answers = [
            answer
            for item_id, answer in payload["answers"].items()
            if _is_empty(
                existing_answers.get(item_id, {}).get("answer")
                if isinstance(existing_answers.get(item_id), dict)
                else existing_answers.get(item_id)
            )
        ]
        detail = {
            "service_case_id": service_case_id,
            "target_date": target_date.isoformat(),
            "trace_ids": sorted(payload["trace_ids"]),
            "fields": sorted(fields),
            "item_ids": sorted(str(item["item_id"]) for item in answers),
        }
        details.append(detail)
        stats["candidate_days"] += 1
        stats["fields_to_write"] += len(fields)
        stats["answers_to_write"] += len(answers)
        if not apply or (not fields and not answers):
            continue
        result = await upsert_checkin(
            session,
            service_case=service_case,
            checkin_date=target_date,
            fields=fields,
            evidence_msg_ids=sorted(payload["evidence_ids"]),
            content_hint="（历史打卡漏记修复）",
            custom_answers=answers,
        )
        stats["days_changed"] += int(
            bool(result.created or result.filled or result.custom_answers_written)
        )
        stats["fields_written"] += len(result.filled)
        stats["answers_written"] += len(result.custom_answers_written)

    if apply and exact_followups:
        by_case: dict[int, list[tuple[date, str]]] = {}
        for service_case_id, cycle_date, item_id in sorted(exact_followups):
            by_case.setdefault(service_case_id, []).append((cycle_date, item_id))
        for service_case_id, cycle_items in by_case.items():
            await mark_followup_answers(
                session,
                service_case_id=service_case_id,
                cycle_dates_and_items=cycle_items,
            )
        stats["followup_answers_marked"] = len(exact_followups)

    if apply:
        applied_at = datetime.now(timezone.utc).isoformat()
        for trace_id in repairable_trace_ids:
            trace = trace_by_id[trace_id]
            kept = dict(trace.kept_json or {})
            kept[RECOVERY_MARKER] = {
                "status": "applied",
                "applied_at": applied_at,
            }
            trace.kept_json = kept
        stats["traces_marked_repaired"] = len(repairable_trace_ids)

    return {
        "mode": "apply" if apply else "preview",
        "stats": dict(stats),
        "details": details,
    }
