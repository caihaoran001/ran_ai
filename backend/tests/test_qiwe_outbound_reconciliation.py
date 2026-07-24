from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone

import pytest

from app.models.message import (
    Message,
    MessageDirection,
    MessagePurpose,
    MessageSenderType,
    MessageStatus,
)
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user import User
from app.services.channels.qiwe import QiWeHistoryPage


class FakeHistoryChannel:
    def __init__(
        self,
        items: list[dict],
        *,
        profile: dict | None = None,
        error: Exception | None = None,
        has_more: bool = False,
        responses: list[QiWeHistoryPage] | None = None,
    ):
        self.items = items
        self.profile = {"userId": "bot-1"} if profile is None else profile
        self.error = error
        self.has_more = has_more
        self.responses = responses
        self.calls: list[tuple[int, int]] = []
        self.profile_calls = 0

    async def get_profile(self):
        self.profile_calls += 1
        return self.profile

    async def sync_message_history(self, *, after_seq: int, limit: int = 50):
        self.calls.append((after_seq, limit))
        if self.error is not None:
            raise self.error
        if self.responses:
            index = min(len(self.calls) - 1, len(self.responses) - 1)
            return self.responses[index]
        return QiWeHistoryPage(
            items=self.items,
            has_more=self.has_more,
            travel_sync_key=None,
        )


def _sent_anchor(*, group_id: str, created_at: datetime, seq: int) -> Message:
    return Message(
        group_id=group_id,
        content="上一条已确认消息",
        message_type="markdown",
        direction=MessageDirection.OUTBOUND,
        sender_type=MessageSenderType.BOT,
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.SENT,
        sent_at=created_at,
        created_at=created_at,
        analysis_json={
            "_delivery": {
                "provider_receipt": {
                    "msgUniqueIdentifier": f"anchor-{seq}",
                    "seq": seq,
                    "timestamp": int(created_at.timestamp()),
                }
            }
        },
    )


def _unknown(
    *,
    group_id: str,
    content: str,
    request_started_at: datetime,
    service_case_id: int | None = None,
    trigger_id: int | None = None,
) -> Message:
    return Message(
        service_case_id=service_case_id,
        group_id=group_id,
        content=content,
        message_type="markdown",
        direction=MessageDirection.OUTBOUND,
        sender_type=MessageSenderType.BOT,
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.UNKNOWN,
        error="read timeout",
        trigger_id=trigger_id,
        created_at=request_started_at,
        analysis_json={
            "_delivery": {
                "state_machine": "agent_outbound_v1",
                "request_started_at": request_started_at.isoformat(),
                "delivery_uncertain": True,
            }
        },
    )


def _history_item(
    *,
    group_id: str,
    content: str,
    sent_at: datetime,
    seq: int,
    sender_id: str = "bot-1",
) -> dict:
    return {
        "fromRoomId": group_id,
        "senderId": sender_id,
        "msgData": {"content": content},
        "msgUniqueIdentifier": f"history-{seq}",
        "msgServerId": seq + 1000,
        "seq": seq,
        "timestamp": int(sent_at.timestamp()),
    }


def _history_page(
    items: list[dict],
    *,
    has_more: bool = False,
    travel_sync_key: int | None = None,
) -> QiWeHistoryPage:
    return QiWeHistoryPage(
        items=items,
        has_more=has_more,
        travel_sync_key=travel_sync_key,
    )


def _spam_block_item(
    *,
    channel_message_id: str,
    sent_at: datetime,
    seq: int,
) -> dict:
    return {
        "cmd": 15000,
        "msgType": 1011,
        "msgUniqueIdentifier": f"spam_block_{channel_message_id}",
        "seq": seq,
        "timestamp": int(sent_at.timestamp()),
        "msgData": {
            "base64RawData": base64.b64encode(
                "账号功能被限制".encode()
            ).decode()
        },
    }


