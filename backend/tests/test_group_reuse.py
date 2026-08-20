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
async def test_staff_talking_in_an_emptied_group_opens_the_next_period(
    db_session, mock_message_channel
):
    """客户走后班主任先在群里说话：这就算新一期开张（判定与全新群一致，任何人说话都建单）。

    上一期已经在客户离开那一刻封存，不能再吸收新对话；而班主任在新客户开口前的铺垫
    要落在新一期里，运营回看这一期能从第一句看起。
    """
    group_id = "G-REUSE-3"
    old_case = await _setup_group_with_customer(db_session, group_id)
    engine = RanrenAIEngine(mock_message_channel)

    await engine.handle_message(
        _quit_notice(group_id, member_id=CUSTOMER_WECOM_ID, message_id="quit-3")
    )
    await engine.handle_message(
        _text(group_id, user_id=STAFF_WECOM_ID, name="班主任", text="这个群留着", message_id="staff-3")
    )

    new_case = await db_session.scalar(
        select(ServiceCase)
        .where(ServiceCase.group_id == group_id, ServiceCase.member_left_at.is_(None))
        .order_by(ServiceCase.id.desc())
    )
    assert new_case is not None and new_case.id != old_case.id, "任何人说话都该开新一期"
    assert new_case.questionnaire_token != old_case.questionnaire_token

    staff_message = await db_session.scalar(
        select(Message).where(Message.wecom_message_id == "staff-3")
    )
    assert staff_message is not None
    assert staff_message.service_case_id == new_case.id, "封存的上一期不该继续吸收新对话"

    # 随后新客户开口：接着用同一期，不会再多开一张
    await engine.handle_message(
        _text(group_id, user_id="customer-new-3", name="新客户", text="你好", message_id="new-3")
    )
    case_count = await db_session.scalar(
        select(func.count()).select_from(ServiceCase).where(ServiceCase.group_id == group_id)
    )
    assert case_count == 2
    customer_message = await db_session.scalar(
        select(Message).where(Message.wecom_message_id == "new-3")
    )
    assert customer_message is not None and customer_message.service_case_id == new_case.id


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
async def test_new_period_binds_to_the_customer_who_speaks_first(
    db_session, mock_message_channel
):
    """复用老群最常见的顺序：客户退群 → 班主任拉新人进来 → 新客户开口。

    进群通知就把新一期开起来（任何人说话都建单），这一期先挂在占位客户上；
    新客户第一次开口时这张单认到她名下——后面她要是退群，才对得上号让位。
    """
    group_id = "G-REUSE-17"
    old_case = await _setup_group_with_customer(db_session, group_id)
    engine = RanrenAIEngine(mock_message_channel)

    await engine.handle_message(
        _quit_notice(group_id, member_id=CUSTOMER_WECOM_ID, message_id="quit-17")
    )
    await engine.handle_message(
        ChannelMessage(
            channel="qiwe",
            chat_id=group_id,
            chat_type="group",
            text="邀请 新同学 进群",
            user_id=STAFF_WECOM_ID,
            user_name="班主任",
            message_type="system_notice",
            message_id="join-17",
            media_card={"kind": "member_join", "member_ids": "customer-new-17"},
            raw_payload={"userId": "bot-1", "msgData": {}},
        )
    )
    new_case = await db_session.scalar(
        select(ServiceCase)
        .where(ServiceCase.group_id == group_id, ServiceCase.member_left_at.is_(None))
        .order_by(ServiceCase.id.desc())
    )
    assert new_case is not None and new_case.id != old_case.id

    await engine.handle_message(
        _text(
            group_id,
            user_id="customer-new-17",
            name="新同学",
            text="老师我来了",
            message_id="hello-17",
        )
    )
    new_owner = await db_session.get(User, new_case.user_id)
    await db_session.refresh(new_owner)
    assert new_owner.wecom_user_id == "customer-new-17", "新一期要认到真正的新客户名下"
    assert new_owner.name == "新同学"

    # 新客户后来自己退群，也能对上号让位给再下一位
    await engine.handle_message(
        _quit_notice(group_id, member_id="customer-new-17", message_id="quit-17b")
    )
    await db_session.refresh(new_case)
    assert new_case.member_left_at is not None


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
async def test_invite_notice_frees_the_group_when_the_quit_notice_was_lost(
    db_session, mock_message_channel
):
    """退群通知丢了也不怕：拉新人进群那一刻当场核对，发现原客户已走就立即让位。"""
    group_id = "G-REUSE-12"
    old_case = await _setup_group_with_customer(db_session, group_id)
    engine = RanrenAIEngine(mock_message_channel)

    async def fake_batch_get_room_members(room_ids):
        # 名单里只剩班主任和新来的人，原客户早就不在了（那条退群通知没收到）
        return {rid: {STAFF_WECOM_ID, "customer-new-12"} for rid in room_ids}

    mock_message_channel.batch_get_room_members = fake_batch_get_room_members

    join_notice = ChannelMessage(
        channel="qiwe",
        chat_id=group_id,
        chat_type="group",
        text="邀请 新同学 进群",
        user_id=STAFF_WECOM_ID,
        user_name="班主任",
        message_type="system_notice",
        message_id="join-12",
        media_card={"kind": "member_join", "member_ids": "customer-new-12"},
        raw_payload={"userId": "bot-1", "msgData": {}},
    )
    await engine.handle_message(join_notice)
    await db_session.refresh(old_case)
    assert old_case.member_left_at is not None, "进群那一刻就该发现原客户已经走了"


