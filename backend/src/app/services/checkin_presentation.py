"""Shared, compact views of generic and personalized check-in data for the reply path."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.checkin_checklist_version import CheckinChecklistVersion
from app.models.user_document import DocumentType, UserDocument
from app.models.user_profile import UserProfile
from app.services.checkin_questionnaires import DEFAULT_PERSONALIZED_CHECKIN_FIELDS
from app.services.intake_generation import personalized_checkin_fields_from_document_payloads


_DOCUMENT_ITEM_ID = re.compile(r"^document:(\d+):(\d+)$")
_CHECKLIST_ITEM_ID = re.compile(r"^checklist:(\d+):(\d+)$")
_PROFILE_ITEM_ID = re.compile(r"^profile:(\d+):(\d+)$")


def personalized_checkin_item_ids(parsed_json: dict[str, Any] | None) -> set[str]:
    if not isinstance(parsed_json, dict):
        return set()
    answers = parsed_json.get("custom_checkin")
    if not isinstance(answers, dict):
        return set()
    return {str(item_id) for item_id in answers}


def personalized_checkin_labels(documents: Iterable[UserDocument]) -> dict[str, str]:
    """Resolve durable document-item ids to the five labels the user was asked."""
    labels: dict[str, str] = {}
    for document in documents:
        if document.document_type != DocumentType.DAY28_EXECUTION_STANDARD:
            continue
        if not isinstance(document.content_json, dict):
            continue
        fields = personalized_checkin_fields_from_document_payloads(
            {DocumentType.DAY28_EXECUTION_STANDARD.value: document.content_json}
        )
        for index, field in enumerate(fields[:5], start=1):
            label = str(field.get("title") or field.get("label") or "").strip()
            if label:
                labels[f"document:{document.id}:{index}"] = label
    return labels


async def load_personalized_checkin_labels(
    session: AsyncSession,
    *,
    service_case_id: int,
    item_ids: Iterable[str],
) -> dict[str, str]:
    ids = {str(item_id) for item_id in item_ids}
    document_ids = {
        int(match.group(1))
        for item_id in ids
        if (match := _DOCUMENT_ITEM_ID.match(item_id))
    }
    version_ids = {
        int(match.group(1))
        for item_id in ids
        if (match := _CHECKLIST_ITEM_ID.match(item_id))
    }
    profile_ids = {
        int(match.group(1))
        for item_id in ids
        if (match := _PROFILE_ITEM_ID.match(item_id))
    }
    documents = (
        list(
            await session.scalars(
                select(UserDocument).where(
                    UserDocument.service_case_id == service_case_id,
                    UserDocument.id.in_(document_ids),
                )
            )
        )
        if document_ids
        else []
    )
    labels = personalized_checkin_labels(documents)

    versions = (
        list(
            await session.scalars(
                select(CheckinChecklistVersion).where(
                    CheckinChecklistVersion.service_case_id == service_case_id,
                    CheckinChecklistVersion.id.in_(version_ids),
                )
            )
        )
        if version_ids
        else []
    )
    for version in versions:
        for index, item in enumerate(version.items_json[:5], start=1):
            label = (
                str(item.get("text") or "").strip()
                if isinstance(item, dict)
                else str(item).strip()
            )
            if label:
                labels[f"checklist:{version.id}:{index}"] = label

    profiles = (
        list(
            await session.scalars(
                select(UserProfile).where(
                    UserProfile.service_case_id == service_case_id,
                    UserProfile.id.in_(profile_ids),
                )
            )
        )
        if profile_ids
        else []
    )
    for profile in profiles:
        fields = (
            profile.profile_json.get("personalized_checkin_fields")
            if isinstance(profile.profile_json, dict)
            else []
        )
        for index, field in enumerate((fields or [])[:5], start=1):
            label = (
                str(field.get("title") or field.get("label") or "").strip()
                if isinstance(field, dict)
                else ""
            )
            if label:
                labels[f"profile:{profile.id}:{index}"] = label

    for index, field in enumerate(DEFAULT_PERSONALIZED_CHECKIN_FIELDS[:5], start=1):
        label = str(field.get("title") or field.get("label") or "").strip()
        if label:
            labels[f"default:v1:{index}"] = label
    return labels


def personalized_checkin_answers(
    parsed_json: dict[str, Any] | None,
    *,
    labels_by_id: Mapping[str, str],
) -> list[dict[str, str]]:
    """Return answered dynamic checklist items with their human-readable labels."""
    if not isinstance(parsed_json, dict):
        return []
    stored = parsed_json.get("custom_checkin")
    if not isinstance(stored, dict):
        return []

    items: list[dict[str, str]] = []
    for item_id, value in stored.items():
        answer = str(value.get("answer") or "").strip() if isinstance(value, dict) else str(value or "").strip()
        if not answer:
            continue
        item_id_text = str(item_id)
        match = _DOCUMENT_ITEM_ID.match(item_id_text)
        fallback = f"专属第 {match.group(2)} 项" if match else "专属执行项"
        question = str(value.get("question") or "").strip() if isinstance(value, dict) else ""
        items.append(
            {
                "item_id": item_id_text,
                "label": question or labels_by_id.get(item_id_text, fallback),
                "answer": answer,
            }
        )
    return items
