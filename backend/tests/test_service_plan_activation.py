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
    service_case = _case(status=ServiceCaseStatus.REVIEWING, plan=date(2026, 8, 12))
    db_session.add(service_case)
    await db_session.flush()
    personalized_items = [
        {
            "order": 1,
            "category": "product",
            "title": "早C早餐后30分钟内、午B随午餐、晚A最晚睡前1小时是否按个案方案完成？",
            "answer_type": "yes_no",
            "options": ["是", "否"],
            "example_answer": "早C已完成；午B未完成；晚A按方案暂停",
        },
        {
            "order": 2,
            "category": "diet",
            "title": "蔬菜总量是否至少2拳、晚餐主食不超过1拳？",
            "answer_type": "yes_no",
            "options": ["是", "否"],
            "example_answer": "是，已完成",
        },
        {
            "order": 3,
            "category": "activity",
            "title": "是否完成7000步或30分钟快走？",
            "answer_type": "yes_no",
            "options": ["是", "否"],
            "example_answer": "是，已完成",
        },
        {
            "order": 4,
            "category": "sleep",
            "title": "23:30前上床后的睡眠时长（小时）",
            "answer_type": "number",
            "options": [],
            "example_answer": "7小时",
        },
        {
            "order": 5,
            "category": "state",
            "title": "午餐后困倦评分（1分很困、5分不困）",
            "answer_type": "score_1_5",
            "options": ["1", "2", "3", "4", "5"],
            "example_answer": "4分，饭后精神尚可",
        },
    ]
    documents = [
        UserDocument(
            service_case_id=service_case.id,
            document_type=document_type,
            title=title,
            content_md="内容",
            content_json={"blocks": (
                [{"key": "daily_checkin", "checkin_items": personalized_items}]
                if document_type == DocumentType.DAY28_EXECUTION_STANDARD
                else []
            )},
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
    for index, item in enumerate(personalized_items, start=1):
        assert f"{index}. {item['title']}｜{item['example_answer']}" in sent_texts[0]
    assert sent_texts[0].index(personalized_items[0]["title"]) < sent_texts[0].index(personalized_items[4]["title"])
    assert "产品：早C" not in sent_texts[0]
    assert "示例回答" not in sent_texts[0]
    assert "产品和餐食建议随手拍照发群里" in sent_texts[0]
    assert program.intro_status == "sent"


@pytest.mark.asyncio
async def test_bulk_send_uses_same_completion_trigger(db_session, monkeypatch, tmp_path):
    service_case = _case(status=ServiceCaseStatus.REVIEWING, plan=date(2026, 8, 12))
    service_case.execution_commitment = {
        "version": 1,
        "high_commitment_candidate": True,
        "standard_target": "28天减重5-7斤",
        "high_commitment_target": "28天向8-10斤推进",
        "execution_path": "standard",
        "status": "pending",
        "reviews": {},
    }
    db_session.add(service_case)
    await db_session.flush()
    items = [
        {
            "order": index,
            "category": "product" if index == 1 else ("activity" if index == 3 else "state"),
            "title": f"个性化原题{index}（条件{index * 10}分钟）",
            "answer_type": "text",
            "options": [],
            "example_answer": (
                "早C已完成；午B未完成；晚A按方案暂停" if index == 1 else f"已完成第{index}项"
            ),
        }
        for index in range(1, 6)
    ]
    documents = [
        UserDocument(
            service_case_id=service_case.id,
            document_type=document_type,
            title=title,
            content_md="内容",
            content_json={"blocks": (
                [{"key": "daily_checkin", "checkin_items": items}]
                if document_type == DocumentType.DAY28_EXECUTION_STANDARD
                else []
            )},
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
        return SimpleNamespace(success=True, message_id="intro-bulk", error=None, delivery_uncertain=False)

    monkeypatch.setattr(document_delivery, "create_temporary_document_file", fake_create_temporary_document_file)
    monkeypatch.setattr(document_delivery, "send_channel_document", fake_send_channel_document)
    monkeypatch.setattr(document_delivery, "send_channel_text", fake_send_channel_text)

    await document_delivery.send_case_documents(db_session, service_case.id)
    await document_delivery.send_case_documents(db_session, service_case.id)

    assert len(sent_texts) == 2  # 文件说明 + 合并后的打卡示例与执行承诺邀请，重复调用不重发
    assert sent_texts[1].count("【每日打卡】") == 1
    assert all(f"个性化原题{index}" in sent_texts[1] for index in range(1, 6))
    assert "高承诺档" in sent_texts[1]
    assert "完整打卡率需达到85%" in sent_texts[1]


@pytest.mark.asyncio
async def test_failed_merged_guide_retries_as_one_complete_message(
    db_session, monkeypatch
):
    service_case = _case(status=ServiceCaseStatus.REVIEWING, plan=date(2026, 8, 12))
    service_case.execution_commitment = {
        "version": 1,
        "high_commitment_candidate": True,
        "standard_target": "28天减重5-7斤",
        "high_commitment_target": "28天向8-10斤推进",
        "execution_path": "standard",
        "status": "pending",
        "reviews": {},
    }
    db_session.add(service_case)
    await db_session.flush()
    items = [
        {
            "order": index,
            "category": "product" if index == 1 else "state",
            "title": f"个性化原题{index}",
            "answer_type": "text",
            "options": [],
            "example_answer": f"已完成第{index}项",
        }
        for index in range(1, 6)
    ]
    db_session.add_all(
        [
            UserDocument(
                service_case_id=service_case.id,
                document_type=document_type,
                title=title,
                content_md="内容",
                content_json={
                    "blocks": (
                        [{"key": "daily_checkin", "checkin_items": items}]
                        if document_type == DocumentType.DAY28_EXECUTION_STANDARD
                        else []
                    )
                },
                status=DocumentStatus.SENT,
                sent_at=datetime.now(TZ),
            )
            for document_type, title in (
                (DocumentType.ENERGY_RESTART_PRESCRIPTION, "个人能量重启诊断报告"),
                (DocumentType.DAY28_EXECUTION_STANDARD, "28天高燃人生执行建议书"),
            )
        ]
    )
    await db_session.flush()
    sent_texts: list[str] = []

    async def fake_send_channel_text(group_id, content):
        sent_texts.append(content)
        if len(sent_texts) == 1:
            return SimpleNamespace(
                success=False,
                message_id=None,
                error="definite failure",
                delivery_uncertain=False,
            )
        return SimpleNamespace(
            success=True,
            message_id="merged-retry",
            error=None,
            delivery_uncertain=False,
        )

    monkeypatch.setattr(document_delivery, "send_channel_text", fake_send_channel_text)

    assert (
        await document_delivery._maybe_send_execution_commitment_invitation(
            db_session, service_case
        )
        is False
    )
    assert (
        await document_delivery._maybe_send_post_document_checkin_example(
            db_session, service_case
        )
        is False
    )
    assert len(sent_texts) == 1

    assert (
        await document_delivery._maybe_send_execution_commitment_invitation(
            db_session, service_case
        )
        is True
    )
    assert len(sent_texts) == 2
    assert all(text.count("【每日打卡】") == 1 for text in sent_texts)
    assert all("高承诺档" in text for text in sent_texts)
    assert sent_texts[0] == sent_texts[1]


@pytest.mark.asyncio
async def test_missing_example_answers_skip_message_without_blocking_files(
    db_session, monkeypatch, tmp_path, caplog
):
    service_case = _case(status=ServiceCaseStatus.REVIEWING, plan=date(2026, 8, 12))
    db_session.add(service_case)
    await db_session.flush()
    documents = [
        UserDocument(
            service_case_id=service_case.id,
            document_type=document_type,
            title=title,
            content_md="历史内容",
            content_json={"blocks": ([{
                "key": "daily_checkin",
                "checkin_items": [
                    {"order": index, "title": f"历史原题{index}"}
                    for index in range(1, 6)
                ],
            }] if document_type == DocumentType.DAY28_EXECUTION_STANDARD else [])},
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
        return SimpleNamespace(success=True, message_id="text", error=None, delivery_uncertain=False)

    monkeypatch.setattr(document_delivery, "create_temporary_document_file", fake_create_temporary_document_file)
    monkeypatch.setattr(document_delivery, "send_channel_document", fake_send_channel_document)
    monkeypatch.setattr(document_delivery, "send_channel_text", fake_send_channel_text)

    with caplog.at_level("WARNING", logger="app.services.document_delivery"):
        await document_delivery.send_case_documents(db_session, service_case.id)

    assert all(document.status == DocumentStatus.SENT for document in documents)
    assert len(sent_texts) == 1  # 仅文件发送前的固定说明，历史文档不补造打卡答案
    assert any("five generated items with answers required" in record.getMessage() for record in caplog.records)
