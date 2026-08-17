"""审核控制台的待办队列必须全量，不按新老群过滤。

背景（2026-08-16 生产事故）：ingress 跑的旧代码没有"提交入营问卷即标记新群"那一行，
三位新客户的服务单留在 legacy；而待审文件队列与侧边栏红点都沿用了看板的"只看新群"
统计口径，于是 6 份待审文件在控制台完全不可见——不报错、不提示、红点也不计数，
直到第二天客户主动来问才发现。

新老群隔离的既定意图只覆盖"看板与群体画像"（老群照常被服务），待办清单不在其列。
"""

from __future__ import annotations

import secrets

import pytest
from sqlalchemy import select

from app.api.routes.events import _review_counts
from app.models.service_case import ServiceCase, ServiceCaseGroupType
from app.models.user_document import DocumentStatus, UserDocument

from tests.test_01_intake import INTAKE_SYSTEM_PROMPT_PREFIX, _make_intake_mock_response


async def _legacy_case_with_pending_documents(
    admin_client, create_case, db_session, mock_ai, cai_haoran_answers, wait_background
) -> int:
    """造一个被标成老群、但有待审交付文件的服务单（复刻 8/16 的三个单子）。"""
    mock_ai._responses = {INTAKE_SYSTEM_PROMPT_PREFIX: _make_intake_mock_response()}
    resp = await create_case(
        {"group_id": f"test-group-legacy-{secrets.token_hex(6)}", "user": {"name": "蔡浩然"}}
    )
    assert resp.status_code == 201, resp.text
    case_id = resp.json()["id"]
    submit_resp = await admin_client.post(
        f"/questionnaires/{resp.json()['questionnaire_token']}/submit?template=intake",
        json={"answers": cai_haoran_answers},
    )
    assert submit_resp.status_code == 201, submit_resp.text
    await wait_background()

    # 文件生成了，但这张单子被标成老客户——8/16 是 ingress 旧代码导致的，静态化之后仍可能
    # 来自封存的老群单子进了新流程、或运营手工标错。队列都必须照样显示。
    service_case = await db_session.get(ServiceCase, case_id)
    service_case.group_type = ServiceCaseGroupType.LEGACY.value
    documents = list(
        await db_session.scalars(
            select(UserDocument).where(UserDocument.service_case_id == case_id)
        )
    )
    assert documents, "前置条件不成立：应先生成出交付文件"
    for document in documents:
        document.status = DocumentStatus.REVIEWING
    await db_session.commit()
    return case_id


@pytest.mark.asyncio
async def test_legacy_group_pending_documents_are_visible_in_the_queue(
    admin_client, create_case, db_session, mock_ai, cai_haoran_answers, wait_background
):
    case_id = await _legacy_case_with_pending_documents(
        admin_client, create_case, db_session, mock_ai, cai_haoran_answers, wait_background
    )

    resp = await admin_client.get("/dashboard/pending-documents")
    assert resp.status_code == 200, resp.text
    pending = [
        document
        for group in resp.json()
        if group["case"]["service_case_id"] == case_id
        for document in group["documents"]
    ]
    assert len(pending) == 2, "被标成老群的服务单，其待审文件也必须出现在审核队列里"


@pytest.mark.asyncio
async def test_legacy_group_pending_documents_are_counted_in_the_badge(
    admin_client, create_case, db_session, mock_ai, cai_haoran_answers, wait_background
):
    """红点计数必须与队列同口径，否则会出现「列表有、红点不算」的错乱。"""
    before = await _review_counts()

    case_id = await _legacy_case_with_pending_documents(
        admin_client, create_case, db_session, mock_ai, cai_haoran_answers, wait_background
    )

    after = await _review_counts()
    assert after["pending_documents"] == before["pending_documents"] + 2, (
        f"红点少算了老群的待审文件：before={before} after={after}"
    )
    assert after["pending_total"] == before["pending_total"] + 2

    # 与队列接口交叉核对：两者必须都看到同一批文件。
    resp = await admin_client.get("/dashboard/pending-documents")
    listed = [
        document
        for group in resp.json()
        if group["case"]["service_case_id"] == case_id
        for document in group["documents"]
    ]
    assert len(listed) == 2


@pytest.mark.asyncio
async def test_dashboard_statistics_still_exclude_legacy_groups(
    admin_client, create_case, db_session, mock_ai, cai_haoran_answers, wait_background
):
    """反向保护：看板统计口径不能被一起改掉——排除老群是它的正当目的。

    只有待办类查询摘掉新老群过滤；统计(新客户数、成效趋势、成本归集等)照旧只看新群，
    否则老批次会稀释指标。
    """
    case_id = await _legacy_case_with_pending_documents(
        admin_client, create_case, db_session, mock_ai, cai_haoran_answers, wait_background
    )

    resp = await admin_client.get("/dashboard/summary")
    assert resp.status_code == 200, resp.text

    # 同一个单子改回新群后，统计里的服务单总数应当增加，证明统计确实仍按群类型过滤。
    service_case = await db_session.get(ServiceCase, case_id)
    before_total = resp.json()["service_cases_total"]
    service_case.group_type = ServiceCaseGroupType.NEW_GROUP.value
    await db_session.commit()

    resp_after = await admin_client.get("/dashboard/summary")
    assert resp_after.status_code == 200, resp_after.text
    assert resp_after.json()["service_cases_total"] == before_total + 1, (
        "看板统计应仍然只统计新群；若两次相同说明统计口径被误改成全量"
    )
