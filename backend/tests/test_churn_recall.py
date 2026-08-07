"""流失召回：服务中但用户本人长期沉默的服务单，自动在群里 @ 他发一条召回消息。

沉默口径与看板「流失用户」一致——只算用户本人发言，班主任跟进不解除沉默。
同一轮沉默最多 2 条、间隔 7 天；用户一回话计数归零。

最关键的一条回归在文件末尾：**发送失败绝不写台账**。打卡追问踩过反向的坑——
它在发送前就占位，通道故障两天就把用户的追问永久关掉，用户一条都没收到。
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models.message import (
    Message,
    MessageDirection,
    MessagePurpose,
    MessageSenderType,
    MessageStatus,
)
from app.models.reminder import ReminderEvent, ReminderEventType
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user import User
from app.services.agent.graph import AgentReplyResult
from app.services.channels.base import ChannelSendResult
from app.services.channels.registry import set_current_channel
from app.services.reminder_service import CHURN_RECALL_RULE_ID, ReminderService

TZ = ZoneInfo("Asia/Shanghai")


def _settings(**overrides):
    """默认关闭的功能在测试里显式打开，不污染 get_settings 的全局缓存。"""
    return get_settings().model_copy(
        update={"churn_recall_enabled": True, **overrides}
    )


def _in_window() -> datetime:
    """落在发送窗口 10:00-11:00 内、且一定晚于任何按 case 散列出的目标时刻。"""
    return datetime.now(TZ).replace(hour=10, minute=59, second=59, microsecond=0)


def _base_utc() -> datetime:
    """所有相对时间的基准 = 调度器眼里的「现在」。

    用真实 now 会和窗口时刻差几小时，让天数在截断时少 1，测试就变得看跑在几点。
    """
    return _in_window().astimezone(timezone.utc)


class OkChannel:
    name = "qiwe"

    def __init__(self):
        self.sent = []

    async def send_text(self, chat_id, content, **kwargs):
        self.sent.append({"chat_id": chat_id, "content": content})
        return ChannelSendResult(success=True, message_id="ok-id")

    async def send_hypertext(self, chat_id, content, **kwargs):
        self.sent.append({"chat_id": chat_id, "content": content, "hypertext": True})
        return ChannelSendResult(success=True, message_id="ok-hyper-id")


class DeadChannel:
    name = "qiwe"

    async def send_text(self, chat_id, content, **kwargs):
        return ChannelSendResult(
            success=False, error="guid错误: 客户端实例不存在/不在线", retryable=False
        )

    async def send_hypertext(self, chat_id, content, **kwargs):
        return ChannelSendResult(
            success=False, error="guid错误: 客户端实例不存在/不在线", retryable=False
        )


async def _case(
    db_session,
    *,
    status=ServiceCaseStatus.IN_SERVICE,
    ai_auto_reply_enabled: bool = True,
    deleted: bool = False,
) -> ServiceCase:
    user = User(name="召回测试用户")
    db_session.add(user)
    await db_session.flush()
    now = _base_utc()
    service_case = ServiceCase(
        case_no=f"RECALL-{secrets.token_hex(4).upper()}",
        user_id=user.id,
        group_id=f"recall-{secrets.token_hex(4)}",
        questionnaire_token=secrets.token_urlsafe(16),
        status=status,
        started_at=now - timedelta(days=20),
        created_at=now - timedelta(days=20),
        ai_auto_reply_enabled=ai_auto_reply_enabled,
        deleted_at=now if deleted else None,
    )
    db_session.add(service_case)
    await db_session.flush()
    return service_case


async def _said(
    db_session,
    service_case: ServiceCase,
    *,
    days_ago: float,
    sender_type=MessageSenderType.USER,
    qiwe_sender: tuple[str, str] | None = None,
) -> Message:
    analysis = None
    if qiwe_sender:
        analysis = {
            "_qiwe_meta": {"sender_id": qiwe_sender[0], "sender_name": qiwe_sender[1]}
        }
    message = Message(
        service_case_id=service_case.id,
        group_id=service_case.group_id,
        direction=MessageDirection.INBOUND,
        sender_type=sender_type,
        message_type="text",
        content="随便说点什么",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
        created_at=_base_utc() - timedelta(days=days_ago),
        analysis_json=analysis,
    )
    db_session.add(message)
    await db_session.flush()
    return message


async def _past_recall(db_session, service_case: ServiceCase, *, days_ago: float):
    """伪造一条历史召回台账。"""
    stamp = _base_utc() - timedelta(days=days_ago)
    event = ReminderEvent(
        service_case_id=service_case.id,
        rule_id=CHURN_RECALL_RULE_ID,
        event_date=stamp.date(),
        event_type=ReminderEventType.REMINDED,
        created_at=stamp,
    )
    db_session.add(event)
    await db_session.flush()
    return event


async def _recalls(db_session, service_case: ServiceCase) -> list[ReminderEvent]:
    return list(
        await db_session.scalars(
            select(ReminderEvent).where(
                ReminderEvent.service_case_id == service_case.id,
                ReminderEvent.rule_id == CHURN_RECALL_RULE_ID,
            )
        )
    )


async def _sent_messages(db_session, service_case: ServiceCase) -> list[Message]:
    return list(
        await db_session.scalars(
            select(Message).where(
                Message.service_case_id == service_case.id,
                Message.direction == MessageDirection.OUTBOUND,
            )
        )
    )


@pytest.fixture
def recall_agent(monkeypatch):
    """替掉真实模型调用，记录每次生成请求的参数。"""
    calls = []

    async def fake_generate(**kwargs):
        calls.append(kwargs)
        return AgentReplyResult(
            reply="这两天先只拍一张餐照就行。要不要安排一次和燃总的电话或视频聊一次？",
            tool_trace=[{"name": "get_service_progress", "ok": True}],
        )

    monkeypatch.setattr(
        "app.services.reminder_service.generate_churn_recall_reply", fake_generate
    )
    return calls


@pytest.fixture
def channel():
    ok = OkChannel()
    set_current_channel(ok)
    yield ok
    set_current_channel(None)


@pytest.mark.asyncio
async def test_recalls_case_silent_for_seven_days(db_session, recall_agent, channel):
    service_case = await _case(db_session)
    await _said(db_session, service_case, days_ago=8, qiwe_sender=("wx-u1", "小王"))

    await ReminderService(_settings()).check_churn_recall_once(now=_in_window())

    assert len(recall_agent) == 1
    assert recall_agent[0]["service_case_id"] == service_case.id
    assert recall_agent[0]["silent_days"] == 8

    messages = await _sent_messages(db_session, service_case)
    assert len(messages) == 1
    assert messages[0].status == MessageStatus.SENT
    assert messages[0].purpose == MessagePurpose.REMINDER
    assert messages[0].analysis_json["churn_recall"]["silent_days"] == 8
    assert messages[0].analysis_json["churn_recall"]["sent_in_silence"] == 1
    # 能拿到企微身份就走原生 @，把消息直接推到本人面前
    assert channel.sent[-1].get("hypertext") is True
    assert len(await _recalls(db_session, service_case)) == 1


@pytest.mark.asyncio
async def test_skips_case_silent_for_six_days(db_session, recall_agent, channel):
    service_case = await _case(db_session)
    await _said(db_session, service_case, days_ago=6)

    await ReminderService(_settings()).check_churn_recall_once(now=_in_window())

    assert recall_agent == []
    assert await _sent_messages(db_session, service_case) == []


@pytest.mark.asyncio
async def test_staff_messages_do_not_reset_silence(db_session, recall_agent, channel):
    """班主任昨天在群里跟进过，用户本人仍然沉默 9 天——照样召回。"""
    service_case = await _case(db_session)
    await _said(db_session, service_case, days_ago=9)
    await _said(
        db_session, service_case, days_ago=1, sender_type=MessageSenderType.STAFF
    )

    await ReminderService(_settings()).check_churn_recall_once(now=_in_window())

    assert len(recall_agent) == 1
    assert recall_agent[0]["silent_days"] == 9


@pytest.mark.asyncio
async def test_second_recall_waits_for_the_interval(db_session, recall_agent, channel):
    service_case = await _case(db_session)
    await _said(db_session, service_case, days_ago=13)
    await _past_recall(db_session, service_case, days_ago=6)

    await ReminderService(_settings()).check_churn_recall_once(now=_in_window())

    assert recall_agent == []
    assert len(await _recalls(db_session, service_case)) == 1


@pytest.mark.asyncio
async def test_second_recall_sends_after_the_interval(db_session, recall_agent, channel):
    service_case = await _case(db_session)
    await _said(db_session, service_case, days_ago=14)
    await _past_recall(db_session, service_case, days_ago=7)

    await ReminderService(_settings()).check_churn_recall_once(now=_in_window())

    assert len(recall_agent) == 1
    messages = await _sent_messages(db_session, service_case)
    assert messages[0].analysis_json["churn_recall"]["sent_in_silence"] == 2
    assert len(await _recalls(db_session, service_case)) == 2


@pytest.mark.asyncio
async def test_stops_after_two_recalls_in_one_silence(db_session, recall_agent, channel):
    service_case = await _case(db_session)
    await _said(db_session, service_case, days_ago=21)
    await _past_recall(db_session, service_case, days_ago=14)
    await _past_recall(db_session, service_case, days_ago=7)

    await ReminderService(_settings()).check_churn_recall_once(now=_in_window())

    assert recall_agent == []
    assert len(await _recalls(db_session, service_case)) == 2


@pytest.mark.asyncio
async def test_counter_resets_once_the_user_replies(db_session, recall_agent, channel):
    """用户回过话，之前那两条召回就不算这一轮的——他再次沉默时还能被捞一次。"""
    service_case = await _case(db_session)
    await _past_recall(db_session, service_case, days_ago=20)
    await _past_recall(db_session, service_case, days_ago=13)
    await _said(db_session, service_case, days_ago=8)  # 回话晚于两条召回

    await ReminderService(_settings()).check_churn_recall_once(now=_in_window())

    assert len(recall_agent) == 1
    messages = await _sent_messages(db_session, service_case)
    assert messages[0].analysis_json["churn_recall"]["sent_in_silence"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case_kwargs",
    [
        {"ai_auto_reply_enabled": False},  # 群里说过「退下」
        {"status": ServiceCaseStatus.COMPLETED},  # 服务已结束
        {"deleted": True},  # 已软删
    ],
)
async def test_skips_cases_that_must_not_be_messaged(
    db_session, recall_agent, channel, case_kwargs
):
    service_case = await _case(db_session, **case_kwargs)
    await _said(db_session, service_case, days_ago=10)

    await ReminderService(_settings()).check_churn_recall_once(now=_in_window())

    assert recall_agent == []
    assert await _sent_messages(db_session, service_case) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("hour", [3, 23])
async def test_skips_outside_the_sending_window(db_session, recall_agent, channel, hour):
    """窗口前后都不能发。

    23 点这个点位是必要的：早于窗口时「按 case 散列出的时刻还没到」也会拦下来，
    只有晚于窗口的时间才真正验证窗口上界本身在起作用。
    """
    service_case = await _case(db_session)
    await _said(db_session, service_case, days_ago=10)
    off_window = datetime.now(TZ).replace(
        hour=hour, minute=0, second=0, microsecond=0
    )

    await ReminderService(_settings()).check_churn_recall_once(now=off_window)

    assert recall_agent == []
    assert await _sent_messages(db_session, service_case) == []


@pytest.mark.asyncio
async def test_disabled_switch_sends_nothing(db_session, recall_agent, channel):
    service_case = await _case(db_session)
    await _said(db_session, service_case, days_ago=10)

    await ReminderService(
        _settings(churn_recall_enabled=False)
    ).check_churn_recall_once(now=_in_window())

    assert recall_agent == []
    assert await _sent_messages(db_session, service_case) == []


@pytest.mark.asyncio
async def test_second_pass_same_day_does_not_send_twice(db_session, recall_agent, channel):
    service_case = await _case(db_session)
    await _said(db_session, service_case, days_ago=10)
    service = ReminderService(_settings())

    await service.check_churn_recall_once(now=_in_window())
    await service.check_churn_recall_once(now=_in_window())

    assert len(recall_agent) == 1
    assert len(await _sent_messages(db_session, service_case)) == 1
    assert len(await _recalls(db_session, service_case)) == 1


@pytest.mark.asyncio
async def test_send_failure_writes_no_ledger_and_retries_later(db_session, recall_agent):
    """通道故障时绝不能记「发过了」——否则两次故障就把这个人的召回名额白白烧光。

    这是打卡追问那个 bug 的反向回归：它在发送前占位，故障两天即永久静音用户。
    """
    set_current_channel(DeadChannel())
    try:
        service_case = await _case(db_session)
        await _said(db_session, service_case, days_ago=10)

        await ReminderService(_settings()).check_churn_recall_once(now=_in_window())

        messages = await _sent_messages(db_session, service_case)
        assert len(messages) == 1
        assert messages[0].status == MessageStatus.FAILED
        assert "不在线" in (messages[0].error or "")
        assert await _recalls(db_session, service_case) == []

        # 通道恢复后照常召回，名额一个没少
        ok = OkChannel()
        set_current_channel(ok)
        await ReminderService(_settings()).check_churn_recall_once(now=_in_window())

        assert len(recall_agent) == 2
        sent = [m for m in await _sent_messages(db_session, service_case) if m.status == MessageStatus.SENT]
        assert len(sent) == 1
        assert sent[0].analysis_json["churn_recall"]["sent_in_silence"] == 1
        assert len(await _recalls(db_session, service_case)) == 1
    finally:
        set_current_channel(None)


@pytest.mark.asyncio
async def test_generation_failure_writes_no_ledger(db_session, monkeypatch, channel):
    """模型生成炸了同样不占名额，也不能留下一条空消息。"""
    service_case = await _case(db_session)
    await _said(db_session, service_case, days_ago=10)

    async def boom(**kwargs):
        raise RuntimeError("model exploded")

    monkeypatch.setattr(
        "app.services.reminder_service.generate_churn_recall_reply", boom
    )

    await ReminderService(_settings()).check_churn_recall_once(now=_in_window())

    assert await _sent_messages(db_session, service_case) == []
    assert await _recalls(db_session, service_case) == []


@pytest.mark.asyncio
async def test_case_without_any_user_message_uses_creation_time(
    db_session, recall_agent, channel
):
    """从进群起就没说过话的人，从建单时间起算沉默，同样会被召回（纯文本，无 @ 目标）。"""
    service_case = await _case(db_session)

    await ReminderService(_settings()).check_churn_recall_once(now=_in_window())

    assert len(recall_agent) == 1
    assert recall_agent[0]["silent_days"] == 20
    assert channel.sent[-1].get("hypertext") is None
