"""群类型(new_group/legacy)隔离：入营问卷提交自动置新群、列表筛选、运营可改、看板只统计新群。

老群（未通过本系统填入营问卷）照常被服务，但默认不进看板统计，避免不活跃老群稀释数据。
"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select

from app.models.service_case import ServiceCase, ServiceCaseGroupType, ServiceCaseStatus


async def _raw_case(db_session, *, name: str, group_type: str = ServiceCaseGroupType.LEGACY.value) -> ServiceCase:
    """直接建一张指定群类型的服务单（绕过 HTTP，供列表/看板口径测试铺数据）。"""
    from app.models.user import User

    user = User(name=name)
    db_session.add(user)
    await db_session.flush()
    case = ServiceCase(
        case_no=f"TEST-GT-{uuid4().hex[:6].upper()}",
        user_id=user.id,
        group_id=f"gt-{uuid4().hex[:8]}",
        questionnaire_token=uuid4().hex,
        status=ServiceCaseStatus.IN_SERVICE,
        group_type=group_type,
    )
    db_session.add(case)
    await db_session.flush()
    return case


async def test_intake_submit_marks_case_as_new_group(admin_client, create_case, db_session, mock_ai, cai_haoran_answers):
    """通过本系统提交入营问卷后，服务单自动置为 new_group。"""
    resp = await create_case({
        "group_id": f"test-gt-intake-{uuid4().hex[:8]}",
        "user": {"name": "蔡浩然"},
        "group_type": ServiceCaseGroupType.LEGACY.value,
    })
    case_id = resp.json()["id"]
    case = await db_session.get(ServiceCase, case_id)
    assert case.group_type == ServiceCaseGroupType.LEGACY.value

    submit_resp = await admin_client.post(
        f"/questionnaires/{resp.json()['questionnaire_token']}/submit?template=intake",
        json={"answers": cai_haoran_answers},
    )
    assert submit_resp.status_code == 201, submit_resp.text

    case = await db_session.get(ServiceCase, case_id)
    assert case.group_type == ServiceCaseGroupType.NEW_GROUP.value


async def test_case_list_filters_by_group_type(admin_client, db_session):
    """列表接口按 group_type 过滤，只返回对应类型的服务单。"""
    await _raw_case(db_session, name="老群用户", group_type=ServiceCaseGroupType.LEGACY.value)
    new_case = await _raw_case(db_session, name="新群用户", group_type=ServiceCaseGroupType.NEW_GROUP.value)

    resp = await admin_client.get("/service-cases?group_type=new_group")
    assert resp.status_code == 200
    ids = [item["id"] for item in resp.json()]
    assert new_case.id in ids
    assert all(item["group_type"] == "new_group" for item in resp.json())

    resp = await admin_client.get("/service-cases?group_type=legacy")
    ids = [item["id"] for item in resp.json()]
    assert all(item["group_type"] == "legacy" for item in resp.json())
    assert new_case.id not in ids

    resp = await admin_client.get("/service-cases")
    assert new_case.id in [item["id"] for item in resp.json()]


async def test_case_list_rejects_unknown_group_type(admin_client, db_session):
    resp = await admin_client.get("/service-cases?group_type=unknown_kind")
    assert resp.status_code == 422


async def test_patch_group_type(admin_client, create_case, db_session):
    """运营可在后台手动修正群类型（新群没填问卷被误判老群等边界）。"""
    resp = await create_case({
        "group_id": f"test-gt-patch-{uuid4().hex[:8]}",
        "user": {"name": "边界用户"},
        "group_type": ServiceCaseGroupType.LEGACY.value,
    })
    case_id = resp.json()["id"]
    case = await db_session.get(ServiceCase, case_id)
    assert case.group_type == ServiceCaseGroupType.LEGACY.value

    patch_resp = await admin_client.patch(
        f"/service-cases/{case_id}",
        json={"group_type": "new_group"},
    )
    assert patch_resp.status_code == 200, patch_resp.text
    assert patch_resp.json()["group_type"] == "new_group"

    case = await db_session.get(ServiceCase, case_id)
    assert case.group_type == ServiceCaseGroupType.NEW_GROUP.value


async def test_dashboard_summary_counts_new_groups_only(admin_client, db_session):
    """看板服务单总数只统计新群；老群不进统计。"""
    before = (await admin_client.get("/dashboard/summary")).json()
    await _raw_case(db_session, name="老群用户", group_type=ServiceCaseGroupType.LEGACY.value)
    await _raw_case(db_session, name="新群用户", group_type=ServiceCaseGroupType.NEW_GROUP.value)

    resp = await admin_client.get("/dashboard/summary")
    assert resp.status_code == 200
    summary = resp.json()
    # 新增 1 个新群 + 1 个老群：总数只 +1（老群不入统计）
    assert summary["service_cases_total"] == before["service_cases_total"] + 1
    assert summary["service_cases_in_service"] == before["service_cases_in_service"] + 1