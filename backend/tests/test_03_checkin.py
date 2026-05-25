import uuid

import pytest

from tests.conftest import seed_checkins


@pytest.mark.asyncio
async def test_create_single_checkin(admin_client, public_client, cai_haoran_answers):
    gid = f"test-checkin-create-{uuid.uuid4().hex[:8]}"

    create_resp = await admin_client.post("/service-cases", json={
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
async def test_seed_28_days_checkins(admin_client, db_session):
    gid = f"test-checkin-seed-{uuid.uuid4().hex[:8]}"

    create_resp = await admin_client.post("/service-cases", json={
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
async def test_checkin_parsed_json_structure(admin_client):
    gid = f"test-checkin-json-{uuid.uuid4().hex[:8]}"

    create_resp = await admin_client.post("/service-cases", json={
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
async def test_checkin_date_ordering(admin_client):
    gid = f"test-checkin-order-{uuid.uuid4().hex[:8]}"

    create_resp = await admin_client.post("/service-cases", json={
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
