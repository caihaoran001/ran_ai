from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select

from app.models.curated_video import (
    CuratedVideoDailyRun,
    CuratedVideoDelivery,
    CuratedVideoItem,
    CuratedVideoSourceCursor,
)
from app.models.inbound_event import InboundEvent
from app.models.message import Message, MessageStatus
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.services.channels.base import ChannelSendResult
from app.services.channels.send_context import notify_send_request_started
from app.services.curated_video_distribution import (
    CuratedVideoDistributionService,
    ingest_curated_video_events,
)
from app.services.qiwe_service.inbox_dispatcher import QiWeInboxDispatcher


SOURCE_GROUP_ID = "10928203839633338"


def _settings(source_group_id: str = SOURCE_GROUP_ID):
    return SimpleNamespace(
        curated_video_enabled=True,
        curated_video_source_group_id=source_group_id,
        curated_video_send_at="12:00",
        reminder_timezone="Asia/Shanghai",
    )


def _feed(index: int) -> dict[str, str]:
    return {
        "channelName": f"第 {index} 条视频",
        "channelUrl": f"https://channels.example/video/{index}",
        "coverUrl": (
            f"https://channels.example/cover/{index}.jpg?finder_expire_time=2000000000"
        ),
        "encodeData": f"encoded-{index}",
        "headImgUrl": "https://channels.example/avatar.jpg",
        "feedId": f"feed-{index}",
        "feedNo": f"feed-no-{index}",
        "username": "finder-user",
    }


def _event(
    index: int,
    received_at: datetime,
    *,
    source_group_id: str = SOURCE_GROUP_ID,
) -> InboundEvent:
    return InboundEvent(
        event_type=InboundEvent.EVENT_MESSAGE,
        dedupe_key=f"curated-video-event-{index}",
        channel="qiwe",
        chat_id=source_group_id,
        user_id="operator-1",
        message_id=f"source-message-{index}",
        text=f"[视频号] 第 {index} 条视频",
        message_type="video_channel",
        raw_payload={
            "cmd": 15000,
            "msgType": 141,
            "msgData": _feed(index),
        },
        message_timestamp=received_at,
        status=InboundEvent.STATUS_PROCESSED,
    )


class RecordingChannel:
    name = "qiwe"

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def send_feed_video(
        self,
        chat_id: str,
        feed: dict[str, str],
    ) -> ChannelSendResult:
        await notify_send_request_started()
        self.calls.append((chat_id, dict(feed)))
        return ChannelSendResult(
            success=True,
            message_id=f"sent-{len(self.calls)}",
            raw_response={
                "code": 0,
                "data": {"msgUniqueIdentifier": f"sent-{len(self.calls)}"},
            },
        )


class RecordingAggregator:
    def __init__(self) -> None:
        self.messages = []

    async def enqueue(self, message, *, inbound_event_id=None) -> None:
        self.messages.append((message, inbound_event_id))


class UncertainChannel(RecordingChannel):
    async def send_feed_video(
        self,
        chat_id: str,
        feed: dict[str, str],
    ) -> ChannelSendResult:
        await notify_send_request_started()
        self.calls.append((chat_id, dict(feed)))
        return ChannelSendResult(
            success=False,
            error="connection reset after request started",
            retryable=True,
            delivery_uncertain=True,
        )


class CrashBeforeRequestChannel(RecordingChannel):
    async def send_feed_video(
        self,
        chat_id: str,
        feed: dict[str, str],
    ) -> ChannelSendResult:
        raise asyncio.CancelledError()


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def clean_curated_video_tables(db_session):
    """开跑前把视频推送这几张表清空，用例不再受开发机上的历史数据影响。

    这里的用例把日期钉死在 2026-08-12，而"每天一批推送"这件事一天只允许一条记录。
    本机跑着开发环境的话，它每天中午都会往同一个库里真记一条，跑测试时就会撞上：
    代码看到"这天已经有批次了"直接返回旧那条，断言读到的是历史结果而不是本次行为，
    于是一批用例莫名变红（本机曾攒下 8/12–8/19 共 8 条）。

    删除只发生在测试自己的事务里，跑完随事务回滚，开发库里的数据不会真掉。
    """
    for model in (
        CuratedVideoDelivery,
        CuratedVideoDailyRun,
        CuratedVideoItem,
        CuratedVideoSourceCursor,
    ):
        await db_session.execute(delete(model))
    await db_session.flush()
    yield


