"""群类型(new_group/legacy)隔离：静态标记、列表筛选、运营可改、看板只统计新群。

老群（接入本系统之前就存在的群）照常被服务，但默认不进看板统计，避免不活跃老群稀释数据。

2026-08-17 起群类型是**静态**的：新建服务单一律默认 new_group，运行时不再做任何新老识别
（原来"提交入营问卷即置 new_group"那一步已移除，理由见 ServiceCase.group_type 注释）。
老群是一个封存的历史集合，例外交运营在后台手动改。
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


async def test_new_case_defaults_to_new_group(db_session):
    """建单即定死为新客户：不传群类型时默认 new_group，运行时不需要任何识别。

    生产上服务单只有一个创建入口——群里来消息、该群还没有单子就自动建一张
    (RanrenAIEngine._create_case_for_message，它不传群类型)，所以这个默认值就是全部。
    """
    from app.models.user import User

    user = User(name="自动建单用户")
    db_session.add(user)
    await db_session.flush()
    case = ServiceCase(
        case_no=f"TEST-GT-{uuid4().hex[:6].upper()}",
        user_id=user.id,
        group_id=f"gt-{uuid4().hex[:8]}",
        questionnaire_token=uuid4().hex,
    )
    db_session.add(case)
    await db_session.flush()
    assert case.group_type == ServiceCaseGroupType.NEW_GROUP.value


async def test_intake_submit_does_not_touch_group_type(
    admin_client, create_case, db_session, mock_ai, cai_haoran_answers
):
    """提交入营问卷不再改群类型——封存的老群单子交完问卷仍是 legacy。

    这是静态化的直接后果，也是刻意的：判定放在问卷提交里就等于放在 ingress 里，而部署脚本
    刻意不重建 ingress，那一步会随时静默失效（8/13–8/17 就失效了四天）。标记过时的代价已
    被压到只影响看板统计——待办队列不按群类型过滤，交付文件不会再从审核控制台消失
    （见 test_review_queue_shows_all_groups.py）。
    """
    resp = await create_case({
        "group_id": f"test-gt-intake-{uuid4().hex[:8]}",
        "user": {"name": "蔡浩然"},
        "group_type": ServiceCaseGroupType.LEGACY.value,
    })
    case_id = resp.json()["id"]

    submit_resp = await admin_client.post(
        f"/questionnaires/{resp.json()['questionnaire_token']}/submit?template=intake",
        json={"answers": cai_haoran_answers},
    )
    assert submit_resp.status_code == 201, submit_resp.text

    case = await db_session.get(ServiceCase, case_id)
    await db_session.refresh(case)
    assert case.group_type == ServiceCaseGroupType.LEGACY.value


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