from __future__ import annotations

import json
from datetime import date
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.document_service import _generate_stage_payload
from tests.conftest import seed_checkins, seed_chat_messages


@pytest.mark.asyncio
async def test_stage_report_generation_all_stages(admin_client, create_case, db_session):
    await _ensure_manual_review(admin_client)

    group_id = f"test-04a-{uuid4().hex[:8]}"
    create_resp = await create_case(
        {
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
async def test_stage_report_no_duplicate_generation(admin_client, create_case, db_session):
    await _ensure_manual_review(admin_client)

    group_id = f"test-04b-{uuid4().hex[:8]}"
    create_resp = await create_case(
        {
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
async def test_stage_report_content_has_checkin_data(admin_client, create_case, db_session):
    await _ensure_manual_review(admin_client)

    group_id = f"test-04c-{uuid4().hex[:8]}"
    create_resp = await create_case(
        {
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
async def test_stage_report_retries_when_llm_fields_missing():
    ai = _SequenceAI(
        [
            json.dumps({"summary": "字段不对"}, ensure_ascii=False),
            json.dumps(
                {
                    "stage_summary": "本阶段执行稳定，体重和饮水都有真实记录。",
                    "risk_reminder": "暂无明显不适，继续观察。",
                    "adjustment_advice": "饮水稳定到1500ml以上，23点前准备休息。",
                    "followup_advice": "先稳住本阶段节奏，再把饮水和睡眠连续性做实。",
                },
                ensure_ascii=False,
            ),
        ]
    )

    payload = await _generate_stage_payload(
        ai=ai,
        service_case=SimpleNamespace(id=1, case_no="TEST-STAGE-RETRY"),
        user_name="测试用户",
        stage="day14",
        title="14天阶段报告",
        latest_profile=None,
        checkins=[_fake_checkin()],
        messages=[],
    )

    assert ai.calls == 2
    assert payload["stage_summary"].startswith("本阶段执行稳定")
    assert "__degraded__" not in payload


@pytest.mark.asyncio
async def test_stage_report_includes_current_archivist_state():
    ai = _SequenceAI(
        [
            json.dumps(
                {
                    "stage_summary": "本阶段执行稳定，已根据最近反馈调整重点。",
                    "risk_reminder": "暂无明显不适，继续观察。",
                    "adjustment_advice": "晚间饮用按当前重点连续完成5天。",
                    "followup_advice": "先稳住本阶段节奏，再复盘执行反馈。",
                },
                ensure_ascii=False,
            )
        ]
    )

    await _generate_stage_payload(
        ai=ai,
        service_case=SimpleNamespace(id=1, case_no="TEST-STAGE-STATE"),
        user_name="测试用户",
        stage="day14",
        title="14天阶段报告",
        latest_profile=None,
        checkins=[_fake_checkin()],
        messages=[],
        state_json={
            "domains": {
                "action_focus": {
                    "label": "当前行动重点",
                    "current": {
                        "value": {"current_focus": {"label": "晚间执行", "action": "连续完成晚A"}},
                        "evidence": "用户反馈晚间容易漏做",
                        "updated_at": "2026-07-14T09:30:00+00:00",
                    },
                    "history": [{"evidence": "不应进入阶段报告上下文"}],
                }
            }
        },
    )

    prompt = ai.requests[0]["user_prompt"]
    assert "档案官当前状态" in prompt
    assert "连续完成晚A" in prompt
    assert "不应进入阶段报告上下文" not in prompt


@pytest.mark.asyncio
async def test_stage_report_marks_degraded_after_unusable_retries():
    ai = _SequenceAI([json.dumps({"summary": "字段不对"}, ensure_ascii=False)] * 3)

    payload = await _generate_stage_payload(
        ai=ai,
        service_case=SimpleNamespace(id=1, case_no="TEST-STAGE-DEGRADED"),
        user_name="测试用户",
        stage="day14",
        title="14天阶段报告",
        latest_profile=None,
        checkins=[_fake_checkin()],
        messages=[],
    )

    assert ai.calls == 3
    assert payload["__degraded__"] is True
    assert "最新反馈" in payload["stage_summary"]


@pytest.mark.asyncio
async def test_stage_report_retries_when_non_graduation_has_commercial_terms():
    ai = _SequenceAI(
        [
            json.dumps(
                {
                    "stage_summary": "本阶段执行稳定，体重和饮水都有真实记录。",
                    "risk_reminder": "暂无明显不适，继续观察。",
                    "adjustment_advice": "饮水稳定到1500ml以上，23点前准备休息。",
                    "followup_advice": "可以提前考虑复购，避免节奏中断。",
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "stage_summary": "本阶段执行稳定，体重和饮水都有真实记录。",
                    "risk_reminder": "暂无明显不适，继续观察。",
                    "adjustment_advice": "饮水稳定到1500ml以上，23点前准备休息。",
                    "followup_advice": "先稳住本阶段节奏，再把饮水和睡眠连续性做实。",
                },
                ensure_ascii=False,
            ),
        ]
    )

    payload = await _generate_stage_payload(
        ai=ai,
        service_case=SimpleNamespace(id=1, case_no="TEST-STAGE-COMMERCIAL"),
        user_name="测试用户",
        stage="day14",
        title="14天阶段报告",
        latest_profile=None,
        checkins=[_fake_checkin()],
        messages=[],
    )

    assert ai.calls == 2
    assert "复购" not in json.dumps(payload, ensure_ascii=False)


@pytest.mark.asyncio
async def test_graduation_report(admin_client, create_case, db_session):
    await _ensure_manual_review(admin_client)

    group_id = f"test-04d-{uuid4().hex[:8]}"
    create_resp = await create_case(
        {
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


class _SequenceAI:
    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls = 0
        self.requests: list[dict] = []

    async def generate_text(self, **kwargs):
        self.calls += 1
        self.requests.append(kwargs)
        return self._responses[min(self.calls - 1, len(self._responses) - 1)]


def _fake_checkin():
    return SimpleNamespace(
        checkin_date=date(2026, 6, 28),
        created_at=datetime(2026, 6, 28, tzinfo=timezone.utc),
        content="晨起体重（kg）: 62.10\n今日饮水量: 1500-2000ml",
        parsed_json={"weight": "62.10", "water": "1500-2000ml"},
    )
