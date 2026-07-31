"""退款客户：模型识别已退款事实 → 待确认 → 确认后停产品类主动推送、保留聊天。

覆盖：
- merge_case_insight 对 refund_intent.completed 的处理（置候选、不改状态；仅意向不置候选；已 REFUNDED 不重复置）。
- 状态改 REFUNDED 后，主动推送（以阶段报告为代表，各类推送同样只查 IN_SERVICE）不再发送。
- check_stage_reports_once 跳过 REFUNDED 服务单。
- 列表筛选 refund_pending / refunded。
"""
from datetime import date, datetime, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user import User
from app.models.user_document import UserDocument
from app.services.message_insight_service import merge_case_insight
from app.services.reminder_service import ReminderService

TZ = ZoneInfo("Asia/Shanghai")


async def _make_case(db_session, *, status=ServiceCaseStatus.IN_SERVICE, started_at=None):
    tag = uuid4().hex[:8]
    user = User(name="u")
    db_session.add(user)
    await db_session.flush()
    sc = ServiceCase(
        case_no=f"RF-{tag}",
        user_id=user.id,
        group_id=f"g-{tag}",
        questionnaire_token=f"tok-{tag}",
        status=status,
        started_at=started_at,
    )
    db_session.add(sc)
    await db_session.flush()
    return sc


# ---- 识别层 ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_completed_refund_flags_pending_confirmation(db_session):
    sc = await _make_case(db_session)
    merge_case_insight(sc, {"refund_intent": {"detected": True, "completed": True}})
    assert sc.refund_pending_confirmation is True
    # 只进候选，不改状态、不停推
    assert sc.status == ServiceCaseStatus.IN_SERVICE


@pytest.mark.asyncio
async def test_refund_intent_only_does_not_flag(db_session):
    sc = await _make_case(db_session)
    merge_case_insight(sc, {"refund_intent": {"detected": True, "completed": False}})
    assert sc.refund_pending_confirmation is False
    # 意向仍照旧记录，供后台意向筛选
    assert sc.refund_intent_flags and sc.refund_intent_flags[0]["detected"] is True


@pytest.mark.asyncio
async def test_completed_refund_ignored_when_already_refunded(db_session):
    sc = await _make_case(db_session, status=ServiceCaseStatus.REFUNDED)
    merge_case_insight(sc, {"refund_intent": {"detected": True, "completed": True}})
    assert sc.refund_pending_confirmation is False


# ---- 阶段报告（以阶段报告为代表，各类主动推送同样只服务 IN_SERVICE）----------

@pytest.mark.asyncio
async def test_stage_reports_skip_refunded(db_session, mock_session_local):
    started_at = datetime.now(timezone.utc).replace(year=2026, month=6, day=1)
    sc = await _make_case(db_session, status=ServiceCaseStatus.REFUNDED, started_at=started_at)
    sc.service_plan_start_date = date(2026, 6, 1)
    await db_session.flush()
    # 第28天当地23点：若为 IN_SERVICE 本会生成 day28 阶段报告；REFUNDED 应完全跳过
    await ReminderService().check_stage_reports_once(now=datetime(2026, 6, 28, 23, 0, tzinfo=TZ))
    docs = list(await db_session.scalars(select(UserDocument).where(UserDocument.service_case_id == sc.id)))
    assert docs == []


# ---- 列表筛选 -------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_filters_refund_pending_and_refunded(admin_client, create_case):
    group_id = f"rf-list-{uuid4().hex[:8]}"
    case = (await create_case({"group_id": group_id, "user": {"name": "退款列表"}})).json()
    case_id = case["id"]

    # 置候选（模拟识别到已退款事实，尚未确认）
    r = await admin_client.patch(f"/service-cases/{case_id}", json={"refund_pending_confirmation": True})
    assert r.status_code == 200, r.text

    pending_ids = [c["id"] for c in (await admin_client.get("/service-cases", params={"filter": "refund_pending"})).json()]
    assert case_id in pending_ids
    refunded_ids = [c["id"] for c in (await admin_client.get("/service-cases", params={"filter": "refunded"})).json()]
    assert case_id not in refunded_ids

    # 运营确认退款 → 改状态；应从待确认移出、进入已退款
    r = await admin_client.patch(f"/service-cases/{case_id}", json={"status": "refunded", "refund_pending_confirmation": False})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "refunded"

    pending_ids = [c["id"] for c in (await admin_client.get("/service-cases", params={"filter": "refund_pending"})).json()]
    assert case_id not in pending_ids
    refunded_ids = [c["id"] for c in (await admin_client.get("/service-cases", params={"filter": "refunded"})).json()]
    assert case_id in refunded_ids
