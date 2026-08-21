"""重新生成交付文件时，旧版「草稿/待审核」文件自动翻成 superseded（已替代）。

非人工决定：系统用新版本替代旧稿。已发送/归档的文件是终态，绝不被动。
待审队列 documents[] 对 content_json 带 {"degraded": true} 的文件补 degraded 标记。
"""

from __future__ import annotations

import secrets

import pytest
from sqlalchemy import select

from app.models.service_case import ServiceCase
from app.models.user_document import DocumentStatus, DocumentType, UserDocument

from tests.test_01_intake import INTAKE_SYSTEM_PROMPT_PREFIX, _make_intake_mock_response


async def _submit_intake(admin_client, create_case, cai_haoran_answers) -> int:
    """创建服务单并提交入营问卷，等待后台生成画像+两份交付文件。"""
    group_id = f"test-group-supersede-{secrets.token_hex(6)}"
    resp = await create_case({
        "group_id": group_id,
        "user": {"name": "蔡浩然"},
    })
    assert resp.status_code == 201, resp.text
    case_id = resp.json()["id"]
    submit_resp = await admin_client.post(
        f"/questionnaires/{resp.json()['questionnaire_token']}/submit?template=intake",
        json={"answers": cai_haoran_answers},
    )
    assert submit_resp.status_code == 201, submit_resp.text
    return case_id


async def _latest_documents(db_session, case_id) -> dict[DocumentType, UserDocument]:
    documents = list(
        await db_session.scalars(
            select(UserDocument)
            .where(UserDocument.service_case_id == case_id)
            .order_by(UserDocument.created_at.asc())
        )
    )
    return {document.document_type: document for document in documents}


@pytest.mark.asyncio
async def test_regeneration_supersedes_old_reviewing_delivery_documents(
    admin_client, create_case, db_session, mock_ai, cai_haoran_answers, wait_background
):
    """重新生成后：旧 REVIEWING 交付文件变 superseded，新文件为 reviewing。"""
    mock_ai._responses = {
        INTAKE_SYSTEM_PROMPT_PREFIX: _make_intake_mock_response(),
    }
    case_id = await _submit_intake(admin_client, create_case, cai_haoran_answers)
    await wait_background()

    # 首次生成的交付文件处于待审核。
    first = await _latest_documents(db_session, case_id)
    assert set(first.keys()) == {
        DocumentType.ENERGY_RESTART_PRESCRIPTION,
        DocumentType.DAY28_EXECUTION_STANDARD,
    }
    assert all(doc.status == DocumentStatus.REVIEWING for doc in first.values())

    # 手动重新生成，旧稿应被翻成 superseded，新稿为 reviewing。
    gen_resp = await admin_client.post(f"/service-cases/{case_id}/generate-documents")
    assert gen_resp.status_code == 200, gen_resp.text
    await wait_background()

    all_docs = list(
        await db_session.scalars(
            select(UserDocument)
            .where(UserDocument.service_case_id == case_id)
            .order_by(UserDocument.created_at.asc())
        )
    )
    assert len(all_docs) == 4
    old = [doc for doc in all_docs if doc.id in {first[DocumentType.ENERGY_RESTART_PRESCRIPTION].id, first[DocumentType.DAY28_EXECUTION_STANDARD].id}]
    new = [doc for doc in all_docs if doc.id not in {first[DocumentType.ENERGY_RESTART_PRESCRIPTION].id, first[DocumentType.DAY28_EXECUTION_STANDARD].id}]
    assert all(doc.status == DocumentStatus.SUPERSEDED for doc in old), (
        "旧版待审核交付文件应被自动标记为 superseded"
    )
    assert all(doc.status == DocumentStatus.REVIEWING for doc in new), (
        "新生成的交付文件应处于待审核"
    )
    # 同类型新稿与原稿并存：每个交付类型各有一个 superseded 旧稿 + 一个 reviewing 新稿。
    for document_type in (DocumentType.ENERGY_RESTART_PRESCRIPTION, DocumentType.DAY28_EXECUTION_STANDARD):
        by_type = [doc for doc in all_docs if doc.document_type == document_type]
        assert sorted(doc.status.value for doc in by_type) == ["reviewing", "superseded"]


