"""一个群先后服务多位客户（老群复用）。

背景：运营会把服务完的老群改个名、拉进新客户接着用（员工都在，不用重新建群，
连续建群还会被企微标记）。此前系统假设「一个群 = 一位客户的 28 天」，逼着运营
先去后台把旧单删掉，忘了删就把新客户的话记进旧客户档案。

这里覆盖的是换人这条链路：客户本人退群让位 → 新客户进来自动开新一期 →
误退的人回来接回原单。
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.models.message import Message, MessageDirection
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user import User
from app.services.channels.base import ChannelMessage
from app.services.content_policy import set_qiwei_staff_senders
from app.services.ranren_ai.engine import RanrenAIEngine


CUSTOMER_WECOM_ID = "customer-old-1"
STAFF_WECOM_ID = "staff-班主任"


async def _setup_group_with_customer(db_session, group_id: str) -> ServiceCase:
    await set_qiwei_staff_senders(
        [{"type": "equals", "pattern": STAFF_WECOM_ID}], session=db_session
    )
    user = User(name="老客户", wecom_user_id=CUSTOMER_WECOM_ID)
    db_session.add(user)
    await db_session.flush()
    service_case = ServiceCase(
        case_no=f"REUSE-{group_id}",
        user_id=user.id,
        group_id=group_id,
        questionnaire_token=f"token-{group_id}",
        status=ServiceCaseStatus.IN_SERVICE,
        ai_auto_reply_enabled=False,  # 静音，测的是归属不是回复
    )
    db_session.add(service_case)
    await db_session.commit()
    return service_case


def _quit_notice(group_id: str, *, member_id: str, message_id: str) -> ChannelMessage:
    return ChannelMessage(
        channel="qiwe",
        chat_id=group_id,
        chat_type="group",
        text="退出了群聊",
        user_id=member_id,
        user_name="",
        message_type="system_notice",
        message_id=message_id,
        media_card={"kind": "member_quit", "member_ids": member_id},
        raw_payload={"userId": "bot-1", "msgData": {}},
    )


def _text(group_id: str, *, user_id: str, name: str, text: str, message_id: str) -> ChannelMessage:
    return ChannelMessage(
        channel="qiwe",
        chat_id=group_id,
        chat_type="group",
        text=text,
        user_id=user_id,
        user_name=name,
        message_type="text",
        message_id=message_id,
        raw_payload={"userId": "bot-1", "msgData": {"atList": []}},
    )


@pytest.mark.asyncio
async def test_customer_quit_frees_the_group_and_next_customer_gets_a_new_case(
    db_session, mock_message_channel
):
    """客户本人退群 → 单子打上「已退群」让位 → 新客户开口自动开新一期。"""
    group_id = "G-REUSE-1"
    old_case = await _setup_group_with_customer(db_session, group_id)
    engine = RanrenAIEngine(mock_message_channel)

    await engine.handle_message(
        _quit_notice(group_id, member_id=CUSTOMER_WECOM_ID, message_id="quit-1")
    )
    await db_session.refresh(old_case)
    assert old_case.member_left_at is not None, "客户本人退群必须让出群位"

    # 退群通知本身仍留在上一期的聊天记录里，方便回看这个群是什么时候换的人
    notice = await db_session.scalar(
        select(Message).where(Message.wecom_message_id == "quit-1")
    )
    assert notice is not None and notice.service_case_id == old_case.id

    await engine.handle_message(
        _text(group_id, user_id="customer-new-1", name="新客户", text="你好", message_id="new-1")
    )
    cases = list(
        await db_session.scalars(
            select(ServiceCase).where(ServiceCase.group_id == group_id).order_by(ServiceCase.id)
        )
    )
    assert len(cases) == 2, "同一个群应当先后挂两张单，旧的一张都不用删"
    new_case = cases[-1]
    assert new_case.id != old_case.id
    assert new_case.member_left_at is None
    assert new_case.questionnaire_token != old_case.questionnaire_token, (
        "新一期必须是全新的问卷，不能让新客户点开上一位客户填过的那份"
    )

    first_message = await db_session.scalar(
        select(Message).where(Message.wecom_message_id == "new-1")
    )
    assert first_message is not None and first_message.service_case_id == new_case.id


@pytest.mark.asyncio
async def test_staff_leaving_does_not_free_the_group(db_session, mock_message_channel):
    """走的是员工不是客户：什么都不该发生（8/13 就有员工一分钟连退 12 个群）。"""
    group_id = "G-REUSE-2"
    case = await _setup_group_with_customer(db_session, group_id)
    engine = RanrenAIEngine(mock_message_channel)

    await engine.handle_message(
        _quit_notice(group_id, member_id=STAFF_WECOM_ID, message_id="quit-staff-1")
    )
    await db_session.refresh(case)
    assert case.member_left_at is None


@pytest.mark.asyncio
async def test_staff_talking_in_an_emptied_group_does_not_open_a_new_case(
    db_session, mock_message_channel
):
    """客户走后群里只剩班主任聊天：不能凭空多出一张空单，消息挂回上一期留痕。"""
    group_id = "G-REUSE-3"
    old_case = await _setup_group_with_customer(db_session, group_id)
    engine = RanrenAIEngine(mock_message_channel)

    await engine.handle_message(
        _quit_notice(group_id, member_id=CUSTOMER_WECOM_ID, message_id="quit-3")
    )
    await engine.handle_message(
        _text(group_id, user_id=STAFF_WECOM_ID, name="班主任", text="这个群留着", message_id="staff-3")
    )

    case_count = await db_session.scalar(
        select(func.count()).select_from(ServiceCase).where(ServiceCase.group_id == group_id)
    )
    assert case_count == 1
    staff_message = await db_session.scalar(
        select(Message).where(Message.wecom_message_id == "staff-3")
    )
    assert staff_message is not None and staff_message.service_case_id == old_case.id


@pytest.mark.asyncio
async def test_staff_asking_for_the_questionnaire_opens_the_next_period(
    db_session, mock_message_channel
):
    """把新客户拉进老群后，班主任说「发送本群专属问卷」——这一刻就该开新一期。

    今早线上翻车的就是这一步：发出去的还是上一位客户那份链接，点开是别人填的答案。
    """
    group_id = "G-REUSE-9"
    old_case = await _setup_group_with_customer(db_session, group_id)
    engine = RanrenAIEngine(mock_message_channel)

    await engine.handle_message(
        _quit_notice(group_id, member_id=CUSTOMER_WECOM_ID, message_id="quit-9")
    )
    await engine.handle_message(
        _text(
            group_id,
            user_id=STAFF_WECOM_ID,
            name="班主任",
            text="发送本群专属问卷",
            message_id="cmd-9",
        )
    )

    new_case = await db_session.scalar(
        select(ServiceCase)
        .where(ServiceCase.group_id == group_id, ServiceCase.member_left_at.is_(None))
        .order_by(ServiceCase.id.desc())
    )
    assert new_case is not None and new_case.id != old_case.id
    assert new_case.questionnaire_token != old_case.questionnaire_token


@pytest.mark.asyncio
async def test_customer_returning_resumes_the_original_case(db_session, mock_message_channel):
    """误退又回来：接回原单，服务不中断，不新开一期。"""
    group_id = "G-REUSE-4"
    case = await _setup_group_with_customer(db_session, group_id)
    engine = RanrenAIEngine(mock_message_channel)

    await engine.handle_message(
        _quit_notice(group_id, member_id=CUSTOMER_WECOM_ID, message_id="quit-4")
    )
    await db_session.refresh(case)
    assert case.member_left_at is not None

    await engine.handle_message(
        _text(group_id, user_id=CUSTOMER_WECOM_ID, name="老客户", text="我又回来了", message_id="back-4")
    )
    await db_session.refresh(case)
    assert case.member_left_at is None, "同一个人回到同一个群应当接回原单"

    case_count = await db_session.scalar(
        select(func.count()).select_from(ServiceCase).where(ServiceCase.group_id == group_id)
    )
    assert case_count == 1
    back_message = await db_session.scalar(
        select(Message).where(Message.wecom_message_id == "back-4")
    )
    assert back_message is not None and back_message.service_case_id == case.id


@pytest.mark.asyncio
async def test_returning_customer_does_not_take_the_group_back_from_the_new_one(
    db_session, mock_message_channel
):
    """新一期已经开始了，原客户又回到群里：静默处理，不抢群，也不复活旧单。"""
    group_id = "G-REUSE-5"
    old_case = await _setup_group_with_customer(db_session, group_id)
    engine = RanrenAIEngine(mock_message_channel)

    await engine.handle_message(
        _quit_notice(group_id, member_id=CUSTOMER_WECOM_ID, message_id="quit-5")
    )
    await engine.handle_message(
        _text(group_id, user_id="customer-new-5", name="新客户", text="报到", message_id="new-5")
    )
    new_case = await db_session.scalar(
        select(ServiceCase)
        .where(ServiceCase.group_id == group_id, ServiceCase.member_left_at.is_(None))
        .order_by(ServiceCase.id.desc())
    )
    assert new_case is not None and new_case.id != old_case.id

    await engine.handle_message(
        _text(group_id, user_id=CUSTOMER_WECOM_ID, name="老客户", text="路过", message_id="oldback-5")
    )
    await db_session.refresh(old_case)
    assert old_case.member_left_at is not None, "旧单不能被复活，新客户正在这个群里服务"

    stray = await db_session.scalar(
        select(Message).where(Message.wecom_message_id == "oldback-5")
    )
    assert stray is not None and stray.service_case_id == new_case.id


@pytest.mark.asyncio
async def test_membership_reconciliation_marks_and_restores(db_session, mock_message_channel):
    """兜底对账：退群通知漏了也能补上标记，人回来了也能撤掉。"""
    from app.services.reminder_service import ReminderService

    group_id = "G-REUSE-6"
    case = await _setup_group_with_customer(db_session, group_id)

    members: dict[str, set[str]] = {group_id: {STAFF_WECOM_ID}}

    async def fake_batch_get_room_members(room_ids):
        return {rid: members[rid] for rid in room_ids if rid in members}

    mock_message_channel.batch_get_room_members = fake_batch_get_room_members
    service = ReminderService()

    await service.check_group_membership_once()
    await db_session.refresh(case)
    assert case.member_left_at is not None, "名单里没有这位客户 → 补上「已退群」"

    members[group_id] = {STAFF_WECOM_ID, CUSTOMER_WECOM_ID}
    service._last_group_membership_check = None
    await service.check_group_membership_once()
    await db_session.refresh(case)
    assert case.member_left_at is None, "人又出现在名单里 → 撤掉标记"

    # 接口抖动返回空名单时必须按兵不动，不能把在服务的客户批量归档
    members.clear()
    service._last_group_membership_check = None
    await service.check_group_membership_once()
    await db_session.refresh(case)
    assert case.member_left_at is None


@pytest.mark.asyncio
async def test_periodic_group_tasks_skip_cases_whose_customer_left(db_session):
    """已退群的服务单不再进「会往群里发东西」的周期任务。"""
    from app.services.service_case_queries import (
        select_active_service_cases,
        select_present_service_cases,
    )

    group_id = "G-REUSE-7"
    case = await _setup_group_with_customer(db_session, group_id)
    case.member_left_at = func.now()
    await db_session.commit()

    present = list(
        await db_session.scalars(
            select_present_service_cases(ServiceCase.group_id == group_id)
        )
    )
    assert present == []

    # 后台仍然查得到这张单（只是不再被自动任务打扰）
    still_listed = list(
        await db_session.scalars(select_active_service_cases(ServiceCase.group_id == group_id))
    )
    assert [c.id for c in still_listed] == [case.id]


@pytest.mark.asyncio
async def test_inbound_direction_is_unchanged_for_notices(db_session, mock_message_channel):
    """退群通知照旧按入站消息落库，不因为归档而丢记录。"""
    group_id = "G-REUSE-8"
    await _setup_group_with_customer(db_session, group_id)
    engine = RanrenAIEngine(mock_message_channel)
    await engine.handle_message(
        _quit_notice(group_id, member_id=CUSTOMER_WECOM_ID, message_id="quit-8")
    )
    notice = await db_session.scalar(
        select(Message).where(Message.wecom_message_id == "quit-8")
    )
    assert notice is not None
    assert notice.direction == MessageDirection.INBOUND
    assert notice.content == "退出了群聊"
