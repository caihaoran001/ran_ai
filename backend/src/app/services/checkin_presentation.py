"""Shared, compact views of generic and personalized check-in data for the reply path."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from app.models.user_document import DocumentType, UserDocument
from app.services.intake_generation import personalized_checkin_fields_from_document_payloads


_DOCUMENT_ITEM_ID = re.compile(r"^document:(\d+):(\d+)$")


def personalized_checkin_document_id(item_id: object) -> int | None:
    match = _DOCUMENT_ITEM_ID.match(str(item_id))
    return int(match.group(1)) if match else None


def personalized_checkin_document_ids(parsed_json: dict[str, Any] | None) -> set[int]:
    """Return execution-document ids referenced by a check-in's dynamic answers."""
    if not isinstance(parsed_json, dict):
        return set()
    answers = parsed_json.get("custom_checkin")
    if not isinstance(answers, dict):
        return set()
    ids: set[int] = set()
    for item_id in answers:
        document_id = personalized_checkin_document_id(item_id)
        if document_id is not None:
            ids.add(document_id)
    return ids


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