async def _recoverable_unknown(
    db_session,
    *,
    group_id: str,
    content: str,
    request_started_at: datetime,
) -> tuple[Message, Message]:
    user = User(name=f"{group_id}-用户")
    db_session.add(user)
    await db_session.flush()
    service_case = ServiceCase(
        user_id=user.id,
        case_no=f"CASE-{group_id}",
        status=ServiceCaseStatus.IN_SERVICE,
        ai_auto_reply_enabled=True,
        ai_introduced_at=request_started_at - timedelta(days=1),
        group_id=group_id,
        questionnaire_token=f"TOKEN-{group_id}",
    )
    db_session.add(service_case)
    await db_session.flush()
    inbound = Message(
        service_case_id=service_case.id,
        group_id=group_id,
        wecom_message_id=f"inbound-{group_id}",
        content="请回复我",
        message_type="text",
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.USER,
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
        analysis_json={
            "_qiwe_meta": {},
            "_reply_processing": {
                "status": "completed",
                "reason": "workflow_completed",
                "updated_at": request_started_at.isoformat(),
            },
        },
        created_at=request_started_at - timedelta(seconds=1),
    )
    db_session.add(inbound)
    await db_session.flush()
    unknown = _unknown(
        group_id=group_id,
        content=content,
        request_started_at=request_started_at,
        service_case_id=service_case.id,
        trigger_id=inbound.id,
    )
    db_session.add(unknown)
    await db_session.flush()
    return inbound, unknown