@pytest.mark.asyncio
async def test_first_ingest_sets_activation_cursor_without_importing_history(db_session):
    settings = _settings()
    old_event = _event(1, datetime(2026, 8, 12, 2, 0, tzinfo=timezone.utc))
    db_session.add(old_event)
    await db_session.flush()

    inserted = await ingest_curated_video_events(settings)

    cursor = await db_session.scalar(
        select(CuratedVideoSourceCursor).where(
            CuratedVideoSourceCursor.source_group_id == SOURCE_GROUP_ID
        )
    )
    assert inserted == 0
    assert cursor is not None
    assert cursor.last_inbound_event_id == old_event.id
    assert await db_session.scalar(select(func.count(CuratedVideoItem.id))) == 0


@pytest.mark.asyncio
async def test_source_group_is_not_dispatched_as_a_customer_case(db_session, monkeypatch):
    settings = _settings()
    monkeypatch.setattr(
        "app.services.qiwe_service.inbox_dispatcher.get_settings",
        lambda: settings,
    )
    event = _event(3, datetime(2026, 8, 12, 2, 0, tzinfo=timezone.utc))
    event.status = InboundEvent.STATUS_PROCESSING
    db_session.add(event)
    await db_session.flush()
    case_count_before = await db_session.scalar(select(func.count(ServiceCase.id)))
    aggregator = RecordingAggregator()
    dispatcher = QiWeInboxDispatcher(aggregator)

    await dispatcher._dispatch(event)

    stored = await db_session.get(InboundEvent, event.id)
    assert stored.status == InboundEvent.STATUS_PROCESSED
    assert stored.business_message_id is None
    assert aggregator.messages == []
    assert await db_session.scalar(select(func.count(ServiceCase.id))) == case_count_before


@pytest.mark.asyncio
async def test_source_group_regular_text_still_enters_customer_flow(db_session, monkeypatch):
    # 选品源群只拦视频号卡片(msgType=141)；普通文本必须继续走客服链路（进聚合窗口）。
    settings = _settings()
    monkeypatch.setattr(
        "app.services.qiwe_service.inbox_dispatcher.get_settings",
        lambda: settings,
    )
    event = _event(4, datetime(2026, 8, 12, 2, 0, tzinfo=timezone.utc))
    event.raw_payload = {
        "cmd": 15000,
        "msgType": 2,
        "msgData": {"content": "这个适合减脂嘛 怎么自己做"},
    }
    event.text = "这个适合减脂嘛 怎么自己做"
    event.message_type = "text"
    event.status = InboundEvent.STATUS_PROCESSING
    db_session.add(event)
    await db_session.flush()
    aggregator = RecordingAggregator()
    dispatcher = QiWeInboxDispatcher(aggregator)

    await dispatcher._dispatch(event)

    assert len(aggregator.messages) == 1
    message, inbound_event_id = aggregator.messages[0]
    assert inbound_event_id == event.id
    assert message.chat_id == SOURCE_GROUP_ID
    assert message.message_type == "text"
    assert message.text == "这个适合减脂嘛 怎么自己做"


@pytest.mark.asyncio
async def test_switching_source_group_does_not_send_old_group_queue(db_session, create_case):
    old_settings = _settings()
    await ingest_curated_video_events(old_settings)
    db_session.add(_event(1, datetime(2026, 8, 12, 1, 0, tzinfo=timezone.utc)))
    await db_session.flush()
    assert await ingest_curated_video_events(old_settings) == 1

    customer_source_group = "customer-curation-room"
    new_settings = _settings(customer_source_group)
    await ingest_curated_video_events(new_settings)
    db_session.add(
        _event(
            2,
            datetime(2026, 8, 12, 2, 0, tzinfo=timezone.utc),
            source_group_id=customer_source_group,
        )
    )
    await db_session.flush()
    assert await ingest_curated_video_events(new_settings) == 1
    active = await create_case({"group_id": "target-after-switch", "user": {"name": "用户"}})
    service_case = await db_session.get(ServiceCase, active.json()["id"])
    service_case.status = ServiceCaseStatus.IN_SERVICE
    channel = RecordingChannel()

    await CuratedVideoDistributionService(channel, new_settings).check_once(
        datetime(2026, 8, 12, 4, 0, tzinfo=timezone.utc)
    )

    assert channel.calls == [("target-after-switch", _feed(2))]
    items = list(
        await db_session.scalars(
            select(CuratedVideoItem).order_by(CuratedVideoItem.id)
        )
    )
    assert [item.source_group_id for item in items] == [
        SOURCE_GROUP_ID,
        customer_source_group,
    ]
    assert [item.status for item in items] == ["queued", "completed"]