@pytest.mark.asyncio
async def test_invite_notice_keeps_the_case_when_the_customer_is_still_there(
    db_session, mock_message_channel
):
    """客户还在群里（只是又拉了个家属进来）：绝不能因为一条进群通知就让位。"""
    group_id = "G-REUSE-13"
    case = await _setup_group_with_customer(db_session, group_id)
    engine = RanrenAIEngine(mock_message_channel)

    async def fake_batch_get_room_members(room_ids):
        return {rid: {STAFF_WECOM_ID, CUSTOMER_WECOM_ID, "family-13"} for rid in room_ids}

    mock_message_channel.batch_get_room_members = fake_batch_get_room_members

    await engine.handle_message(
        ChannelMessage(
            channel="qiwe",
            chat_id=group_id,
            chat_type="group",
            text="邀请 家属 进群",
            user_id=STAFF_WECOM_ID,
            user_name="班主任",
            message_type="system_notice",
            message_id="join-13",
            media_card={"kind": "member_join", "member_ids": "family-13"},
            raw_payload={"userId": "bot-1", "msgData": {}},
        )
    )
    await db_session.refresh(case)
    assert case.member_left_at is None


@pytest.mark.asyncio
async def test_each_period_sees_only_its_own_chat_history(db_session, admin_client):
    """每一期的聊天记录是首尾相接的闭区间：上一期看不到后来的对话，新一期能看到前情。

    这条是真出过问题的：聊天记录按「群 + 从建单起」查且没有上界，客户走后群里
    再聊的每一句都会显示在她那张单里（生产上小刺猬那个群一次混进 66 条）。
    这里用显式时间点铺数据，因为同一个测试事务里 now() 取到的是同一个值，
    拿不出真实的时间差。
    """
    from datetime import datetime, timedelta, timezone as _tz

    from app.models.message import MessagePurpose, MessageSenderType, MessageStatus

    group_id = "G-REUSE-16"
    t0 = datetime(2026, 8, 1, 10, 0, tzinfo=_tz.utc)
    left_at = t0 + timedelta(days=20)

    old_case = await _setup_group_with_customer(db_session, group_id)
    old_case.created_at = t0
    old_case.member_left_at = left_at
    new_user = User(name="新客户", wecom_user_id="customer-new-16")
    db_session.add(new_user)
    await db_session.flush()
    new_case = ServiceCase(
        case_no="REUSE-16-B",
        user_id=new_user.id,
        group_id=group_id,
        questionnaire_token="token-16-b",
        status=ServiceCaseStatus.CREATED,
        created_at=left_at + timedelta(days=2),
    )
    db_session.add(new_case)

    def _msg(content: str, at: datetime, case_id: int | None) -> Message:
        return Message(
            service_case_id=case_id,
            group_id=group_id,
            direction=MessageDirection.INBOUND,
            sender_type=MessageSenderType.USER,
            message_type="text",
            purpose=MessagePurpose.CHAT,
            status=MessageStatus.RECEIVED,
            content=content,
            created_at=at,
        )

    await db_session.flush()
    db_session.add_all(
        [
            _msg("第1天打卡", t0 + timedelta(days=1), old_case.id),
            _msg("退出了群聊", left_at, old_case.id),
            # 客户走了之后、新客户开口之前班主任的铺垫（归新一期，时间早于新单建单时刻）
            _msg("马上安排新同学", left_at + timedelta(days=1), new_case.id),
            _msg("我到了", left_at + timedelta(days=2, hours=1), new_case.id),
        ]
    )
    await db_session.commit()

    old_texts = [
        row["content"]
        for row in (await admin_client.get(f"/service-cases/{old_case.id}/messages")).json()
    ]
    assert "第1天打卡" in old_texts
    assert "退出了群聊" in old_texts, "退群那条通知属于上一期，是这个群换人的凭据"
    assert "马上安排新同学" not in old_texts, "上一期到客户离开为止，不该看到后来的对话"
    assert "我到了" not in old_texts

    new_texts = [
        row["content"]
        for row in (await admin_client.get(f"/service-cases/{new_case.id}/messages")).json()
    ]
    assert "我到了" in new_texts
    assert "马上安排新同学" in new_texts, "客户开口前班主任的铺垫属于新一期"
    assert "第1天打卡" not in new_texts, "上一位客户的打卡不能出现在新一期"


