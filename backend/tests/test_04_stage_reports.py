from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from tests.conftest import seed_checkins, seed_chat_messages


@pytest.mark.asyncio
async def test_stage_report_generation_all_stages(admin_client, db_session):
    await _ensure_manual_review(admin_client)

    group_id = f"test-04a-{uuid4().hex[:8]}"
    create_resp = await admin_client.post(
        "/service-cases",
        json={
            "group_id": group_id,
            "user": {"name": "阶段测试用户"},
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    case = create_resp.json()
    case_id = case["id"]
    token = case["questionnaire_token"]

    submit_resp = await admin_client.post(
        f"/questionnaires/{token}/submit",
        json={"answers": {"name": "阶段测试用户"}},
        params={"template": "intake"},
    )
    assert submit_resp.status_code == 201, submit_resp.text

    await seed_checkins(
        db_session,
        service_case_id=case_id,
        group_id=case["group_id"],
        user_id=case["user_id"],
        days=28,
    )
    await seed_chat_messages(
        db_session,
        service_case_id=case_id,
        group_id=case["group_id"],
    )
    await db_session.commit()

    started_at = datetime.now(timezone.utc) - timedelta(days=28)
    patch_resp = await admin_client.patch(
        f"/service-cases/{case_id}",
        json={"started_at": started_at.strftime("%Y-%m-%dT%H:%M:%SZ")},
    )
    assert patch_resp.status_code == 200, patch_resp.text

    stages = ["day3", "day7", "day14", "day21", "day28", "graduation"]
    for stage in stages:
        gen_resp = await admin_client.post(
            f"/service-cases/{case_id}/stage-reports/generate",
            params={"stage": stage},
        )
        assert gen_resp.status_code == 200, f"stage={stage}: {gen_resp.text}"
        doc = gen_resp.json()
        assert doc["service_case_id"] == case_id
        assert doc["document_type"] in ("stage_report", "graduation_report")

    docs_resp = await admin_client.get(f"/service-cases/{case_id}/documents")
    assert docs_resp.status_code == 200, docs_resp.text
    documents = docs_resp.json()
    assert len(documents) >= 6

    stage_docs = [d for d in documents if d["document_type"] in ("stage_report", "graduation_report")]
    assert len(stage_docs) >= 6


@pytest.mark.asyncio
async def test_stage_report_no_duplicate_generation(admin_client, db_session):
    await _ensure_manual_review(admin_client)

    group_id = f"test-04b-{uuid4().hex[:8]}"
    create_resp = await admin_client.post(
        "/service-cases",
        json={
            "group_id": group_id,
            "user": {"name": "去重测试用户"},
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    case = create_resp.json()
    case_id = case["id"]
    token = case["questionnaire_token"]

    submit_resp = await admin_client.post(
        f"/questionnaires/{token}/submit",
        json={"answers": {"name": "去重测试用户"}},
        params={"template": "intake"},
    )
    assert submit_resp.status_code == 201, submit_resp.text

    await seed_checkins(
        db_session,
        service_case_id=case_id,
        group_id=case["group_id"],
        user_id=case["user_id"],
        days=28,
    )
    await seed_chat_messages(
        db_session,
        service_case_id=case_id,
        group_id=case["group_id"],
    )
    await db_session.commit()

    started_at = datetime.now(timezone.utc) - timedelta(days=28)
    patch_resp = await admin_client.patch(
        f"/service-cases/{case_id}",
        json={"started_at": started_at.strftime("%Y-%m-%dT%H:%M:%SZ")},
    )
    assert patch_resp.status_code == 200, patch_resp.text

    first_resp = await admin_client.post(
        f"/service-cases/{case_id}/stage-reports/generate",
        params={"stage": "day7"},
    )
    assert first_resp.status_code == 200, first_resp.text
    first_doc_id = first_resp.json()["id"]

    docs_before = await admin_client.get(f"/service-cases/{case_id}/documents")
    assert docs_before.status_code == 200
    count_before = len(docs_before.json())

    second_resp = await admin_client.post(
        f"/service-cases/{case_id}/stage-reports/generate",
        params={"stage": "day7"},
    )
    assert second_resp.status_code == 200, second_resp.text
    second_doc_id = second_resp.json()["id"]

    assert first_doc_id == second_doc_id

    docs_after = await admin_client.get(f"/service-cases/{case_id}/documents")
    assert docs_after.status_code == 200
    count_after = len(docs_after.json())

    assert count_after == count_before


@pytest.mark.asyncio
async def test_stage_report_content_has_checkin_data(admin_client, db_session):
    await _ensure_manual_review(admin_client)

    group_id = f"test-04c-{uuid4().hex[:8]}"
    create_resp = await admin_client.post(
        "/service-cases",
        json={
            "group_id": group_id,
            "user": {"name": "打卡数据测试用户"},
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    case = create_resp.json()
    case_id = case["id"]
    token = case["questionnaire_token"]

    submit_resp = await admin_client.post(
        f"/questionnaires/{token}/submit",
        json={"answers": {"name": "打卡数据测试用户"}},
        params={"template": "intake"},
    )
    assert submit_resp.status_code == 201, submit_resp.text

    await seed_checkins(
        db_session,
        service_case_id=case_id,
        group_id=case["group_id"],
        user_id=case["user_id"],
        days=28,
    )
    await seed_chat_messages(
        db_session,
        service_case_id=case_id,
        group_id=case["group_id"],
    )
    await db_session.commit()

    started_at = datetime.now(timezone.utc) - timedelta(days=28)
    patch_resp = await admin_client.patch(
        f"/service-cases/{case_id}",
        json={"started_at": started_at.strftime("%Y-%m-%dT%H:%M:%SZ")},
    )
    assert patch_resp.status_code == 200, patch_resp.text

    gen_resp = await admin_client.post(
        f"/service-cases/{case_id}/stage-reports/generate",
        params={"stage": "day7"},
    )
    assert gen_resp.status_code == 200, gen_resp.text
    doc = gen_resp.json()

    content_json = doc.get("content_json") or {}
    content_md = doc.get("content_md") or ""

    blocks = content_json.get("blocks") or []
    block_contents = " ".join(str(b.get("content", "")) for b in blocks)
    assert len(content_md) > 0
    assert "打卡" in content_md or "打卡" in block_contents or "checkin" in content_md.lower()

    assert content_json.get("stage") == "day7"
    assert doc.get("file_path") is None

    download_resp = await admin_client.get(f"/documents/{doc['id']}/download")
    assert download_resp.status_code == 200, download_resp.text
    assert download_resp.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert len(download_resp.content) > 1000


@pytest.mark.asyncio
async def test_graduation_report(admin_client, db_session):
    await _ensure_manual_review(admin_client)

    group_id = f"test-04d-{uuid4().hex[:8]}"
    create_resp = await admin_client.post(
        "/service-cases",
        json={
            "group_id": group_id,
            "user": {"name": "毕业报告测试用户"},
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    case = create_resp.json()
    case_id = case["id"]
    token = case["questionnaire_token"]

    submit_resp = await admin_client.post(
        f"/questionnaires/{token}/submit",
        json={"answers": {"name": "毕业报告测试用户"}},
        params={"template": "intake"},
    )
    assert submit_resp.status_code == 201, submit_resp.text

    await seed_checkins(
        db_session,
        service_case_id=case_id,
        group_id=case["group_id"],
        user_id=case["user_id"],
        days=28,
    )
    await seed_chat_messages(
        db_session,
        service_case_id=case_id,
        group_id=case["group_id"],
    )
    await db_session.commit()

    started_at = datetime.now(timezone.utc) - timedelta(days=28)
    patch_resp = await admin_client.patch(
        f"/service-cases/{case_id}",
        json={"started_at": started_at.strftime("%Y-%m-%dT%H:%M:%SZ")},
    )
    assert patch_resp.status_code == 200, patch_resp.text

    gen_resp = await admin_client.post(
        f"/service-cases/{case_id}/stage-reports/generate",
        params={"stage": "graduation"},
    )
    assert gen_resp.status_code == 200, gen_resp.text
    doc = gen_resp.json()

    assert doc["document_type"] == "graduation_report"

    content_json = doc.get("content_json") or {}
    assert content_json.get("stage") == "graduation"

    content_md = doc.get("content_md") or ""
    blocks = content_json.get("blocks") or []
    block_contents_by_key = {b.get("key", ""): str(b.get("content", "")) for b in blocks}

    assert "weight_change" in block_contents_by_key or "weight" in content_md.lower()
    assert "growth_summary" in block_contents_by_key or "成长" in content_md


async def _ensure_manual_review(admin_client):
    resp = await admin_client.post(
        "/settings/content-delivery-policy",
        json={"mode": "manual_review"},
    )
    assert resp.status_code == 200, resp.text