@pytest.mark.asyncio
async def test_daily_distribution_is_fifo_one_item_per_day_and_active_groups_only(
    db_session,
    create_case,
):
    settings = _settings()
    await ingest_curated_video_events(settings)

    active_one = await create_case({"group_id": "target-room-1", "user": {"name": "用户一"}})
    active_two = await create_case({"group_id": "target-room-2", "user": {"name": "用户二"}})
    inactive = await create_case({"group_id": "inactive-room", "user": {"name": "用户三"}})
    for case_id in (active_one.json()["id"], active_two.json()["id"]):
        service_case = await db_session.get(ServiceCase, case_id)
        service_case.status = ServiceCaseStatus.IN_SERVICE
    inactive_case = await db_session.get(ServiceCase, inactive.json()["id"])
    inactive_case.status = ServiceCaseStatus.COMPLETED

    first_received = datetime(2026, 8, 12, 2, 0, tzinfo=timezone.utc)
    db_session.add_all(
        [
            _event(1, first_received),
            _event(2, first_received + timedelta(minutes=1)),
        ]
    )
    await db_session.flush()
    assert await ingest_curated_video_events(settings) == 2

    channel = RecordingChannel()
    service = CuratedVideoDistributionService(channel, settings)
    first_noon = datetime(2026, 8, 12, 4, 0, tzinfo=timezone.utc)
    await service.check_once(first_noon - timedelta(minutes=1))
    assert channel.calls == []
    assert await db_session.scalar(select(func.count(CuratedVideoDailyRun.id))) == 0
    await service.check_once(first_noon)
    restarted_service = CuratedVideoDistributionService(channel, settings)
    await restarted_service.check_once(first_noon + timedelta(hours=2))

    assert channel.calls == [
        ("target-room-1", _feed(1)),
        ("target-room-2", _feed(1)),
    ]
    first_run = await db_session.scalar(
        select(CuratedVideoDailyRun).where(CuratedVideoDailyRun.run_date == first_noon.date())
    )
    assert first_run is not None
    assert first_run.status == "completed"
    assert first_run.target_count == 2
    assert first_run.sent_count == 2
    assert first_run.failed_count == 0

    deliveries = list(
        await db_session.scalars(
            select(CuratedVideoDelivery)
            .where(CuratedVideoDelivery.daily_run_id == first_run.id)
            .order_by(CuratedVideoDelivery.id)
        )
    )
    assert [delivery.group_id for delivery in deliveries] == [
        "target-room-1",
        "target-room-2",
    ]
    assert all(delivery.status == "sent" for delivery in deliveries)
    assert all(delivery.attempt_count == 1 for delivery in deliveries)
    assert all(delivery.outbound_message_id is not None for delivery in deliveries)
    for delivery in deliveries:
        outbound = await db_session.get(Message, delivery.outbound_message_id)
        assert outbound is not None
        assert outbound.status == MessageStatus.SENT

    second_noon = first_noon + timedelta(days=1)
    await restarted_service.check_once(second_noon)

    assert channel.calls[-2:] == [
        ("target-room-1", _feed(2)),
        ("target-room-2", _feed(2)),
    ]
    assert len(channel.calls) == 4
    items = list(
        await db_session.scalars(
            select(CuratedVideoItem).order_by(CuratedVideoItem.received_at, CuratedVideoItem.id)
        )
    )
    assert [item.feed_payload["feedId"] for item in items] == ["feed-1", "feed-2"]
    assert [item.status for item in items] == ["completed", "completed"]


