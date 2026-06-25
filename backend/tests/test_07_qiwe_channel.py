from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.api.routes.qiwe import _bypass_aggregator, _channel_message_from_qiwe, _is_authorized_callback
from app.services.channels.base import ChannelMessage, ChannelSendResult
from app.services.channels.qiwe import QiWeChannel
from app.services.qiwe_service.aggregator import QiWeMessageAggregator
from app.services.ranren_ai.engine import RanrenAIEngine
from app.models.message import Message


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse(self.responses.pop(0))


class FakeEngine:
    def __init__(self):
        self.messages = []

    async def handle_message(self, message: ChannelMessage) -> None:
        self.messages.append(message)


class FailingEngine:
    async def handle_message(self, message: ChannelMessage) -> None:
        raise RuntimeError("boom")


class FakeChannel:
    name = "qiwe"

    async def send_text(
        self,
        chat_id: str,
        content: str,
        *,
        reply_to: str | None = None,
        reply_context: object | None = None,
    ):
        return ChannelSendResult(success=True, message_id="fake-text")

    async def send_document(
        self,
        chat_id: str,
        file_path: str,
        *,
        caption: str | None = None,
        reply_to: str | None = None,
        reply_context: object | None = None,
    ):
        return ChannelSendResult(success=True, message_id="fake-doc")


def _settings():
    return SimpleNamespace(
        qiwei_token="token",
        qiwei_guid="guid",
        qiwei_base_url="http://manager.qiweapi.com/qiwe",
        qiwei_callback_secret="",
    )


@pytest.mark.asyncio
async def test_qiwe_send_text_uses_to_id():
    client = FakeClient([
        {"code": 0, "msg": "成功", "data": {"msgUniqueIdentifier": "msg-1"}}
    ])
    channel = QiWeChannel(_settings(), http_client=client)

    result = await channel.send_text("room-1", "hello")

    assert result.success is True
    assert result.message_id == "msg-1"
    _, kwargs = client.calls[0]
    assert kwargs["json"]["method"] == "/msg/sendText"
    assert kwargs["json"]["params"]["toId"] == "room-1"
    assert "told" not in kwargs["json"]["params"]


@pytest.mark.asyncio
async def test_qiwe_send_text_builds_reply_payload_from_context():
    client = FakeClient([
        {"code": 0, "msg": "成功", "data": {"msgUniqueIdentifier": "msg-1"}}
    ])
    channel = QiWeChannel(_settings(), http_client=client)

    result = await channel.send_text(
        "room-1",
        "收到",
        reply_to="origin-id",
        reply_context={
            "senderId": "user-1",
            "senderName": "蔡浩然",
            "timestamp": 1780000000,
            "msgData": {"content": "我刚才说的那句话"},
        },
    )

    assert result.success is True
    params = client.calls[0][1]["json"]["params"]
    assert params["reply"]["msgUniqueIdentifier"] == "origin-id"
    assert params["reply"]["userId"] == "user-1"
    assert params["reply"]["showName"] == "蔡浩然"
    assert params["reply"]["msgData"]["content"] == "我刚才说的那句话"


@pytest.mark.asyncio
async def test_qiwe_send_document_uploads_then_sends_file(tmp_path):
    file_path = tmp_path / "doc.txt"
    file_path.write_text("hello", encoding="utf-8")
    client = FakeClient([
        {
            "code": 0,
            "msg": "成功",
            "data": {
                "fileAesKey": "aes",
                "fileId": "file-id",
                "fileSize": 5,
                "filename": "doc.txt",
            },
        },
        {"code": 0, "msg": "成功", "data": {"msgUniqueIdentifier": "msg-file"}},
    ])
    channel = QiWeChannel(_settings(), http_client=client)

    result = await channel.send_document("room-1", str(file_path))

    assert result.success is True
    assert result.message_id == "msg-file"
    assert client.calls[0][1]["data"]["method"] == "/cloud/cdnBigUpload"
    assert client.calls[0][1]["data"]["fileType"] == "5"
    assert client.calls[1][1]["json"]["method"] == "/msg/sendFile"
    assert client.calls[1][1]["json"]["params"]["fileId"] == "file-id"


