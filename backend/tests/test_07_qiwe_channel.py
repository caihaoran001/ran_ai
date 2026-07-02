from __future__ import annotations

import asyncio
import secrets
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
    explicit_query = ChannelMessage(
        channel="qiwe",
        chat_id="room-1",
        chat_type="group",
        text="@燃人AI 最近有什么好看的电影？",
        raw_payload={
            "userId": "bot-1",
            "msgData": {"atList": [{"userId": "bot-1"}]},
        },
        timestamp=datetime.now(timezone.utc),
    )
    assert _bypass_aggregator(explicit_query) is True


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
    reply_inputs: list[tuple[str, MessageSenderType]] = []

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
    monkeypatch.setattr(RanrenAIEngine, "_run_outcome_analysis", lambda *args, **kwargs: asyncio.sleep(0, result=None))

    async def fake_reply_with_ai(self, message, **kwargs):
        trigger_id = kwargs.get("trigger_id")
        trigger = added_messages[(trigger_id or 1) - 1]
        reply_inputs.append((message.text, trigger.sender_type))

    monkeypatch.setattr(RanrenAIEngine, "_reply_with_ai", fake_reply_with_ai)

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

    # Case 4: Staff explicitly @mentions the bot; it should trigger a reply but remain STAFF in storage.
    message4 = ChannelMessage(
        channel="qiwe",
        chat_id="room-staff",
        chat_type="group",
        text="@燃人AI 最近有什么好看的电影？",
        message_id="m4",
        user_id="user-1",
        user_name="上海 Lucy",
        raw_payload={
            "userId": "bot-1",
            "msgData": {"atList": [{"userId": "bot-1"}]},
        },
        timestamp=datetime.now(timezone.utc),
    )
    await engine.handle_message(message4)

    assert len(added_messages) == 4
    # Message 1 should be MessageSenderType.STAFF
    assert added_messages[0].sender_type == MessageSenderType.STAFF
    # Message 2 should be MessageSenderType.STAFF
    assert added_messages[1].sender_type == MessageSenderType.STAFF
    # Message 3 should be MessageSenderType.USER
    assert added_messages[2].sender_type == MessageSenderType.USER
    # Message 4 should still be MessageSenderType.STAFF even though it triggers AI.
    assert added_messages[3].sender_type == MessageSenderType.STAFF
    assert reply_inputs == [
        ("我是普通用户", MessageSenderType.USER),
        ("最近有什么好看的电影？", MessageSenderType.STAFF),
    ]
    assert added_messages[3].analysis_json["_qiwe_meta"]["sender_type"] == "staff"
    assert added_messages[3].analysis_json["_qiwe_meta"]["explicit_bot_query_text"] == "最近有什么好看的电影？"
    assert called_analyze is True