@pytest.mark.asyncio
async def test_unique_history_match_marks_only_frozen_unknown_sent(db_session):
    from app.services.qiwe_service.outbound_reconciliation import (
        reconcile_frozen_unknown_sends,
    )

    started_at = datetime.now(timezone.utc) - timedelta(seconds=90)
    anchor = _sent_anchor(
        group_id="group-reconcile",
        created_at=started_at - timedelta(seconds=10),
        seq=8817000,
    )
    unknown = _unknown(
        group_id="group-reconcile",
        content="部署中断时的回复",
        request_started_at=started_at,
    )
    db_session.add_all([anchor, unknown])
    await db_session.flush()

    # 模拟服务恢复后刚产生的新 SENDING；它不在启动冻结名单中。
    new_sending = Message(
        group_id="group-reconcile",
        content="恢复后正在发送的新回复",
        message_type="markdown",
        direction=MessageDirection.OUTBOUND,
        sender_type=MessageSenderType.BOT,
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.SENDING,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(new_sending)
    await db_session.flush()

    channel = FakeHistoryChannel([
        _history_item(
            group_id=unknown.group_id,
            content=unknown.content,
            sent_at=started_at + timedelta(seconds=1),
            seq=8817001,
        )
    ])
    result = await reconcile_frozen_unknown_sends(channel, [unknown.id])

    await db_session.refresh(unknown)
    await db_session.refresh(new_sending)
    assert channel.calls == [(8817000, 50)]
    assert result.confirmed_sent == 1
    assert unknown.status == MessageStatus.SENT
    assert unknown.error is None
    assert unknown.wecom_message_id == "history-8817001"
    assert unknown.sent_at == started_at.replace(microsecond=0) + timedelta(seconds=1)
    assert unknown.analysis_json["_delivery"]["reconciliation"] == "history_confirmed_sent"
    assert unknown.analysis_json["_delivery"]["provider_receipt"]["seq"] == 8817001
    assert new_sending.status == MessageStatus.SENDING


@pytest.mark.asyncio
async def test_empty_frozen_batch_does_not_call_qiwe(db_session):
    from app.services.qiwe_service.outbound_reconciliation import (
        reconcile_frozen_unknown_sends,
    )

    channel = FakeHistoryChannel([])
    result = await reconcile_frozen_unknown_sends(channel, [])

    assert result.frozen_count == 0
    assert result.api_called is False
    assert channel.profile_calls == 0
    assert channel.calls == []


@pytest.mark.asyncio
async def test_history_zero_match_without_trigger_is_failed_not_retried(db_session):
    from app.services.qiwe_service.outbound_reconciliation import (
        reconcile_frozen_unknown_sends,
    )

    started_at = datetime.now(timezone.utc) - timedelta(seconds=60)
    anchor = _sent_anchor(
        group_id="group-zero",
        created_at=started_at - timedelta(seconds=5),
        seq=200,
    )
    unknown = _unknown(
        group_id="group-zero",
        content="没有出现在历史里的回复",
        request_started_at=started_at,
    )
    db_session.add_all([anchor, unknown])
    await db_session.flush()

    channel = FakeHistoryChannel([
        _history_item(
            group_id=unknown.group_id,
            content=unknown.content,
            sent_at=started_at,
            seq=201,
            sender_id="someone-else",
        ),
        _history_item(
            group_id="another-group",
            content=unknown.content,
            sent_at=started_at,
            seq=202,
        ),
        _history_item(
            group_id=unknown.group_id,
            content="相似但不完全相同",
            sent_at=started_at,
            seq=203,
        ),
        _history_item(
            group_id=unknown.group_id,
            content=unknown.content,
            sent_at=started_at + timedelta(minutes=4),
            seq=204,
        ),
    ])
    result = await reconcile_frozen_unknown_sends(
        channel,
        [unknown.id],
        recheck_delay_seconds=0,
    )

    await db_session.refresh(unknown)
    assert result.not_found == 1
    assert result.confirmed_not_sent == 1
    assert result.retry_queued == 0
    assert unknown.status == MessageStatus.FAILED
    assert "no inbound trigger" in unknown.error
    assert (
        unknown.analysis_json["_delivery"]["reconciliation"]
        == "history_confirmed_not_sent_no_trigger"
    )
    assert channel.calls == [(200, 50), (200, 50)]


@pytest.mark.asyncio
async def test_confirmed_not_sent_queues_original_graph_recovery(
    db_session,
    monkeypatch,
):
    from app.services.qiwe_service import outbound_reconciliation

    class FakeCheckpointer:
        def __init__(self):
            self.deleted_threads: list[str] = []

        async def adelete_thread(self, thread_id: str):
            self.deleted_threads.append(thread_id)

    checkpointer = FakeCheckpointer()
    monkeypatch.setattr(
        outbound_reconciliation,
        "get_agent_checkpointer",
        lambda: checkpointer,
    )
    reconcile_frozen_unknown_sends = (
        outbound_reconciliation.reconcile_frozen_unknown_sends
    )

    started_at = datetime.now(timezone.utc) - timedelta(seconds=60)
    anchor = _sent_anchor(
        group_id="group-retry",
        created_at=started_at - timedelta(seconds=5),
        seq=210,
    )
    db_session.add(anchor)
    inbound, unknown = await _recoverable_unknown(
        db_session,
        group_id="group-retry",
        content="需要恢复的回复",
        request_started_at=started_at,
    )

    channel = FakeHistoryChannel([])
    result = await reconcile_frozen_unknown_sends(
        channel,
        [unknown.id],
        recheck_delay_seconds=0,
    )

    await db_session.refresh(inbound)
    await db_session.refresh(unknown)
    assert result.confirmed_not_sent == 1
    assert result.retry_queued == 1
    assert result.circuit_open is False
    assert unknown.status == MessageStatus.PENDING
    assert unknown.error is None
    assert (
        unknown.analysis_json["_delivery"]["reconciliation"]
        == "history_confirmed_not_sent_retry_pending"
    )
    assert (
        inbound.analysis_json["_reply_processing"]["status"]
        == "retryable_failure"
    )
    assert len(checkpointer.deleted_threads) == 1
    assert checkpointer.deleted_threads[0].startswith("ran-ai:reply:")
    assert channel.calls == [(210, 50), (210, 50)]


@pytest.mark.asyncio
async def test_second_history_read_can_confirm_delayed_sent_message(db_session):
    from app.services.qiwe_service.outbound_reconciliation import (
        reconcile_frozen_unknown_sends,
    )

    started_at = datetime.now(timezone.utc) - timedelta(seconds=60)
    anchor = _sent_anchor(
        group_id="group-delayed",
        created_at=started_at - timedelta(seconds=5),
        seq=220,
    )
    unknown = _unknown(
        group_id="group-delayed",
        content="稍后才进入历史的回复",
        request_started_at=started_at,
    )
    db_session.add_all([anchor, unknown])
    await db_session.flush()
    delayed = _history_item(
        group_id=unknown.group_id,
        content=unknown.content,
        sent_at=started_at + timedelta(seconds=1),
        seq=221,
    )
    channel = FakeHistoryChannel(
        [],
        responses=[
            _history_page([]),
            _history_page([delayed]),
        ],
    )

    result = await reconcile_frozen_unknown_sends(
        channel,
        [unknown.id],
        recheck_delay_seconds=0,
    )

    await db_session.refresh(unknown)
    assert result.confirmed_sent == 1
    assert result.confirmed_not_sent == 0
    assert unknown.status == MessageStatus.SENT
    assert channel.calls == [(220, 50), (220, 50)]


@pytest.mark.asyncio
async def test_history_paginates_by_latest_seq_until_match(db_session):
    from app.services.qiwe_service.outbound_reconciliation import (
        reconcile_frozen_unknown_sends,
    )

    started_at = datetime.now(timezone.utc) - timedelta(seconds=60)
    anchor = _sent_anchor(
        group_id="group-page",
        created_at=started_at - timedelta(seconds=5),
        seq=230,
    )
    unknown = _unknown(
        group_id="group-page",
        content="第二页里的回复",
        request_started_at=started_at,
    )
    db_session.add_all([anchor, unknown])
    await db_session.flush()
    irrelevant = _history_item(
        group_id="another-group",
        content="无关消息",
        sent_at=started_at,
        seq=231,
    )
    matched = _history_item(
        group_id=unknown.group_id,
        content=unknown.content,
        sent_at=started_at + timedelta(seconds=1),
        seq=232,
    )
    channel = FakeHistoryChannel(
        [],
        responses=[
            _history_page(
                [irrelevant],
                has_more=True,
                travel_sync_key=231,
            ),
            _history_page([matched]),
        ],
    )

    result = await reconcile_frozen_unknown_sends(channel, [unknown.id])

    await db_session.refresh(unknown)
    assert result.confirmed_sent == 1
    assert result.history_pages == 2
    assert unknown.status == MessageStatus.SENT
    assert channel.calls == [(230, 50), (231, 50)]


@pytest.mark.asyncio
async def test_history_spam_block_wins_over_sent_match(db_session):
    from app.services.content_policy import get_ai_reply_state
    from app.services.qiwe_service.outbound_reconciliation import (
        reconcile_frozen_unknown_sends,
    )

    started_at = datetime.now(timezone.utc) - timedelta(seconds=60)
    anchor = _sent_anchor(
        group_id="group-blocked",
        created_at=started_at - timedelta(seconds=5),
        seq=240,
    )
    unknown = _unknown(
        group_id="group-blocked",
        content="被企微风控拦截的回复",
        request_started_at=started_at,
    )
    outbound_item = _history_item(
        group_id=unknown.group_id,
        content=unknown.content,
        sent_at=started_at,
        seq=241,
    )
    spam_item = _spam_block_item(
        channel_message_id=outbound_item["msgUniqueIdentifier"],
        sent_at=started_at + timedelta(seconds=1),
        seq=242,
    )
    db_session.add_all([anchor, unknown])
    await db_session.flush()
    channel = FakeHistoryChannel([outbound_item, spam_item])

    result = await reconcile_frozen_unknown_sends(channel, [unknown.id])

    await db_session.refresh(unknown)
    assert result.confirmed_sent == 0
    assert result.blocked == 1
    assert result.retry_queued == 0
    assert unknown.status == MessageStatus.FAILED
    assert "风控拦截" in unknown.error
    assert (
        unknown.analysis_json["_delivery"]["reconciliation"]
        == "history_confirmed_blocked"
    )
    state = await get_ai_reply_state(db_session)
    assert state["enabled"] is False
    assert state["auto_disabled_reason"]


@pytest.mark.asyncio
async def test_confirmed_not_sent_batch_opens_circuit_without_replies(db_session):
    from app.services.content_policy import get_ai_reply_state
    from app.services.qiwe_service.outbound_reconciliation import (
        AUTO_RETRY_BATCH_LIMIT,
        reconcile_frozen_unknown_sends,
    )

    started_at = datetime.now(timezone.utc) - timedelta(seconds=60)
    anchor = _sent_anchor(
        group_id="group-circuit-anchor",
        created_at=started_at - timedelta(seconds=5),
        seq=250,
    )
    db_session.add(anchor)
    unknowns: list[Message] = []
    for index in range(AUTO_RETRY_BATCH_LIMIT + 1):
        _inbound, unknown = await _recoverable_unknown(
            db_session,
            group_id=f"group-circuit-{index}",
            content=f"待恢复回复-{index}",
            request_started_at=started_at + timedelta(milliseconds=index),
        )
        unknowns.append(unknown)

    channel = FakeHistoryChannel([])
    result = await reconcile_frozen_unknown_sends(
        channel,
        [row.id for row in unknowns],
        recheck_delay_seconds=0,
    )

    for row in unknowns:
        await db_session.refresh(row)
        assert row.status == MessageStatus.FAILED
        assert (
            row.analysis_json["_delivery"]["reconciliation"]
            == "history_confirmed_not_sent_batch_circuit"
        )
    assert result.confirmed_not_sent == AUTO_RETRY_BATCH_LIMIT + 1
    assert result.retry_queued == 0
    assert result.circuit_open is True
    state = await get_ai_reply_state(db_session)
    assert state["enabled"] is False


@pytest.mark.asyncio
async def test_only_latest_confirmed_miss_in_same_group_is_retried(db_session):
    from app.services.qiwe_service.outbound_reconciliation import (
        reconcile_frozen_unknown_sends,
    )

    started_at = datetime.now(timezone.utc) - timedelta(seconds=60)
    anchor = _sent_anchor(
        group_id="group-latest",
        created_at=started_at - timedelta(seconds=5),
        seq=260,
    )
    db_session.add(anchor)
    _first_inbound, first_unknown = await _recoverable_unknown(
        db_session,
        group_id="group-latest",
        content="旧回复",
        request_started_at=started_at,
    )
    service_case_id = first_unknown.service_case_id
    second_inbound = Message(
        service_case_id=service_case_id,
        group_id="group-latest",
        wecom_message_id="inbound-group-latest-2",
        content="更新的问题",
        message_type="text",
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.USER,
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
        analysis_json={"_qiwe_meta": {}},
        created_at=started_at + timedelta(seconds=1),
    )
    db_session.add(second_inbound)
    await db_session.flush()
    second_unknown = _unknown(
        group_id="group-latest",
        content="新回复",
        request_started_at=started_at + timedelta(seconds=2),
        service_case_id=service_case_id,
        trigger_id=second_inbound.id,
    )
    db_session.add(second_unknown)
    await db_session.flush()

    result = await reconcile_frozen_unknown_sends(
        FakeHistoryChannel([]),
        [first_unknown.id, second_unknown.id],
        recheck_delay_seconds=0,
    )

    await db_session.refresh(first_unknown)
    await db_session.refresh(second_unknown)
    assert first_unknown.status == MessageStatus.FAILED
    assert (
        first_unknown.analysis_json["_delivery"]["reconciliation"]
        == "history_confirmed_not_sent_superseded"
    )
    assert second_unknown.status == MessageStatus.PENDING
    assert result.retry_queued == 1
    assert result.suppressed == 1


@pytest.mark.asyncio
async def test_incomplete_history_window_never_retries(db_session):
    from app.services.qiwe_service.outbound_reconciliation import (
        reconcile_frozen_unknown_sends,
    )

    started_at = datetime.now(timezone.utc) - timedelta(seconds=60)
    anchor = _sent_anchor(
        group_id="group-incomplete",
        created_at=started_at - timedelta(seconds=5),
        seq=270,
    )
    unknown = _unknown(
        group_id="group-incomplete",
        content="不能在历史不完整时补发",
        request_started_at=started_at,
    )
    irrelevant = _history_item(
        group_id="another-group",
        content="无关",
        sent_at=started_at,
        seq=271,
    )
    db_session.add_all([anchor, unknown])
    await db_session.flush()
    channel = FakeHistoryChannel(
        [irrelevant],
        has_more=True,
    )

    result = await reconcile_frozen_unknown_sends(
        channel,
        [unknown.id],
        recheck_delay_seconds=0,
    )

    await db_session.refresh(unknown)
    assert result.confirmed_not_sent == 0
    assert result.retry_queued == 0
    assert result.skipped == 1
    assert unknown.status == MessageStatus.UNKNOWN
    assert (
        unknown.analysis_json["_delivery"]["reconciliation"]
        == "history_not_found_unconfirmed"
    )


@pytest.mark.asyncio
async def test_multiple_history_matches_keep_unknown_ambiguous(db_session):
    from app.services.qiwe_service.outbound_reconciliation import (
        reconcile_frozen_unknown_sends,
    )

    started_at = datetime.now(timezone.utc) - timedelta(seconds=60)
    anchor = _sent_anchor(
        group_id="group-ambiguous",
        created_at=started_at - timedelta(seconds=5),
        seq=300,
    )
    unknown = _unknown(
        group_id="group-ambiguous",
        content="重复正文",
        request_started_at=started_at,
    )
    db_session.add_all([anchor, unknown])
    await db_session.flush()

    channel = FakeHistoryChannel([
        _history_item(
            group_id=unknown.group_id,
            content=unknown.content,
            sent_at=started_at,
            seq=301,
        ),
        _history_item(
            group_id=unknown.group_id,
            content=unknown.content,
            sent_at=started_at + timedelta(seconds=2),
            seq=302,
        ),
    ])
    result = await reconcile_frozen_unknown_sends(channel, [unknown.id])

    await db_session.refresh(unknown)
    assert result.ambiguous == 1
    assert unknown.status == MessageStatus.UNKNOWN
    assert unknown.analysis_json["_delivery"]["reconciliation"] == "history_ambiguous"


@pytest.mark.asyncio
async def test_one_history_item_cannot_confirm_two_unknown_rows(db_session):
    from app.services.qiwe_service.outbound_reconciliation import (
        reconcile_frozen_unknown_sends,
    )

    started_at = datetime.now(timezone.utc) - timedelta(seconds=60)
    anchor = _sent_anchor(
        group_id="group-collision",
        created_at=started_at - timedelta(seconds=5),
        seq=400,
    )
    first = _unknown(
        group_id="group-collision",
        content="相同回复",
        request_started_at=started_at,
    )
    second = _unknown(
        group_id="group-collision",
        content="相同回复",
        request_started_at=started_at + timedelta(seconds=1),
    )
    db_session.add_all([anchor, first, second])
    await db_session.flush()

    channel = FakeHistoryChannel([
        _history_item(
            group_id=first.group_id,
            content=first.content,
            sent_at=started_at + timedelta(seconds=2),
            seq=401,
        )
    ])
    result = await reconcile_frozen_unknown_sends(
        channel,
        [first.id, second.id],
    )

    await db_session.refresh(first)
    await db_session.refresh(second)
    assert result.confirmed_sent == 0
    assert result.ambiguous == 2
    assert first.status == MessageStatus.UNKNOWN
    assert second.status == MessageStatus.UNKNOWN


@pytest.mark.asyncio
async def test_history_failure_or_missing_anchor_is_conservative(db_session):
    from app.services.qiwe_service.outbound_reconciliation import (
        reconcile_frozen_unknown_sends,
    )

    started_at = datetime.now(timezone.utc) - timedelta(seconds=60)
    no_anchor = _unknown(
        group_id="group-no-anchor",
        content="没有起点",
        request_started_at=started_at,
    )
    db_session.add(no_anchor)
    await db_session.flush()

    no_anchor_channel = FakeHistoryChannel([])
    no_anchor_result = await reconcile_frozen_unknown_sends(
        no_anchor_channel,
        [no_anchor.id],
    )
    assert no_anchor_result.skipped == 1
    assert no_anchor_channel.calls == []

    anchor = _sent_anchor(
        group_id="group-api-fail",
        created_at=started_at,
        seq=500,
    )
    api_failure = _unknown(
        group_id="group-api-fail",
        content="接口失败",
        request_started_at=started_at + timedelta(seconds=1),
    )
    db_session.add_all([anchor, api_failure])
    await db_session.flush()

    failing_channel = FakeHistoryChannel(
        [],
        error=RuntimeError("gateway timeout"),
    )
    failure_result = await reconcile_frozen_unknown_sends(
        failing_channel,
        [api_failure.id],
    )

    await db_session.refresh(no_anchor)
    await db_session.refresh(api_failure)
    assert failure_result.api_called is True
    assert failure_result.skipped == 1
    assert no_anchor.status == MessageStatus.UNKNOWN
    assert api_failure.status == MessageStatus.UNKNOWN
    assert api_failure.error == "read timeout"
