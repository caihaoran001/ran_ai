import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from app.models.checkin import Checkin
from app.models.questionnaire import QuestionnaireSubmission
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user import User
from app.models.user_document import DocumentStatus, DocumentType, UserDocument
from app.services.personalized_checkin_backfill import backfill_personalized_h5_checkins
from tests.conftest import seed_checkins


@pytest.mark.asyncio
async def test_create_single_checkin(admin_client, create_case, public_client, cai_haoran_answers):
    gid = f"test-checkin-create-{uuid.uuid4().hex[:8]}"

    create_resp = await create_case({
        "group_id": gid,
        "user": {"name": "Test User", "phone": "13800000001"},
    })
    assert create_resp.status_code == 201
    case = create_resp.json()
    case_id = case["id"]
    token = case["questionnaire_token"]

    submit_resp = await public_client.post(
        f"/questionnaires/{token}/submit",
        params={"template": "intake"},
        json={"answers": cai_haoran_answers},
    )
    assert submit_resp.status_code == 201

    checkin_resp = await admin_client.post(
        f"/service-cases/{case_id}/checkins",
        json={
            "checkin_date": "2026-05-01",
            "content": "打卡D1: wt65.0 饮水达标 睡眠6h 情绪3 运动完成",
            "parsed_json": {
                "weight": "65.0",
                "water": "是",
                "sleep": "6h",
                "mood": "3",
                "exercise": "是",
            },
        },
    )
    assert checkin_resp.status_code == 201
    data = checkin_resp.json()
    assert data["service_case_id"] == case_id
    assert data["checkin_date"] == "2026-05-01"
    assert data["content"] == "打卡D1: wt65.0 饮水达标 睡眠6h 情绪3 运动完成"
    assert data["group_id"] == gid
    assert data["user_id"] == case["user_id"]
    assert "id" in data
    assert "created_at" in data
    parsed = data["parsed_json"]
    assert parsed is not None
    assert parsed["weight"] == "65.0"
    assert parsed["water"] == "是"
    assert parsed["sleep"] == "6h"
    assert parsed["mood"] == "3"
    assert parsed["exercise"] == "是"


@pytest.mark.asyncio
async def test_graduated_case_rejects_new_checkin(admin_client, public_client, db_session):
    suffix = uuid.uuid4().hex[:8]
    user = User(name="Graduated User", phone="13800000009")
    db_session.add(user)
    await db_session.flush()
    service_case = ServiceCase(
        case_no=f"GRAD-{suffix}",
        user_id=user.id,
        group_id=f"test-graduated-{suffix}",
        questionnaire_token=f"tok-graduated-{suffix}",
        status=ServiceCaseStatus.IN_SERVICE,
        service_plan_start_date=date(2026, 1, 1),
    )
    db_session.add(service_case)
    await db_session.flush()

    checkin_resp = await admin_client.post(
        f"/service-cases/{service_case.id}/checkins",
        json={"content": "毕业后补打卡", "parsed_json": {"weight": "60"}},
    )
    assert checkin_resp.status_code == 409

    questionnaire_resp = await public_client.get(
        f"/questionnaires/{service_case.questionnaire_token}",
        params={"template": "personalized_checkin"},
    )
    assert questionnaire_resp.status_code == 410


@pytest.mark.asyncio
async def test_backfill_historical_personalized_h5_answers_keeps_question_snapshot(db_session):
    now = datetime.now(timezone.utc)
    user = User(name="历史H5打卡用户")
    db_session.add(user)
    await db_session.flush()
    service_case = ServiceCase(
        case_no=f"H5-BACKFILL-{uuid.uuid4().hex[:8]}",
        user_id=user.id,
        group_id=f"h5-backfill-{uuid.uuid4().hex[:8]}",
        questionnaire_token=uuid.uuid4().hex,
        status=ServiceCaseStatus.IN_SERVICE,
    )
    db_session.add(service_case)
    await db_session.flush()
    document = UserDocument(
        service_case_id=service_case.id,
        document_type=DocumentType.DAY28_EXECUTION_STANDARD,
        title="28天执行标准与打卡要求",
        content_md="ok",
        content_json={
            "daily_checkin": [
                "今天是否完成早C/午B/晚A？",
                "今天是否在23:30前上床？",
                "今天蔬菜是否达到两拳？",
                "今天是否完成7000步？",
                "今天情绪评分（1-5分）",
            ]
        },
        status=DocumentStatus.SENT,
        created_at=now - timedelta(minutes=2),
        sent_at=now - timedelta(minutes=1),
    )
    checkin = Checkin(
        service_case_id=service_case.id,
        group_id=service_case.group_id,
        user_id=user.id,
        checkin_date=now.date(),
        content="历史H5打卡",
        parsed_json={"weight": "65"},
    )
    submission = QuestionnaireSubmission(
        service_case_id=service_case.id,
        template_key="personalized_checkin",
        created_at=now,
        answers_json={
            "personalized_checkin_2": "是",
            "personalized_checkin_3": "否",
        },
    )
    db_session.add_all([document, checkin, submission])
    await db_session.flush()

    preview = await backfill_personalized_h5_checkins(db_session, apply=False)
    assert preview.answers_planned == 2
    assert preview.answers_written == 0
    assert "custom_checkin" not in (checkin.parsed_json or {})

    applied = await backfill_personalized_h5_checkins(db_session, apply=True)
    assert applied.answers_written == 2
    parsed = checkin.parsed_json or {}
    item = parsed["custom_checkin"][f"document:{document.id}:2"]
    assert item == {
        "question": "今天是否在23:30前上床？",
        "answer": "是",
        "source": "historical_h5",
        "questionnaire_submission_id": submission.id,
    }

    rerun = await backfill_personalized_h5_checkins(db_session, apply=True)
    assert rerun.answers_written == 0
    assert rerun.skipped["existing_custom_answer"] == 2