@pytest.mark.asyncio
async def test_qiwe_aggregator_merges_consecutive_user_messages():
    engine = FakeEngine()
    aggregator = QiWeMessageAggregator(FakeChannel(), window_seconds=0.01, engine=engine)

    await aggregator.enqueue(
        ChannelMessage(
            channel="qiwe",
            chat_id="room-1",
            chat_type="group",
            text="第一句",
            message_id="m1",
            user_id="u1",
            timestamp=datetime.now(timezone.utc),
        )
    )
    await aggregator.enqueue(
        ChannelMessage(
            channel="qiwe",
            chat_id="room-1",
            chat_type="group",
            text="第二句",
            message_id="m2",
            user_id="u1",
            timestamp=datetime.now(timezone.utc),
        )
    )
    await asyncio.sleep(0.03)

    assert len(engine.messages) == 1
    assert engine.messages[0].text == "第一句\n第二句"
    assert engine.messages[0].message_id == "m2"


@pytest.mark.asyncio
async def test_qiwe_aggregator_ignores_duplicate_message_id():
    engine = FakeEngine()
    aggregator = QiWeMessageAggregator(FakeChannel(), window_seconds=0.01, engine=engine)

    message = ChannelMessage(
        channel="qiwe",
        chat_id="room-1",
        chat_type="group",
        text="重复消息",
        message_id="same-id",
        user_id="u1",
        timestamp=datetime.now(timezone.utc),
    )
    await aggregator.enqueue(message)
    await aggregator.enqueue(message)
    await asyncio.sleep(0.03)

    assert len(engine.messages) == 1
    assert engine.messages[0].text == "重复消息"


@pytest.mark.asyncio
async def test_qiwe_aggregator_uses_fixed_window_without_resetting_timer():
    engine = FakeEngine()
    aggregator = QiWeMessageAggregator(FakeChannel(), window_seconds=0.03, engine=engine)

    await aggregator.enqueue(
        ChannelMessage(
            channel="qiwe",
            chat_id="room-1",
            chat_type="group",
            text="第一句",
            message_id="fixed-1",
            user_id="u1",
            timestamp=datetime.now(timezone.utc),
        )
    )
    await asyncio.sleep(0.02)
    await aggregator.enqueue(
        ChannelMessage(
            channel="qiwe",
            chat_id="room-1",
            chat_type="group",
            text="第二句",
            message_id="fixed-2",
            user_id="u1",
            timestamp=datetime.now(timezone.utc),
        )
    )
    await asyncio.sleep(0.02)

    assert len(engine.messages) == 1
    assert engine.messages[0].text == "第一句\n第二句"


@pytest.mark.asyncio
async def test_qiwe_aggregator_dispatch_now_reuses_dedupe():
    engine = FakeEngine()
    aggregator = QiWeMessageAggregator(FakeChannel(), window_seconds=0.1, engine=engine)
    message = ChannelMessage(
        channel="qiwe",
        chat_id="room-1",
        chat_type="group",
        text="发送本群入营问卷",
        message_id="command-id",
        user_id="u1",
        timestamp=datetime.now(timezone.utc),
    )

    await aggregator.dispatch_now(message)
    await aggregator.dispatch_now(message)

    assert len(engine.messages) == 1
    assert engine.messages[0].text == "发送本群入营问卷"


@pytest.mark.asyncio
async def test_qiwe_aggregator_swallows_engine_failure_for_callback_safety():
    aggregator = QiWeMessageAggregator(FakeChannel(), window_seconds=0.01, engine=FailingEngine())

    message = ChannelMessage(
        channel="qiwe",
        chat_id="room-1",
        chat_type="group",
        text="普通消息",
        message_id="failure-id",
        user_id="u1",
        timestamp=datetime.now(timezone.utc),
    )

    await aggregator.dispatch_now(message)
    await aggregator.enqueue(
        ChannelMessage(
            channel="qiwe",
            chat_id="room-1",
            chat_type="group",
            text="聚合消息",
            message_id="failure-id-2",
            user_id="u1",
            timestamp=datetime.now(timezone.utc),
        )
    )
    await asyncio.sleep(0.03)


@pytest.mark.asyncio
async def test_qiwe_aggregator_allows_message_after_ttl():
    engine = FakeEngine()
    aggregator = QiWeMessageAggregator(
        FakeChannel(),
        window_seconds=0.01,
        engine=engine,
        dedupe_ttl_seconds=0.05,
    )

    message = ChannelMessage(
        channel="qiwe",
        chat_id="room-1",
        chat_type="group",
        text="可过期消息",
        message_id="ttl-id",
        user_id="u1",
        timestamp=datetime.now(timezone.utc),
    )
    await aggregator.enqueue(message)
    await asyncio.sleep(0.08)
    await aggregator.enqueue(message)
    await asyncio.sleep(0.03)

    assert len(engine.messages) == 2
    assert [item.text for item in engine.messages] == ["可过期消息", "可过期消息"]


