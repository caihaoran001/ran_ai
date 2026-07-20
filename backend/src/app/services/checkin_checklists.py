from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.checkin_checklist_version import CheckinChecklistVersion
from app.models.message import Message
from app.models.service_case import ServiceCase
from app.models.user_document import DocumentStatus, DocumentType, UserDocument
from app.models.user_profile import UserProfile
from app.services.checkin_questionnaires import DEFAULT_PERSONALIZED_CHECKIN_FIELDS
from app.services.intake_generation import personalized_checkin_fields_from_document_payloads
from app.services.service_case_queries import select_active_service_cases


CHECKLIST_SIZE = 5
MAX_QUESTION_CHARS = 200
TRACKING_PRODUCT_SCHEDULE = "product_schedule"


@dataclass(frozen=True)
class DailyChecklist:
    source_type: str
    source_ref: str
    items: list[dict[str, str]]
    version_no: int | None = None
    source_document_id: int | None = None

    def resolved_items(self) -> list[dict[str, str]]:
        return [
            {
                "id": f"{self.source_ref}:{index}",
                "label": item["text"],
                **(
                    {"tracking_kind": item["tracking_kind"]}
                    if item.get("tracking_kind")
                    else {}
                ),
            }
            for index, item in enumerate(self.items, start=1)
        ]