@pytest.mark.asyncio
async def test_noon_without_content_reserves_the_day(db_session, create_case):
    settings = _settings()
    await ingest_curated_video_events(settings)
    active = await create_case({"group_id": "target-room-late", "user": {"name": "用户"}})
    service_case = await db_session.get(ServiceCase, active.json()["id"])
    service_case.status = ServiceCaseStatus.IN_SERVICE
    channel = RecordingChannel()
    service = CuratedVideoDistributionService(channel, settings)
    noon = datetime(2026, 8, 12, 4, 0, tzinfo=timezone.utc)

    await service.check_once(noon)
    db_session.add(_event(9, noon + timedelta(minutes=1)))
    await db_session.flush()
    await service.check_once(noon + timedelta(minutes=2))

    run = await db_session.scalar(
        select(CuratedVideoDailyRun).where(CuratedVideoDailyRun.run_date == noon.date())
    )
    assert run is not None
    assert run.status == "no_content"
    assert channel.calls == []
    queued = await db_session.scalar(
        select(CuratedVideoItem).where(CuratedVideoItem.source_message_id == "source-message-9")
    )
    assert queued is not None
    assert queued.status == "queued"


@pytest.mark.asyncio
async def test_uncertain_delivery_is_not_retried_blindly(db_session, create_case):
    settings = _settings()
    await ingest_curated_video_events(settings)
    active = await create_case({"group_id": "target-room-unknown", "user": {"name": "用户"}})
    service_case = await db_session.get(ServiceCase, active.json()["id"])
    service_case.status = ServiceCaseStatus.IN_SERVICE
    db_session.add(_event(7, datetime(2026, 8, 12, 2, 0, tzinfo=timezone.utc)))
    await db_session.flush()
    assert await ingest_curated_video_events(settings) == 1
    channel = UncertainChannel()
    noon = datetime(2026, 8, 12, 4, 0, tzinfo=timezone.utc)

    await CuratedVideoDistributionService(channel, settings).check_once(noon)
    await CuratedVideoDistributionService(channel, settings).check_once(
        noon + timedelta(hours=1)
    )

    assert channel.calls == [("target-room-unknown", _feed(7))]
    delivery = await db_session.scalar(
        select(CuratedVideoDelivery).where(
            CuratedVideoDelivery.group_id == "target-room-unknown"
        )
    )
    assert delivery is not None
    assert delivery.status == "unknown"
    assert delivery.attempt_count == 1
    outbound = await db_session.get(Message, delivery.outbound_message_id)
    assert outbound is not None
    assert outbound.status == MessageStatus.UNKNOWN


@pytest.mark.asyncio
async def test_stale_pre_request_claim_resumes_after_restart(db_session, create_case):
    settings = _settings()
    await ingest_curated_video_events(settings)
    active = await create_case({"group_id": "target-room-resume", "user": {"name": "用户"}})
    service_case = await db_session.get(ServiceCase, active.json()["id"])
    service_case.status = ServiceCaseStatus.IN_SERVICE
    db_session.add(_event(8, datetime(2026, 8, 12, 2, 0, tzinfo=timezone.utc)))
    await db_session.flush()
    assert await ingest_curated_video_events(settings) == 1
    noon = datetime(2026, 8, 12, 4, 0, tzinfo=timezone.utc)

    with pytest.raises(asyncio.CancelledError):
        await CuratedVideoDistributionService(
            CrashBeforeRequestChannel(),
            settings,
        ).check_once(noon)

    delivery = await db_session.scalar(
        select(CuratedVideoDelivery).where(
            CuratedVideoDelivery.group_id == "target-room-resume"
        )
    )
    assert delivery is not None
    assert delivery.status == "queued"
    assert delivery.attempt_count == 0
    outbound = await db_session.get(Message, delivery.outbound_message_id)
    assert outbound is not None
    assert outbound.status == MessageStatus.PENDING

    resumed_channel = RecordingChannel()
    recovery_now = delivery.updated_at + timedelta(minutes=3)
    await CuratedVideoDistributionService(resumed_channel, settings).check_once(
        recovery_now
    )

    assert resumed_channel.calls == [("target-room-resume", _feed(8))]
    await db_session.refresh(delivery)
    assert delivery.status == "sent"
    assert delivery.attempt_count == 1
