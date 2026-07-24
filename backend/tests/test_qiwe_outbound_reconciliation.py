from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.message import (
    Message,
    MessageDirection,
    MessagePurpose,
    MessageSenderType,
    MessageStatus,
)
from app.services.channels.qiwe import QiWeHistoryPage


class FakeHistoryChannel:
    def __init__(
        self,
        items: list[dict],
        *,
        profile: dict | None = None,
        error: Exception | None = None,
        has_more: bool = False,
    ):
        self.items = items
        self.profile = {"userId": "bot-1"} if profile is None else profile
        self.error = error
        self.has_more = has_more
        self.calls: list[tuple[int, int]] = []
        self.profile_calls = 0

    async def get_profile(self):
        self.profile_calls += 1
        return self.profile

    async def sync_message_history(self, *, after_seq: int, limit: int = 50):
        self.calls.append((after_seq, limit))
        if self.error is not None:
            raise self.error
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
) -> Message:
    return Message(
        group_id=group_id,
        content=content,
        message_type="markdown",
        direction=MessageDirection.OUTBOUND,
        sender_type=MessageSenderType.BOT,
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.UNKNOWN,
        error="read timeout",
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
async def test_history_zero_match_keeps_unknown(db_session):
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
    result = await reconcile_frozen_unknown_sends(channel, [unknown.id])

    await db_session.refresh(unknown)
    assert result.not_found == 1
    assert unknown.status == MessageStatus.UNKNOWN
    assert unknown.error == "read timeout"
    assert unknown.analysis_json["_delivery"]["reconciliation"] == "history_not_found"


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