def test_qiwe_callback_text_event_to_channel_message():
    message = _channel_message_from_qiwe(
        {
            "cmd": 15000,
            "msgType": 2,
            "userId": "bot-user",
            "fromRoomId": 10926518308089720,
            "senderId": 7881300658070689,
            "senderName": "用户A",
            "timestamp": 1779951380,
            "msgUniqueIdentifier": "unique-1",
            "msgData": {"content": "老师我分几句说"},
        }
    )

    assert message is not None
    assert message.channel == "qiwe"
    assert message.chat_id == "10926518308089720"
    assert message.chat_type == "group"
    assert message.user_id == "7881300658070689"
    assert message.text == "老师我分几句说"
    assert message.message_type == "text"


def test_qiwe_callback_image_event_to_channel_message():
    message = _channel_message_from_qiwe(
        {
            "cmd": 15000,
            "msgType": 101,
            "userId": "bot-user",
            "fromRoomId": 10926518308089720,
            "senderId": 7881300658070689,
            "senderName": "用户A",
            "timestamp": 1779951380,
            "msgUniqueIdentifier": "unique-image-1",
            "msgData": {
                "fileMiddleHttpUrl": "http://example.com/image.jpg",
                "fileAesKey": "aes-key-here",
            },
        }
    )

    assert message is not None
    assert message.channel == "qiwe"
    assert message.chat_id == "10926518308089720"
    assert message.chat_type == "group"
    assert message.user_id == "7881300658070689"
    assert message.message_type == "image"
    assert message.text == "[图片]"



def test_qiwe_callback_ignores_self_message():
    message = _channel_message_from_qiwe(
        {
            "cmd": 15000,
            "msgType": 2,
            "userId": "bot-user",
            "senderId": "bot-user",
            "fromRoomId": 1,
            "msgData": {"content": "机器人自己发的消息"},
        }
    )

    assert message is None


def test_qiwe_callback_secret_rejects_when_unconfigured():
    # fail-closed：未配置密钥时拒绝回调(防止线上漏配密钥时回调被任意伪造)
    request = SimpleNamespace(headers={}, query_params={})
    settings = SimpleNamespace(qiwei_callback_secret="")

    assert _is_authorized_callback(request, settings) is False


def test_qiwe_callback_secret_validates_header_or_query():
    settings = SimpleNamespace(qiwei_callback_secret="secret-1")

    assert _is_authorized_callback(
        SimpleNamespace(headers={"x-qiwei-callback-secret": "secret-1"}, query_params={}),
        settings,
    ) is True
    assert _is_authorized_callback(
        SimpleNamespace(headers={}, query_params={"secret": "secret-1"}),
        settings,
    ) is True
    assert _is_authorized_callback(
        SimpleNamespace(headers={"x-qiwei-callback-secret": "bad"}, query_params={}),
        settings,
    ) is False


def test_qiwe_system_command_bypasses_aggregator():
    command = ChannelMessage(
        channel="qiwe",
        chat_id="room-1",
        chat_type="group",
        text="请发送本群入营问卷",
        message_id="command-1",
        user_id="u1",
        timestamp=datetime.now(timezone.utc),
    )
    normal = ChannelMessage(
        channel="qiwe",
        chat_id="room-1",
        chat_type="group",
        text="老师我分几句说",
        message_id="normal-1",
        user_id="u1",
        timestamp=datetime.now(timezone.utc),
    )

    assert _bypass_aggregator(command) is True
    assert _bypass_aggregator(normal) is False


@pytest.mark.asyncio
async def test_qiwe_questionnaire_command_skips_intervention_llm(monkeypatch):
    async def _should_not_run(*args, **kwargs):
        raise AssertionError("analyze_intervention should not run for questionnaire command")

    monkeypatch.setattr(
        "app.services.ranren_ai.engine.analyze_intervention",
        _should_not_run,
    )

    fake_case = SimpleNamespace(
        id=42,
        case_no="CASE-42",
        group_id="room-fast-path",
        questionnaire_token="token-42",
    )
    added_messages: list[Message] = []

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return False

        def add(self, obj):
            if isinstance(obj, Message):
                obj.id = len(added_messages) + 1
                added_messages.append(obj)

        async def commit(self):
            return None

        async def flush(self):
            return None

    reply_calls: list[tuple[ChannelMessage, object, object]] = []

    async def _reply_questionnaire_link(self, message, service_case, *, trigger_id=None):
        reply_calls.append((message, service_case, trigger_id))

    monkeypatch.setattr("app.services.ranren_ai.engine.SessionLocal", lambda *args, **kwargs: FakeSession())
    monkeypatch.setattr(RanrenAIEngine, "_latest_active_case", lambda self, session, chat_id: asyncio.sleep(0, result=fake_case))
    monkeypatch.setattr(RanrenAIEngine, "_reply_questionnaire_link", _reply_questionnaire_link)

    engine = RanrenAIEngine(FakeChannel())
    message = ChannelMessage(
        channel="qiwe",
        chat_id="room-fast-path",
        chat_type="group",
        text="发送本群入营问卷",
        message_id="fast-path-1",
        user_id="user-fast-path",
        timestamp=datetime.now(timezone.utc),
    )

    await engine.handle_message(message)

    assert len(added_messages) == 1
    assert added_messages[0].analysis_json is not None
    assert added_messages[0].analysis_json.get("_qiwe_meta", {}).get("sender_id") == "user-fast-path"
    assert len(reply_calls) == 1
    assert reply_calls[0][1] is fake_case