def _clean_question(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def normalize_manual_checklist_items(values: Iterable[object]) -> list[dict[str, str]]:
    questions = [_clean_question(value) for value in values]
    if len(questions) != CHECKLIST_SIZE:
        raise ValueError("五项追问必须正好提供 5 项。")
    if any(not question for question in questions):
        raise ValueError("五项追问不能包含空内容。")
    if any(len(question) > MAX_QUESTION_CHARS for question in questions):
        raise ValueError(f"每项追问不能超过 {MAX_QUESTION_CHARS} 个字符。")
    normalized = [
        re.sub(r"[\s，,。！？!?；;：:]+", "", question).casefold()
        for question in questions
    ]
    if len(set(normalized)) != CHECKLIST_SIZE:
        raise ValueError("五项追问不能有重复内容。")
    return [{"text": question} for question in questions]


def _snapshot_items(raw: object) -> list[dict[str, str]]:
    if not isinstance(raw, list) or len(raw) != CHECKLIST_SIZE:
        return []
    items: list[dict[str, str]] = []
    for value in raw:
        if isinstance(value, dict):
            text = _clean_question(value.get("text"))
            tracking_kind = _clean_question(value.get("tracking_kind"))
        else:
            text = _clean_question(value)
            tracking_kind = ""
        if not text:
            return []
        item = {"text": text}
        if tracking_kind:
            item["tracking_kind"] = tracking_kind
        items.append(item)
    return items


def _items_from_fields(
    fields: object, *, track_first_product_schedule: bool
) -> list[dict[str, str]]:
    if not isinstance(fields, list) or len(fields) < CHECKLIST_SIZE:
        return []
    questions = [
        _clean_question(field.get("title") or field.get("label"))
        if isinstance(field, dict)
        else _clean_question(field)
        for field in fields[:CHECKLIST_SIZE]
    ]
    try:
        items = normalize_manual_checklist_items(questions)
    except ValueError:
        return []
    if track_first_product_schedule:
        items[0]["tracking_kind"] = TRACKING_PRODUCT_SCHEDULE
    return items


async def get_system_daily_checklist(
    session: AsyncSession,
    *,
    service_case_id: int,
    sent_documents_only: bool = True,
) -> DailyChecklist:
    document_conditions = [
        UserDocument.service_case_id == service_case_id,
        UserDocument.document_type == DocumentType.DAY28_EXECUTION_STANDARD,
    ]
    if sent_documents_only:
        document_conditions.append(UserDocument.status == DocumentStatus.SENT)
    document_query = (
        select(UserDocument)
        .where(*document_conditions)
        .limit(1)
    )
    document_query = (
        document_query.order_by(UserDocument.sent_at.desc(), UserDocument.id.desc())
        if sent_documents_only
        else document_query.order_by(UserDocument.created_at.desc(), UserDocument.id.desc())
    )
    document = await session.scalar(document_query)
    if document is not None and isinstance(document.content_json, dict):
        fields = personalized_checkin_fields_from_document_payloads(
            {DocumentType.DAY28_EXECUTION_STANDARD.value: document.content_json}
        )
        items = _items_from_fields(fields, track_first_product_schedule=True)
        if items:
            return DailyChecklist(
                source_type=(
                    "sent_document" if sent_documents_only else "latest_document_preview"
                ),
                source_ref=f"document:{document.id}",
                items=items,
                source_document_id=document.id,
            )

    profile = await session.scalar(
        select(UserProfile)
        .where(UserProfile.service_case_id == service_case_id)
        .order_by(UserProfile.created_at.desc(), UserProfile.id.desc())
        .limit(1)
    )
    if profile is not None and isinstance(profile.profile_json, dict):
        fields = profile.profile_json.get("personalized_checkin_fields")
        first_field = fields[0] if isinstance(fields, list) and fields else {}
        first_key = (
            str(first_field.get("key") or first_field.get("id") or "")
            if isinstance(first_field, dict)
            else ""
        )
        items = _items_from_fields(
            fields,
            track_first_product_schedule=first_key == "personalized_checkin_1",
        )
        if items:
            return DailyChecklist(
                source_type="generated_profile",
                source_ref=f"profile:{profile.id}",
                items=items,
            )

    items = _items_from_fields(
        DEFAULT_PERSONALIZED_CHECKIN_FIELDS,
        track_first_product_schedule=False,
    )
    if not items:
        raise RuntimeError("默认五项追问配置无效。")
    return DailyChecklist(
        source_type="safe_default",
        source_ref="default:v1",
        items=items,
    )


async def get_effective_daily_checklist(
    session: AsyncSession,
    *,
    service_case_id: int,
    sent_documents_only: bool = True,
) -> DailyChecklist:
    version = await session.scalar(
        select(CheckinChecklistVersion)
        .where(CheckinChecklistVersion.service_case_id == service_case_id)
        .order_by(
            CheckinChecklistVersion.version_no.desc(),
            CheckinChecklistVersion.id.desc(),
        )
        .limit(1)
    )
    if version is not None:
        items = _snapshot_items(version.items_json)
        if items:
            return DailyChecklist(
                source_type=version.source_type,
                source_ref=f"checklist:{version.id}",
                items=items,
                version_no=version.version_no,
                source_document_id=version.source_document_id,
            )
    return await get_system_daily_checklist(
        session,
        service_case_id=service_case_id,
        sent_documents_only=sent_documents_only,
    )


async def create_daily_checklist_version(
    session: AsyncSession,
    *,
    service_case_id: int,
    items: list[dict[str, str]],
    source_type: str,
    source_message: Message,
    source_document_id: int | None = None,
) -> CheckinChecklistVersion:
    service_case = await session.scalar(
        select_active_service_cases(ServiceCase.id == service_case_id).with_for_update()
    )
    if service_case is None or service_case.group_id != source_message.group_id:
        raise ValueError("当前消息不属于有效的本群服务单。")
    existing = await session.scalar(
        select(CheckinChecklistVersion).where(
            CheckinChecklistVersion.source_message_id == source_message.id
        )
    )
    if existing is not None:
        existing_items = _snapshot_items(existing.items_json)
        if existing.source_type != source_type or existing_items != items:
            raise ValueError("本条消息已经执行过另一项五项清单修改，不能重复改写。")
        return existing
    latest = await session.scalar(
        select(CheckinChecklistVersion)
        .where(CheckinChecklistVersion.service_case_id == service_case_id)
        .order_by(CheckinChecklistVersion.version_no.desc())
        .limit(1)
    )
    analysis = source_message.analysis_json if isinstance(source_message.analysis_json, dict) else {}
    meta = analysis.get("_qiwe_meta") if isinstance(analysis.get("_qiwe_meta"), dict) else {}
    version = CheckinChecklistVersion(
        service_case_id=service_case_id,
        version_no=(latest.version_no if latest is not None else 0) + 1,
        items_json=[dict(item) for item in items],
        source_type=source_type,
        source_message_id=source_message.id,
        source_document_id=source_document_id,
        changed_by_id=_clean_question(meta.get("sender_id")) or None,
        changed_by_name=_clean_question(meta.get("sender_name")) or None,
    )
    session.add(version)
    await session.flush()
    return version


async def set_manual_daily_checklist(
    session: AsyncSession,
    *,
    service_case_id: int,
    questions: Iterable[object],
    source_message: Message,
) -> DailyChecklist:
    items = normalize_manual_checklist_items(questions)
    version = await create_daily_checklist_version(
        session,
        service_case_id=service_case_id,
        items=items,
        source_type="staff_override",
        source_message=source_message,
    )
    return DailyChecklist(
        source_type=version.source_type,
        source_ref=f"checklist:{version.id}",
        items=_snapshot_items(version.items_json),
        version_no=version.version_no,
    )


async def restore_system_daily_checklist(
    session: AsyncSession,
    *,
    service_case_id: int,
    source_message: Message,
) -> DailyChecklist:
    system = await get_system_daily_checklist(session, service_case_id=service_case_id)
    version = await create_daily_checklist_version(
        session,
        service_case_id=service_case_id,
        items=system.items,
        source_type="system_default",
        source_message=source_message,
        source_document_id=system.source_document_id,
    )
    return DailyChecklist(
        source_type=version.source_type,
        source_ref=f"checklist:{version.id}",
        items=_snapshot_items(version.items_json),
        version_no=version.version_no,
        source_document_id=system.source_document_id,
    )