@pytest.mark.asyncio
async def test_seed_28_days_checkins(admin_client, create_case, db_session):
    gid = f"test-checkin-seed-{uuid.uuid4().hex[:8]}"

    create_resp = await create_case({
        "group_id": gid,
        "user": {"name": "Seed User", "phone": "13800000002"},
    })
    assert create_resp.status_code == 201
    case = create_resp.json()
    case_id = case["id"]
    group_id = case["group_id"]
    user_id = case["user_id"]

    await seed_checkins(db_session, case_id, group_id, user_id, days=28)
    await db_session.commit()

    resp = await admin_client.get(f"/service-cases/{case_id}/checkins")
    assert resp.status_code == 200
    checkins = resp.json()
    assert len(checkins) == 28

    sorted_by_date = sorted(checkins, key=lambda c: c["checkin_date"])
    first_weight = sorted_by_date[0]["parsed_json"]["weight"]
    last_weight = sorted_by_date[-1]["parsed_json"]["weight"]
    assert first_weight == "65.0"
    assert last_weight == "63.3"

    for c in checkins:
        assert c["service_case_id"] == case_id
        parsed = c["parsed_json"]
        assert parsed is not None
        for key in ("weight", "water", "sleep", "mood", "exercise"):
            assert key in parsed


@pytest.mark.asyncio
async def test_checkin_parsed_json_structure(admin_client, create_case):
    gid = f"test-checkin-json-{uuid.uuid4().hex[:8]}"

    create_resp = await create_case({
        "group_id": gid,
        "user": {"name": "JSON Test User", "phone": "13800000003"},
    })
    assert create_resp.status_code == 201
    case = create_resp.json()
    case_id = case["id"]

    checkin_resp = await admin_client.post(
        f"/service-cases/{case_id}/checkins",
        json={
            "checkin_date": "2026-05-15",
            "content": "完整打卡数据",
            "parsed_json": {
                "weight": "62.5",
                "water": "是",
                "sleep": "7.5h",
                "mood": "5",
                "exercise": "是",
                "waist": "78cm",
                "diet": "干净饮食",
                "bowel": "规律",
            },
        },
    )
    assert checkin_resp.status_code == 201
    data = checkin_resp.json()
    parsed = data["parsed_json"]
    assert parsed is not None
    assert parsed["weight"] == "62.5"
    assert parsed["water"] == "是"
    assert parsed["sleep"] == "7.5h"
    assert parsed["mood"] == "5"
    assert parsed["exercise"] == "是"
    assert parsed["waist"] == "78cm"
    assert parsed["diet"] == "干净饮食"
    assert parsed["bowel"] == "规律"

    unknown_key = parsed.get("nonexistent_field")
    assert unknown_key is None

    get_resp = await admin_client.get(f"/service-cases/{case_id}/checkins")
    assert get_resp.status_code == 200
    stored = get_resp.json()
    assert len(stored) == 1
    assert stored[0]["parsed_json"]["weight"] == "62.5"


@pytest.mark.asyncio
async def test_checkin_date_ordering(admin_client, create_case):
    gid = f"test-checkin-order-{uuid.uuid4().hex[:8]}"

    create_resp = await create_case({
        "group_id": gid,
        "user": {"name": "Order Test User", "phone": "13800000004"},
    })
    assert create_resp.status_code == 201
    case = create_resp.json()
    case_id = case["id"]

    dates = ["2026-05-03", "2026-05-01", "2026-05-02"]
    for d in dates:
        resp = await admin_client.post(
            f"/service-cases/{case_id}/checkins",
            json={
                "checkin_date": d,
                "content": f"打卡 {d}",
                "parsed_json": {"weight": "64.0"},
            },
        )
        assert resp.status_code == 201

    get_resp = await admin_client.get(f"/service-cases/{case_id}/checkins")
    assert get_resp.status_code == 200
    checkins = get_resp.json()
    assert len(checkins) == 3

    returned_dates = [c["checkin_date"] for c in checkins]
    assert returned_dates == ["2026-05-03", "2026-05-02", "2026-05-01"]
