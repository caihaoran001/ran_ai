"""晨间执行重点推送回归测试。"""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.models.message import Message, MessagePurpose
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user import User
from app.models.user_state_profile import UserStateProfile
from app.services.reminder_service import ReminderService, _current_focus_key

TZ = ZoneInfo("Asia/Shanghai")


def _state(key):
    return {"domains": {"action_focus": {"current": {"value": {"current_focus": {"key": key, "label": "x", "action": "y"}}}}}}


def test_current_focus_key():
    assert _current_focus_key(_state("bowel_rhythm")) == "bowel_rhythm"
    assert _current_focus_key({"domains": {"action_focus": {"current": {"value": {"current_focus": {}}}}}}) is None
    assert _current_focus_key(None) is None


async def _make_case(db_session, *, risk="normal", focus_key=None, case_no):
    user = User(name="u")
    db_session.add(user)
    await db_session.flush()
    sc = ServiceCase(
        case_no=case_no, user_id=user.id, group_id=f"g-{case_no}",
        questionnaire_token=f"tok-{case_no}", status=ServiceCaseStatus.IN_SERVICE, risk_level=risk,
    )
    db_session.add(sc)
    await db_session.flush()
    if focus_key:
        db_session.add(UserStateProfile(service_case_id=sc.id, state_json=_state(focus_key), summary=None, source="test"))
        await db_session.flush()
    return sc


async def _msgs(db_session, sc_id):
    return list(await db_session.scalars(select(Message).where(Message.service_case_id == sc_id)))


@pytest.mark.asyncio
async def test_push_sends_mapped_focus(db_session, mock_session_local):
    sc = await _make_case(db_session, focus_key="bowel_rhythm", case_no="AF-ok")
    await db_session.flush()
    await ReminderService().check_action_focus_push_once(now=datetime(2026, 6, 25, 9, 0, tzinfo=TZ))
    msgs = await _msgs(db_session, sc.id)
    assert any("肠道" in m.content and m.purpose == MessagePurpose.REMINDER for m in msgs)


@pytest.mark.asyncio
async def test_push_skips_high_risk(db_session, mock_session_local):
    sc = await _make_case(db_session, risk="high", focus_key="bowel_rhythm", case_no="AF-high")
    await db_session.flush()
    await ReminderService().check_action_focus_push_once(now=datetime(2026, 6, 25, 9, 0, tzinfo=TZ))
    assert len(await _msgs(db_session, sc.id)) == 0


@pytest.mark.asyncio
async def test_push_only_at_9am(db_session, mock_session_local):
    sc = await _make_case(db_session, focus_key="bowel_rhythm", case_no="AF-time")
    await db_session.flush()
    await ReminderService().check_action_focus_push_once(now=datetime(2026, 6, 25, 10, 0, tzinfo=TZ))
    assert len(await _msgs(db_session, sc.id)) == 0


@pytest.mark.asyncio
async def test_push_skips_no_focus(db_session, mock_session_local):
    sc = await _make_case(db_session, focus_key=None, case_no="AF-nofocus")
    await db_session.flush()
    await ReminderService().check_action_focus_push_once(now=datetime(2026, 6, 25, 9, 0, tzinfo=TZ))
    assert len(await _msgs(db_session, sc.id)) == 0


@pytest.mark.asyncio
async def test_push_skips_when_case_toggle_off(db_session, mock_session_local):
    # 单服务单关闭「个性化提醒」→ 即使有 focus、非高风险、正点，也不推送
    sc = await _make_case(db_session, focus_key="bowel_rhythm", case_no="AF-off")
    sc.morning_push_enabled = False
    await db_session.flush()
    await ReminderService().check_action_focus_push_once(now=datetime(2026, 6, 25, 9, 0, tzinfo=TZ))
    assert len(await _msgs(db_session, sc.id)) == 0
