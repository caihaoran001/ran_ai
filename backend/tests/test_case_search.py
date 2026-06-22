"""服务单搜索（名字/手机号/群名）+ 群名回填回归测试。"""
import pytest

from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user import User
from app.services.channels.registry import set_current_channel
from app.services.reminder_service import ReminderService


async def _make_case(db_session, *, name, phone, group_id, group_name=None, case_no):
    user = User(name=name, phone=phone)
    db_session.add(user)
    await db_session.flush()
    sc = ServiceCase(
        case_no=case_no,
        user_id=user.id,
        group_id=group_id,
        group_name=group_name,
        questionnaire_token=f"tok-{case_no}",
        status=ServiceCaseStatus.IN_SERVICE,
    )
    db_session.add(sc)
    await db_session.flush()
    return sc


@pytest.mark.asyncio
async def test_search_by_name(admin_client, db_session):
    await _make_case(db_session, name="张三丰", phone="13800000001", group_id="ROOM_A", case_no="SRCH-A")
    await _make_case(db_session, name="李四光", phone="13900000002", group_id="ROOM_B", case_no="SRCH-B")
    await db_session.flush()

    r = await admin_client.get("/service-cases?q=张三")
    assert r.status_code == 200
    nos = [c["case_no"] for c in r.json()]
    assert "SRCH-A" in nos and "SRCH-B" not in nos


@pytest.mark.asyncio
async def test_search_by_phone(admin_client, db_session):
    await _make_case(db_session, name="王五", phone="13800000001", group_id="ROOM_C", case_no="SRCH-C")
    await _make_case(db_session, name="赵六", phone="13912345678", group_id="ROOM_D", case_no="SRCH-D")
    await db_session.flush()

    r = await admin_client.get("/service-cases?q=13912345678")
    assert r.status_code == 200
    nos = [c["case_no"] for c in r.json()]
    assert "SRCH-D" in nos and "SRCH-C" not in nos


@pytest.mark.asyncio
async def test_search_by_group_name(admin_client, db_session):
    await _make_case(db_session, name="孙七", phone=None, group_id="ROOM_E", group_name="阿素-高燃人生-0615", case_no="SRCH-E")
    await _make_case(db_session, name="周八", phone=None, group_id="ROOM_F", group_name="别的群-0620", case_no="SRCH-F")
    await db_session.flush()

    r = await admin_client.get("/service-cases?q=阿素")
    assert r.status_code == 200
    nos = [c["case_no"] for c in r.json()]
    assert "SRCH-E" in nos and "SRCH-F" not in nos


@pytest.mark.asyncio
async def test_check_room_names_backfills_name(db_session, mock_session_local):
    sc = await _make_case(db_session, name="顾九", phone=None, group_id="ROOM_X", case_no="RN-1")
    await db_session.flush()

    class FakeChannel:
        async def batch_get_room_names(self, room_ids):
            return {"ROOM_X": "高燃群X"}

    set_current_channel(FakeChannel())
    await ReminderService().check_room_names_once()
    assert sc.group_name == "高燃群X"


@pytest.mark.asyncio
async def test_check_room_names_marks_empty_when_unavailable(db_session, mock_session_local):
    # 群名取不到（机器人已退/群解散）→ 把 None 标记成 ""，避免补空分支每轮重复重试
    sc = await _make_case(db_session, name="郑十", phone=None, group_id="ROOM_GONE", case_no="RN-2")
    await db_session.flush()

    class FakeChannel:
        async def batch_get_room_names(self, room_ids):
            return {}  # 查不到

    set_current_channel(FakeChannel())
    await ReminderService().check_room_names_once()
    assert sc.group_name == ""
