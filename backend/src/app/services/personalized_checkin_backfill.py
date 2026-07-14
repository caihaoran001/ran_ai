"""Idempotent backfill of historic personalized H5 answers into daily check-ins."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.checkin import Checkin
from app.models.questionnaire import QuestionnaireSubmission
from app.models.user_document import DocumentStatus, DocumentType, UserDocument
from app.services.intake_generation import personalized_checkin_fields_from_document_payloads


_SH_TZ = ZoneInfo("Asia/Shanghai")
_PERSONALIZED_KEY = re.compile(r"^personalized_checkin_([1-5])$")


@dataclass
class PersonalizedCheckinBackfillResult:
    submissions_seen: int = 0
    submissions_matched: int = 0
    answers_planned: int = 0
    answers_written: int = 0
    skipped: Counter[str] = field(default_factory=Counter)

    def as_dict(self) -> dict[str, object]:
        return {
            "submissions_seen": self.submissions_seen,
            "submissions_matched": self.submissions_matched,
            "answers_planned": self.answers_planned,
            "answers_written": self.answers_written,
            "skipped": dict(sorted(self.skipped.items())),
        }


def _shanghai_date(value: datetime) -> object:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(_SH_TZ).date()


def _h5_personalized_answers(answers_json: dict | None) -> list[tuple[int, str]]:
    values: list[tuple[int, str]] = []
    for key, value in (answers_json or {}).items():
        match = _PERSONALIZED_KEY.match(str(key))
        answer = str(value or "").strip()
        if match and answer:
            values.append((int(match.group(1)), answer))
    return sorted(values)


async def _document_for_submission(
    session: AsyncSession,
    submission: QuestionnaireSubmission,
) -> UserDocument | None:
    """Find the execution document that existed when this H5 form was submitted.

    The legacy submission did not persist a document id or a field snapshot. A sent document
    at or before submission is authoritative; pre-send historical rows can only use an already
    created document, and otherwise remain safely skipped.
    """
    sent = await session.scalar(
        select(UserDocument)
        .where(
            UserDocument.service_case_id == submission.service_case_id,
            UserDocument.document_type == DocumentType.DAY28_EXECUTION_STANDARD,
            UserDocument.status == DocumentStatus.SENT,
            UserDocument.sent_at.is_not(None),
            UserDocument.sent_at <= submission.created_at,
        )
        .order_by(UserDocument.sent_at.desc(), UserDocument.id.desc())
        .limit(1)
    )
    if sent is not None:
        return sent
    return await session.scalar(
        select(UserDocument)
        .where(
            UserDocument.service_case_id == submission.service_case_id,
            UserDocument.document_type == DocumentType.DAY28_EXECUTION_STANDARD,
            UserDocument.created_at <= submission.created_at,
        )
        .order_by(UserDocument.created_at.desc(), UserDocument.id.desc())
        .limit(1)
    )


async def backfill_personalized_h5_checkins(
    session: AsyncSession,
    *,
    apply: bool = False,
) -> PersonalizedCheckinBackfillResult:
    """Preview or copy legacy H5 personalized answers into `checkins.custom_checkin`.

    Never deletes source submissions or overwrites an existing dynamic answer. Callers own the
    transaction so production can inspect a dry-run before committing an apply run.
    """
    result = PersonalizedCheckinBackfillResult()
    submissions = list(
        await session.scalars(
            select(QuestionnaireSubmission)
            .where(QuestionnaireSubmission.template_key == "personalized_checkin")
            .order_by(QuestionnaireSubmission.id.asc())
        )
    )
    for submission in submissions:
        result.submissions_seen += 1
        answers = _h5_personalized_answers(submission.answers_json)
        if not answers:
            result.skipped["no_dynamic_answers"] += 1
            continue
        document = await _document_for_submission(session, submission)
        if document is None or not isinstance(document.content_json, dict):
            result.skipped["no_matching_execution_document"] += 1
            continue
        fields = personalized_checkin_fields_from_document_payloads(
            {DocumentType.DAY28_EXECUTION_STANDARD.value: document.content_json}
        )
        if len(fields) != 5:
            result.skipped["execution_document_does_not_have_five_items"] += 1
            continue
        checkin = await session.scalar(
            select(Checkin).where(
                Checkin.service_case_id == submission.service_case_id,
                Checkin.checkin_date == _shanghai_date(submission.created_at),
                Checkin.deleted_at.is_(None),
            )
        )
        if checkin is None:
            result.skipped["no_same_day_checkin"] += 1
            continue

        result.submissions_matched += 1
        parsed = dict(checkin.parsed_json or {})
        custom = dict(parsed.get("custom_checkin") or {})
        changed = False
        for index, answer in answers:
            question = str(fields[index - 1].get("title") or fields[index - 1].get("label") or "").strip()
            if not question:
                result.skipped["blank_execution_item"] += 1
                continue
            item_id = f"document:{document.id}:{index}"
            existing = custom.get(item_id)
            if isinstance(existing, dict) and str(existing.get("answer") or "").strip():
                result.skipped["existing_custom_answer"] += 1
                continue
            result.answers_planned += 1
            if apply:
                custom[item_id] = {
                    "question": question,
                    "answer": answer,
                    "source": "historical_h5",
                    "questionnaire_submission_id": submission.id,
                }
                changed = True
                result.answers_written += 1
        if apply and changed:
            parsed["custom_checkin"] = custom
            checkin.parsed_json = parsed
    return result