@pytest.mark.asyncio
async def test_qiwe_recovery_of_stuck_inbound_messages(db_session, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from app.services.ranren_ai.engine import recover_stuck_inbound_messages
    from app.models.service_case import ServiceCase, ServiceCaseStatus
    from app.models.message import Message, MessageSenderType, MessageDirection, MessagePurpose, MessageStatus
    from app.models.user import User
    
    # Seed User
    user = User(name="测试用户")
    db_session.add(user)
    await db_session.flush()
    
    now = datetime.now(timezone.utc)
    
    # Scenario A: Stuck message (should be recovered)
    case_a = ServiceCase(
        user_id=user.id, case_no="CASE-A", status=ServiceCaseStatus.IN_SERVICE,
        ai_auto_reply_enabled=True, group_id="group-a", questionnaire_token="token-a",
    )
    db_session.add(case_a)
    await db_session.flush()
    
    msg_a = Message(
        service_case_id=case_a.id, group_id="group-a", wecom_message_id="msg-a",
        content="我需要补发这个回复。", message_type="text",
        direction=MessageDirection.INBOUND, sender_type=MessageSenderType.USER,
        purpose=MessagePurpose.CHAT, status=MessageStatus.RECEIVED,
        analysis_json={"_qiwe_meta": {}}, created_at=now - timedelta(minutes=1),
    )
    db_session.add(msg_a)
    
    # Scenario B: Message already replied to (should NOT be recovered)
    case_b = ServiceCase(
        user_id=user.id, case_no="CASE-B", status=ServiceCaseStatus.IN_SERVICE,
        ai_auto_reply_enabled=True, group_id="group-b", questionnaire_token="token-b",
    )
    db_session.add(case_b)
    await db_session.flush()
    
    msg_b = Message(
        service_case_id=case_b.id, group_id="group-b", wecom_message_id="msg-b",
        content="已经回复过的消息。", message_type="text",
        direction=MessageDirection.INBOUND, sender_type=MessageSenderType.USER,
        purpose=MessagePurpose.CHAT, status=MessageStatus.RECEIVED,
        analysis_json={"_qiwe_meta": {}}, created_at=now - timedelta(minutes=1),
    )
    db_session.add(msg_b)
    await db_session.flush()
    
    reply_b = Message(
        service_case_id=case_b.id, group_id="group-b", wecom_message_id="reply-b",
        content="这是回复内容。", message_type="text",
        direction=MessageDirection.OUTBOUND, sender_type=MessageSenderType.BOT,
        purpose=MessagePurpose.CHAT, status=MessageStatus.SENT,
        trigger_id=msg_b.id, created_at=now - timedelta(seconds=30),
    )
    db_session.add(reply_b)
    
    # Scenario C: Message is too new (within 15 seconds) (should NOT be recovered)
    case_c = ServiceCase(
        user_id=user.id, case_no="CASE-C", status=ServiceCaseStatus.IN_SERVICE,
        ai_auto_reply_enabled=True, group_id="group-c", questionnaire_token="token-c",
    )
    db_session.add(case_c)
    await db_session.flush()
    
    msg_c = Message(
        service_case_id=case_c.id, group_id="group-c", wecom_message_id="msg-c",
        content="刚发的消息。", message_type="text",
        direction=MessageDirection.INBOUND, sender_type=MessageSenderType.USER,
        purpose=MessagePurpose.CHAT, status=MessageStatus.RECEIVED,
        analysis_json={"_qiwe_meta": {}}, created_at=now - timedelta(seconds=5),
    )
    db_session.add(msg_c)
    
    # Scenario D: Silenced service case (should NOT be recovered)
    case_d = ServiceCase(
        user_id=user.id, case_no="CASE-D", status=ServiceCaseStatus.IN_SERVICE,
        ai_auto_reply_enabled=False, group_id="group-d", questionnaire_token="token-d",
    )
    db_session.add(case_d)
    await db_session.flush()
    
    msg_d = Message(
        service_case_id=case_d.id, group_id="group-d", wecom_message_id="msg-d",
        content="群静默时的消息。", message_type="text",
        direction=MessageDirection.INBOUND, sender_type=MessageSenderType.USER,
        purpose=MessagePurpose.CHAT, status=MessageStatus.RECEIVED,
        analysis_json={"_qiwe_meta": {}}, created_at=now - timedelta(minutes=1),
    )
    db_session.add(msg_d)

    # Scenario E: Already processed by intervention analysis (should NOT be recovered)
    case_e = ServiceCase(
        user_id=user.id, case_no="CASE-E", status=ServiceCaseStatus.IN_SERVICE,
        ai_auto_reply_enabled=True, group_id="group-e", questionnaire_token="token-e",
    )
    db_session.add(case_e)
    await db_session.flush()

    msg_e = Message(
        service_case_id=case_e.id, group_id="group-e", wecom_message_id="msg-e",
        content="已经走过风控但还没出站。", message_type="text",
        direction=MessageDirection.INBOUND, sender_type=MessageSenderType.USER,
        purpose=MessagePurpose.CHAT, status=MessageStatus.RECEIVED,
        analysis_json={"_qiwe_meta": {}, "requires_human_intervention": False},
        created_at=now - timedelta(minutes=1),
    )
    db_session.add(msg_e)

    # Scenario F: Already claimed by another recovery task (should NOT be recovered)
    case_f = ServiceCase(
        user_id=user.id, case_no="CASE-F", status=ServiceCaseStatus.IN_SERVICE,
        ai_auto_reply_enabled=True, group_id="group-f", questionnaire_token="token-f",
    )
    db_session.add(case_f)
    await db_session.flush()

    msg_f = Message(
        service_case_id=case_f.id, group_id="group-f", wecom_message_id="msg-f",
        content="已经被恢复任务认领。", message_type="text",
        direction=MessageDirection.INBOUND, sender_type=MessageSenderType.USER,
        purpose=MessagePurpose.CHAT, status=MessageStatus.RECEIVED,
        analysis_json={
            "_qiwe_meta": {},
            "_recovery_claim": {
                "claimed_at": now.isoformat(),
                "reason": "startup_recovery",
            },
        },
        created_at=now - timedelta(minutes=1),
    )
    db_session.add(msg_f)
    
    await db_session.flush()
    
    reprocess_calls = []
    async def fake_reprocess_message(self, message):
        reprocess_calls.append(message)
    monkeypatch.setattr(RanrenAIEngine, "reprocess_message", fake_reprocess_message)
    
    await recover_stuck_inbound_messages(FakeChannel(), now)
    
    assert len(reprocess_calls) == 1
    assert reprocess_calls[0].id == msg_a.id
    assert reprocess_calls[0].content == "我需要补发这个回复。"


@pytest.mark.asyncio
async def test_send_and_record_is_record_first(db_session):
    """Fix③：出站发送前先落 SENDING 行(record-first)，发送成功后回填 SENT + sent_at。"""
    from sqlalchemy import select
    from app.models.service_case import ServiceCase, ServiceCaseStatus
    from app.models.message import (
        Message,
        MessageDirection,
        MessagePurpose,
        MessageSenderType,
        MessageStatus,
    )
    from app.models.user import User

    user = User(name="record-first用户")
    db_session.add(user)
    await db_session.flush()
    case = ServiceCase(
        user_id=user.id, case_no="CASE-RF", status=ServiceCaseStatus.IN_SERVICE,
        ai_auto_reply_enabled=True, group_id="group-rf", questionnaire_token="token-rf",
    )
    db_session.add(case)
    await db_session.flush()
    inbound = Message(
        service_case_id=case.id, group_id="group-rf", wecom_message_id="in-rf",
        content="问句", message_type="text",
        direction=MessageDirection.INBOUND, sender_type=MessageSenderType.USER,
        purpose=MessagePurpose.CHAT, status=MessageStatus.RECEIVED,
    )
    db_session.add(inbound)
    await db_session.flush()

    engine = RanrenAIEngine(FakeChannel())
    channel_msg = ChannelMessage(
        channel="qiwe", chat_id="group-rf", chat_type="group",
        text="问句", message_id="in-rf", user_id="u-rf",
        timestamp=datetime.now(timezone.utc),
    )

    seen = {}

    async def _send():
        # 发送这一刻，DB 里应已存在一条 SENDING 出站记录（record-first 的核心）
        row = await db_session.scalar(
            select(Message).where(
                Message.direction == MessageDirection.OUTBOUND,
                Message.trigger_id == inbound.id,
            )
        )
        seen["status"] = row.status if row else None
        return ChannelSendResult(success=True, message_id="sent-rf")

    result = await engine._send_and_record(
        message=channel_msg, service_case_id=case.id, content="回复内容",
        trigger_id=inbound.id, send=_send,
    )

    assert result.success is True
    assert seen["status"] == MessageStatus.SENDING
    row = await db_session.scalar(
        select(Message).where(
            Message.direction == MessageDirection.OUTBOUND,
            Message.trigger_id == inbound.id,
        )
    )
    assert row.status == MessageStatus.SENT
    assert row.sent_at is not None
    assert row.wecom_message_id == "sent-rf"


@pytest.mark.asyncio
async def test_interrupted_send_not_duplicated_by_recovery(db_session, monkeypatch):
    """Fix③：崩溃留下的 SENDING 出站记录带 trigger_id，recover_stuck 视为已回复、不重复补发。"""
    from datetime import timedelta
    from app.services.ranren_ai.engine import recover_stuck_inbound_messages
    from app.models.service_case import ServiceCase, ServiceCaseStatus
    from app.models.message import (
        Message,
        MessageDirection,
        MessagePurpose,
        MessageSenderType,
        MessageStatus,
    )
    from app.models.user import User

    user = User(name="中断发送用户")
    db_session.add(user)
    await db_session.flush()
    now = datetime.now(timezone.utc)
    case = ServiceCase(
        user_id=user.id, case_no="CASE-INT", status=ServiceCaseStatus.IN_SERVICE,
        ai_auto_reply_enabled=True, group_id="group-int", questionnaire_token="token-int",
    )
    db_session.add(case)
    await db_session.flush()
    inbound = Message(
        service_case_id=case.id, group_id="group-int", wecom_message_id="in-int",
        content="被中断的问句", message_type="text",
        direction=MessageDirection.INBOUND, sender_type=MessageSenderType.USER,
        purpose=MessagePurpose.CHAT, status=MessageStatus.RECEIVED,
        analysis_json={"_qiwe_meta": {}}, created_at=now - timedelta(minutes=1),
    )
    db_session.add(inbound)
    await db_session.flush()
    # 模拟“已发出但没回填”崩溃：留下一条 SENDING 出站记录
    stuck = Message(
        service_case_id=case.id, group_id="group-int", wecom_message_id=None,
        content="在途回复", message_type="markdown",
        direction=MessageDirection.OUTBOUND, sender_type=MessageSenderType.BOT,
        purpose=MessagePurpose.CHAT, status=MessageStatus.SENDING,
        trigger_id=inbound.id, created_at=now - timedelta(seconds=30),
    )
    db_session.add(stuck)
    await db_session.flush()

    reprocess_calls = []

    async def fake_reprocess_message(self, message):
        reprocess_calls.append(message)

    monkeypatch.setattr(RanrenAIEngine, "reprocess_message", fake_reprocess_message)
    await recover_stuck_inbound_messages(FakeChannel(), now)
    assert reprocess_calls == []  # 已有出站(SENDING)记录 → 不重复补发


@pytest.mark.asyncio
async def test_reconcile_interrupted_sends_marks_failed(db_session):
    """Fix③：开机对账把重启前残留的 SENDING 标为 FAILED(不自动重发)；新起的 SENDING 不动。"""
    from datetime import timedelta
    from app.services.ranren_ai.engine import reconcile_interrupted_sends
    from app.models.message import (
        Message,
        MessageDirection,
        MessagePurpose,
        MessageSenderType,
        MessageStatus,
    )

    now = datetime.now(timezone.utc)
    old = Message(
        group_id="group-rec", content="旧的在途", message_type="markdown",
        direction=MessageDirection.OUTBOUND, sender_type=MessageSenderType.BOT,
        purpose=MessagePurpose.CHAT, status=MessageStatus.SENDING,
        created_at=now - timedelta(minutes=1),
    )
    fresh = Message(
        group_id="group-rec", content="刚起的在途", message_type="markdown",
        direction=MessageDirection.OUTBOUND, sender_type=MessageSenderType.BOT,
        purpose=MessagePurpose.CHAT, status=MessageStatus.SENDING,
        created_at=now,  # 晚于 cutoff(now-15s) → 不动
    )
    db_session.add_all([old, fresh])
    await db_session.flush()

    await reconcile_interrupted_sends(now)

    await db_session.refresh(old)
    await db_session.refresh(fresh)
    assert old.status == MessageStatus.FAILED
    assert old.error and "interrupted" in old.error
    assert fresh.status == MessageStatus.SENDING


@pytest.mark.asyncio
async def test_qiwe_aggregator_persists_and_marks_inbound_events(db_session):
    """Fix②：进 8s 聚合窗口前先落 inbound_events；处理成功后标 processed。"""
    from sqlalchemy import select
    from app.models.inbound_event import InboundEvent

    engine = FakeEngine()
    aggregator = QiWeMessageAggregator(FakeChannel(), window_seconds=10.0, engine=engine)
    message_id = f"inbox-ok-{secrets.token_hex(4)}"
    message = ChannelMessage(
        channel="qiwe", chat_id="room-inbox", chat_type="group",
        text="窗口内消息", message_id=message_id, user_id="u1",
        timestamp=datetime.now(timezone.utc),
    )

    await aggregator.enqueue(message)
    pending = await db_session.scalar(select(InboundEvent).where(InboundEvent.message_id == message_id))
    assert pending is not None
    assert pending.status == InboundEvent.STATUS_PENDING

    await aggregator.flush(("room-inbox", "u1"))
    await db_session.refresh(pending)
    assert len(engine.messages) == 1
    assert pending.status == InboundEvent.STATUS_PROCESSED
    assert pending.processed_at is not None


@pytest.mark.asyncio
async def test_qiwe_aggregator_keeps_inbound_event_pending_when_engine_fails(db_session):
    """Fix②：引擎抛异常时不标 processed，保留给开机 replay 兜底。"""
    from sqlalchemy import select
    from app.models.inbound_event import InboundEvent

    aggregator = QiWeMessageAggregator(FakeChannel(), window_seconds=10.0, engine=FailingEngine())
    message_id = f"inbox-fail-{secrets.token_hex(4)}"
    message = ChannelMessage(
        channel="qiwe", chat_id="room-inbox-fail", chat_type="group",
        text="会失败的消息", message_id=message_id, user_id="u1",
        timestamp=datetime.now(timezone.utc),
    )

    await aggregator.enqueue(message)
    await aggregator.flush(("room-inbox-fail", "u1"))

    event = await db_session.scalar(select(InboundEvent).where(InboundEvent.message_id == message_id))
    assert event.status == InboundEvent.STATUS_PENDING
    assert event.processed_at is None


@pytest.mark.asyncio
async def test_replay_pending_inbound_events_reprocesses_unhandled_event(db_session, monkeypatch):
    """Fix②：开机重放卡在聚合窗口、从未进引擎的 pending inbound_event。"""
    from datetime import timedelta
    from sqlalchemy import select
    from app.models.inbound_event import InboundEvent
    from app.services.qiwe_service.inbox import replay_pending_inbound_events

    now = datetime.now(timezone.utc)
    event = InboundEvent(
        channel="qiwe", chat_id="room-replay", user_id="u1", message_id="replay-1",
        text="重放我", message_type="text", raw_payload={"k": "v"},
        message_timestamp=now - timedelta(minutes=1),
        status=InboundEvent.STATUS_PENDING,
        created_at=now - timedelta(minutes=1),
    )
    db_session.add(event)
    await db_session.flush()

    calls = []

    async def fake_handle_message(self, message):
        calls.append(message)

    monkeypatch.setattr(RanrenAIEngine, "handle_message", fake_handle_message)
    await replay_pending_inbound_events(FakeChannel(), now)

    assert len(calls) == 1
    assert calls[0].text == "重放我"
    assert calls[0].message_id == "replay-1"
    await db_session.refresh(event)
    assert event.status == InboundEvent.STATUS_PROCESSED


@pytest.mark.asyncio
async def test_replay_pending_inbound_events_reprocesses_recent_pre_startup_event(db_session, monkeypatch):
    """Fix②回归：启动前 1 秒写入的 pending event 也必须重放，不能被 15s cutoff 漏掉。"""
    from datetime import timedelta
    from app.models.inbound_event import InboundEvent
    from app.services.qiwe_service.inbox import replay_pending_inbound_events

    now = datetime.now(timezone.utc)
    event = InboundEvent(
        channel="qiwe", chat_id="room-replay-recent", user_id="u1", message_id="replay-recent-1",
        text="刚进窗口就重启", message_type="text", raw_payload=None,
        message_timestamp=now - timedelta(seconds=1),
        status=InboundEvent.STATUS_PENDING,
        created_at=now - timedelta(seconds=1),
    )
    db_session.add(event)
    await db_session.flush()

    calls = []

    async def fake_handle_message(self, message):
        calls.append(message)

    monkeypatch.setattr(RanrenAIEngine, "handle_message", fake_handle_message)
    await replay_pending_inbound_events(FakeChannel(), now)

    assert len(calls) == 1
    assert calls[0].text == "刚进窗口就重启"
    await db_session.refresh(event)
    assert event.status == InboundEvent.STATUS_PROCESSED


@pytest.mark.asyncio
async def test_replay_pending_inbound_events_skips_already_handled_event(db_session, monkeypatch):
    """Fix②：若同窗口已存在入站 Message，说明正常进入过引擎，只标 processed、不重放。"""
    from datetime import timedelta
    from app.models.inbound_event import InboundEvent
    from app.models.message import Message, MessageDirection, MessagePurpose, MessageSenderType, MessageStatus
    from app.services.qiwe_service.inbox import replay_pending_inbound_events

    now = datetime.now(timezone.utc)
    event = InboundEvent(
        channel="qiwe", chat_id="room-skip", user_id="u1", message_id="skip-1",
        text="别重放", message_type="text", raw_payload=None,
        message_timestamp=now - timedelta(minutes=1),
        status=InboundEvent.STATUS_PENDING,
        created_at=now - timedelta(minutes=1),
    )
    db_session.add(event)
    db_session.add(Message(
        group_id="room-skip", wecom_message_id="merged-skip", content="别重放", message_type="text",
        direction=MessageDirection.INBOUND, sender_type=MessageSenderType.USER,
        purpose=MessagePurpose.CHAT, status=MessageStatus.RECEIVED,
        created_at=now - timedelta(seconds=50),
    ))
    await db_session.flush()

    calls = []

    async def fake_handle_message(self, message):
        calls.append(message)

    monkeypatch.setattr(RanrenAIEngine, "handle_message", fake_handle_message)
    await replay_pending_inbound_events(FakeChannel(), now)

    assert calls == []
    await db_session.refresh(event)
    assert event.status == InboundEvent.STATUS_PROCESSED