@pytest.mark.asyncio
async def test_qiwe_staff_sender_ignoring(monkeypatch):
    from app.models.message import MessageSenderType
    added_messages: list[Message] = []

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return False

        def add(self, obj):
            if isinstance(obj, Message):
                obj.id = len(added_messages) + 1
                added_messages.append(obj)

        async def commit(self):
            return None

        async def flush(self):
            return None

    async def fake_get_qiwei_staff_senders(*args, **kwargs):
        return [
            {"type": "equals", "pattern": "上海 Lucy"},
            {"type": "contains", "pattern": "奢咖"},
        ]

    fake_case = SimpleNamespace(
        id=42,
        case_no="CASE-42",
        group_id="room-staff",
        questionnaire_token="token-42",
    )

    monkeypatch.setattr("app.services.ranren_ai.engine.SessionLocal", lambda *args, **kwargs: FakeSession())
    monkeypatch.setattr("app.services.content_policy.get_qiwei_staff_senders", fake_get_qiwei_staff_senders)
    monkeypatch.setattr(RanrenAIEngine, "_latest_active_case", lambda self, session, chat_id: asyncio.sleep(0, result=fake_case))
    monkeypatch.setattr(RanrenAIEngine, "_recent_user_messages", lambda *args, **kwargs: asyncio.sleep(0, result=[]))
    monkeypatch.setattr(RanrenAIEngine, "_store_intervention_analysis", lambda *args, **kwargs: asyncio.sleep(0, result=None))
    monkeypatch.setattr(RanrenAIEngine, "_reply_with_ai", lambda *args, **kwargs: asyncio.sleep(0, result=None))
    monkeypatch.setattr(RanrenAIEngine, "_run_outcome_analysis", lambda *args, **kwargs: asyncio.sleep(0, result=None))

    engine = RanrenAIEngine(FakeChannel())

    # Case 1: Match equals "上海 Lucy"
    message1 = ChannelMessage(
        channel="qiwe",
        chat_id="room-staff",
        chat_type="group",
        text="我是上海 Lucy",
        message_id="m1",
        user_id="user-1",
        user_name="  上海 lucy  ",
        timestamp=datetime.now(timezone.utc),
    )
    await engine.handle_message(message1)

    # Case 2: Match contains "奢咖"
    message2 = ChannelMessage(
        channel="qiwe",
        chat_id="room-staff",
        chat_type="group",
        text="我是奢咖运营",
        message_id="m2",
        user_id="user-2",
        user_name="小奢咖助教",
        timestamp=datetime.now(timezone.utc),
    )
    await engine.handle_message(message2)

    # Case 3: Does not match
    message3 = ChannelMessage(
        channel="qiwe",
        chat_id="room-staff",
        chat_type="group",
        text="我是普通用户",
        message_id="m3",
        user_id="user-3",
        user_name="普通用户",
        timestamp=datetime.now(timezone.utc),
    )
    
    called_analyze = False
    async def fake_analyze_intervention(*args, **kwargs):
        nonlocal called_analyze
        called_analyze = True
        return {}
    monkeypatch.setattr("app.services.ranren_ai.engine.analyze_intervention", fake_analyze_intervention)

    await engine.handle_message(message3)

    assert len(added_messages) == 3
    # Message 1 should be MessageSenderType.STAFF
    assert added_messages[0].sender_type == MessageSenderType.STAFF
    # Message 2 should be MessageSenderType.STAFF
    assert added_messages[1].sender_type == MessageSenderType.STAFF
    # Message 3 should be MessageSenderType.USER
    assert added_messages[2].sender_type == MessageSenderType.USER
    assert called_analyze is True
