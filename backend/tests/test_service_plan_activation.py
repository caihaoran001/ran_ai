from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user_document import DocumentStatus, DocumentType, UserDocument
from app.models.checkin_reward import CheckinRewardProgram
from app.services import document_delivery
from app.services.document_delivery import send_one_document
from app.services.reminder_service import ReminderService
from app.services.service_case_activation import (
    activate_service_case_if_due,
    set_pre_service_status_or_activate,
)


TZ = ZoneInfo(get_settings().reminder_timezone)


def _case(*, status: ServiceCaseStatus, plan: date | None) -> ServiceCase:
    token = uuid4().hex
    return ServiceCase(
        case_no=f"PLAN-{token[:10]}",
        group_id=f"group-{token[:10]}",
        questionnaire_token=token,
        status=status,
        service_plan_start_date=plan,
    )


def test_due_plan_activates_without_delivery_documents():
    service_case = _case(status=ServiceCaseStatus.REVIEWING, plan=date(2026, 7, 20))

    changed = activate_service_case_if_due(
        service_case,
        now=datetime(2026, 7, 20, 8, 30, tzinfo=TZ),
        tz=TZ,
    )

    assert changed is True
    assert service_case.status == ServiceCaseStatus.IN_SERVICE
    assert service_case.started_at == datetime(2026, 7, 20, 0, 0, tzinfo=TZ)


def test_due_plan_activates_created_case_without_questionnaire():
    service_case = _case(status=ServiceCaseStatus.CREATED, plan=date(2026, 7, 20))

    changed = activate_service_case_if_due(
        service_case,
        now=datetime(2026, 7, 20, 8, 30, tzinfo=TZ),
        tz=TZ,
    )

    assert changed is True
    assert service_case.status == ServiceCaseStatus.IN_SERVICE
    assert service_case.started_at == datetime(2026, 7, 20, 0, 0, tzinfo=TZ)


def test_document_generation_status_does_not_regress_active_service():
    service_case = _case(status=ServiceCaseStatus.IN_SERVICE, plan=date(2026, 7, 20))
    original_start = datetime(2026, 7, 20, 0, 0, tzinfo=TZ)
    service_case.started_at = original_start

    changed = set_pre_service_status_or_activate(
        service_case,
        ServiceCaseStatus.REVIEWING,
        now=datetime(2026, 7, 21, 12, 0, tzinfo=TZ),
        tz=TZ,
    )

    assert changed is False
    assert service_case.status == ServiceCaseStatus.IN_SERVICE
    assert service_case.started_at == original_start


@pytest.mark.asyncio
async def test_scheduler_activates_due_reviewing_case(db_session, mock_session_local):
    service_case = _case(
        status=ServiceCaseStatus.REVIEWING,
        plan=date(2026, 7, 20),
    )
    db_session.add(service_case)
    await db_session.flush()

    await ReminderService(get_settings()).check_service_starts_once(
        now=datetime(2026, 7, 20, 0, 1, tzinfo=TZ)
    )

    await db_session.refresh(service_case)
    assert service_case.status == ServiceCaseStatus.IN_SERVICE
    assert service_case.started_at == datetime(2026, 7, 20, 0, 0, tzinfo=TZ)


@pytest.mark.asyncio
async def test_scheduler_activates_due_created_case_without_questionnaire(
    db_session, mock_session_local
):
    service_case = _case(
        status=ServiceCaseStatus.CREATED,
        plan=date(2026, 7, 20),
    )
    db_session.add(service_case)
    await db_session.flush()

    await ReminderService(get_settings()).check_service_starts_once(
        now=datetime(2026, 7, 20, 0, 1, tzinfo=TZ)
    )

    await db_session.refresh(service_case)
    assert service_case.status == ServiceCaseStatus.IN_SERVICE
    assert service_case.started_at == datetime(2026, 7, 20, 0, 0, tzinfo=TZ)


@pytest.mark.asyncio
async def test_scheduler_aligns_legacy_active_start_to_plan_date(
    db_session, mock_session_local
):
    service_case = _case(
        status=ServiceCaseStatus.IN_SERVICE,
        plan=date(2026, 7, 20),
    )
    service_case.started_at = datetime(2026, 7, 22, 9, 0, tzinfo=TZ)
    db_session.add(service_case)
    await db_session.flush()

    await ReminderService(get_settings()).check_service_starts_once(
        now=datetime(2026, 7, 23, 8, 0, tzinfo=TZ)
    )

    await db_session.refresh(service_case)
    assert service_case.status == ServiceCaseStatus.IN_SERVICE
    assert service_case.started_at == datetime(2026, 7, 20, 0, 0, tzinfo=TZ)