@pytest.mark.asyncio
async def test_regeneration_never_touches_sent_documents(
    admin_client, create_case, db_session, mock_ai, cai_haoran_answers, wait_background
):
    """旧 SENT 文档保持 sent，不被动；只有草稿/待审核旧稿被替代。"""
    mock_ai._responses = {
        INTAKE_SYSTEM_PROMPT_PREFIX: _make_intake_mock_response(),
    }
    case_id = await _submit_intake(admin_client, create_case, cai_haoran_answers)
    await wait_background()

    first = await _latest_documents(db_session, case_id)
    # 把其中一份（执行单）标记为已发送，另一份保持待审核。
    first[DocumentType.DAY28_EXECUTION_STANDARD].status = DocumentStatus.SENT
    await db_session.commit()
    await db_session.refresh(first[DocumentType.DAY28_EXECUTION_STANDARD])

    gen_resp = await admin_client.post(f"/service-cases/{case_id}/generate-documents")
    assert gen_resp.status_code == 200, gen_resp.text
    await wait_background()

    all_docs = list(
        await db_session.scalars(
            select(UserDocument)
            .where(UserDocument.service_case_id == case_id)
            .order_by(UserDocument.created_at.asc())
        )
    )
    sent_id = first[DocumentType.DAY28_EXECUTION_STANDARD].id
    reviewing_id = first[DocumentType.ENERGY_RESTART_PRESCRIPTION].id
    by_id = {document.id: document for document in all_docs}

    # 已发送的执行单保持 sent；待审核的诊断报告被替代。
    assert by_id[sent_id].status == DocumentStatus.SENT
    assert by_id[reviewing_id].status == DocumentStatus.SUPERSEDED
    # 新生成的执行单应为 reviewing（旧版 sent 不阻碍再次生成新版本）。
    new_execution = [
        doc
        for doc in all_docs
        if doc.document_type == DocumentType.DAY28_EXECUTION_STANDARD and doc.id != sent_id
    ]
    assert len(new_execution) == 1
    assert new_execution[0].status == DocumentStatus.REVIEWING


@pytest.mark.asyncio
async def test_pending_documents_marks_degraded(
    admin_client, create_case, db_session, mock_ai, cai_haoran_answers, wait_background
):
    """待审队列对 content_json 带 {"degraded": true} 的文档返回 degraded=true。"""
    mock_ai._responses = {
        INTAKE_SYSTEM_PROMPT_PREFIX: _make_intake_mock_response(),
    }
    case_id = await _submit_intake(admin_client, create_case, cai_haoran_answers)
    await wait_background()

    docs = list(
        await db_session.scalars(
            select(UserDocument).where(UserDocument.service_case_id == case_id)
        )
    )
    assert docs
    # 给其中一份文档打上降级标记，模拟阶段报告式降级兜底内容。
    content_json = dict(docs[0].content_json or {})
    content_json["degraded"] = True
    docs[0].content_json = content_json
    await db_session.commit()

    resp = await admin_client.get("/dashboard/pending-documents")
    assert resp.status_code == 200, resp.text
    degraded_ids = {
        document["document_id"]
        for group in resp.json()
        if group["case"]["service_case_id"] == case_id
        for document in group["documents"]
    }
    assert docs[0].id in degraded_ids, "带 degraded 标记的文件必须出现在待审队列"
    matched = [
        document
        for group in resp.json()
        if group["case"]["service_case_id"] == case_id
        for document in group["documents"]
        if document["document_id"] == docs[0].id
    ]
    assert matched and matched[0]["degraded"] is True
    # 未打标记的文件 degraded 应为 False（默认值）。
    untouched = [document for document in docs if document.id != docs[0].id]
    for document in untouched:
        found = [
            item
            for group in resp.json()
            if group["case"]["service_case_id"] == case_id
            for item in group["documents"]
            if item["document_id"] == document.id
        ]
        assert found and found[0]["degraded"] is False