@pytest.mark.asyncio
async def test_group_name_is_frozen_after_the_customer_left(db_session, mock_message_channel):
    """老群改名交给下一位客户后，上一期的群名要停在换人那一刻，不能跟着改。"""
    from app.services.reminder_service import ReminderService

    group_id = "G-REUSE-10"
    case = await _setup_group_with_customer(db_session, group_id)
    case.group_name = "老客户-高燃人生-0801"
    await db_session.commit()

    async def fake_batch_get_room_names(room_ids):
        return {rid: "心23-高燃人生-0819" for rid in room_ids}

    mock_message_channel.batch_get_room_names = fake_batch_get_room_names
    service = ReminderService()

    await service.check_room_names_once()
    await db_session.refresh(case)
    assert case.group_name == "心23-高燃人生-0819", "客户还在群里时，改名照常同步"

    case.member_left_at = func.now()
    await db_session.commit()
    case.group_name = "老客户-高燃人生-0801"
    await db_session.commit()

    service._last_room_name_full_refresh = None
    await service.check_room_names_once()
    await db_session.refresh(case)
    assert case.group_name == "老客户-高燃人生-0801", (
        "客户已退群的那一期，群名必须冻结，否则历史记录看起来像换了个人"
    )


@pytest.mark.asyncio
async def test_reminders_from_the_previous_customer_are_cancelled(
    db_session, mock_message_channel
):
    """上一位客户设的定时提醒不能发进换了人的群里。"""
    from datetime import datetime, timedelta, timezone

    from app.models.agent_reminder_task import AgentReminderTask, AgentReminderTaskStatus
    from app.services.reminder_service import ReminderService

    group_id = "G-REUSE-11"
    case = await _setup_group_with_customer(db_session, group_id)
    case.member_left_at = func.now()
    task = AgentReminderTask(
        service_case_id=case.id,
        group_id=group_id,
        remind_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        status=AgentReminderTaskStatus.PENDING,
        content="记得喝水",
    )
    db_session.add(task)
    await db_session.commit()

    await ReminderService().check_agent_reminder_tasks_once()
    await db_session.refresh(task)
    assert task.status == AgentReminderTaskStatus.CANCELLED
    assert task.error == "customer left the group"


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


@pytest.mark.asyncio
async def test_reconciliation_does_not_cut_off_a_case_bound_to_the_wrong_person(
    db_session, mock_message_channel
):
    """服务单绑错了人时，兜底对账不能把还在群里的客户读成「已退群」。

    真实事故：建单那一刻收到的第一条是某人的退群通知，通知也算非员工发言，于是这张单
    被绑到了刚走的那个人身上；真客户从头到尾用另一个 id 打卡。对账只看绑定 id，就把
    一位服务到第 20 天、当天还在打卡的客户标成退群，打卡提醒、追问、阶段报告全停了。
    """
    from app.models.message import MessagePurpose, MessageSenderType, MessageStatus
    from app.services.reminder_service import ReminderService

    group_id = "G-REUSE-18"
    case = await _setup_group_with_customer(db_session, group_id)  # 绑的是 CUSTOMER_WECOM_ID
    real_customer_id = "customer-real-18"

    # 绑定的那个 id 只留下一条退群通知；真客户一直在正常说话
    db_session.add_all(
        [
            Message(
                service_case_id=case.id,
                group_id=group_id,
                direction=MessageDirection.INBOUND,
                sender_type=MessageSenderType.USER,
                message_type="system_notice",
                purpose=MessagePurpose.CHAT,
                status=MessageStatus.RECEIVED,
                content="退出了群聊",
                analysis_json={"_qiwe_meta": {"sender_id": CUSTOMER_WECOM_ID}},
            ),
            Message(
                service_case_id=case.id,
                group_id=group_id,
                direction=MessageDirection.INBOUND,
                sender_type=MessageSenderType.USER,
                message_type="text",
                purpose=MessagePurpose.CHAT,
                status=MessageStatus.RECEIVED,
                content="今日体重57.4",
                analysis_json={"_qiwe_meta": {"sender_id": real_customer_id}},
            ),
        ]
    )
    await db_session.flush()

    async def fake_batch_get_room_members(room_ids):
        # 群里是真客户和班主任，绑错的那个 id 早就不在了
        return {rid: {STAFF_WECOM_ID, real_customer_id} for rid in room_ids}

    mock_message_channel.batch_get_room_members = fake_batch_get_room_members
    await ReminderService().check_group_membership_once()

    await db_session.refresh(case)
    assert case.member_left_at is None, "客户本人还在群里，绝不能标成已退群"