@pytest.mark.asyncio
async def test_sending_document_no_longer_starts_service(
    db_session, monkeypatch, tmp_path
):
    service_case = _case(
        status=ServiceCaseStatus.REVIEWING,
        plan=datetime.now(TZ).date() + timedelta(days=1),
    )
    db_session.add(service_case)
    await db_session.flush()
    document = UserDocument(
        service_case_id=service_case.id,
        document_type=DocumentType.DAY28_EXECUTION_STANDARD,
        title="28天执行标准与打卡要求",
        content_md="内容",
        content_json={"blocks": []},
        status=DocumentStatus.APPROVED,
    )
    db_session.add(document)
    await db_session.flush()
    rendered = tmp_path / "document.docx"
    rendered.write_bytes(b"docx")

    async def fake_create_temporary_document_file(session, item):
        return rendered, lambda: None

    async def fake_send_channel_document(group_id, file_path, *, caption=None):
        return SimpleNamespace(success=True, message_id="sent-1", error=None)

    monkeypatch.setattr(
        document_delivery,
        "create_temporary_document_file",
        fake_create_temporary_document_file,
    )
    monkeypatch.setattr(
        document_delivery,
        "send_channel_document",
        fake_send_channel_document,
    )

    await send_one_document(db_session, document)

    assert document.status == DocumentStatus.SENT
    assert service_case.status == ServiceCaseStatus.REVIEWING
    assert service_case.started_at is None


@pytest.mark.asyncio
async def test_two_documents_sent_one_by_one_trigger_reward_intro_once(
    db_session, monkeypatch, tmp_path
):
    monkeypatch.setattr(
        document_delivery,
        "get_settings",
        lambda: SimpleNamespace(checkin_rewards_enabled=True),
    )
    service_case = _case(status=ServiceCaseStatus.REVIEWING, plan=date(2026, 8, 12))
    db_session.add(service_case)
    await db_session.flush()
    documents = [
        UserDocument(
            service_case_id=service_case.id,
            document_type=document_type,
            title=title,
            content_md="内容",
            content_json={
                "blocks": [],
                "personalized_checkin_fields": [
                    {"title": "今日是否完成早C/午B/晚A？", "key": "personalized_checkin_1"},
                    {"title": "餐食是否符合方案？"},
                    {"title": "饮水是否完成？"},
                    {"title": "运动是否完成？"},
                    {"title": "睡眠情况如何？"},
                ],
            },
            status=DocumentStatus.APPROVED,
        )
        for document_type, title in (
            (DocumentType.ENERGY_RESTART_PRESCRIPTION, "个人能量重启诊断报告"),
            (DocumentType.DAY28_EXECUTION_STANDARD, "28天高燃人生执行建议书"),
        )
    ]
    db_session.add_all(documents)
    await db_session.flush()
    rendered = tmp_path / "document.docx"
    rendered.write_bytes(b"docx")
    sent_texts: list[str] = []

    async def fake_create_temporary_document_file(session, item):
        return rendered, lambda: None

    async def fake_send_channel_document(group_id, file_path, *, caption=None):
        return SimpleNamespace(success=True, message_id=f"doc-{caption}", error=None)

    async def fake_send_channel_text(group_id, content):
        sent_texts.append(content)
        return SimpleNamespace(
            success=True,
            message_id="intro-1",
            error=None,
            delivery_uncertain=False,
        )

    monkeypatch.setattr(document_delivery, "create_temporary_document_file", fake_create_temporary_document_file)
    monkeypatch.setattr(document_delivery, "send_channel_document", fake_send_channel_document)
    monkeypatch.setattr(document_delivery, "send_channel_text", fake_send_channel_text)

    await send_one_document(db_session, documents[0])
    assert sent_texts == []
    await send_one_document(db_session, documents[1])
    await send_one_document(db_session, documents[1])

    program = await db_session.scalar(
        select(CheckinRewardProgram).where(
            CheckinRewardProgram.service_case_id == service_case.id
        )
    )
    assert len(sent_texts) == 1
    assert "1 管咖啡" in sent_texts[0]
    assert "1 盒钥匙" in sent_texts[0]
    assert "同一张图里同时有咖啡和饭也可以" in sent_texts[0]
    assert program.intro_status == "sent"
