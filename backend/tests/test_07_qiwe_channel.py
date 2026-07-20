from __future__ import annotations

import asyncio
import secrets
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.api.routes.qiwe import _bypass_aggregator, _channel_message_from_qiwe, _is_authorized_callback
from app.services.agent.prompts import build_agent_user_prompt
from app.services.channels.base import ChannelMessage, ChannelSendResult
from app.services.channels.qiwe import QiWeChannel
from app.services.content_policy import (
    get_qiwe_group_nickname,
    remember_qiwe_group_nicknames,
)
from app.services.qiwe_service.aggregator import QiWeMessageAggregator, _merge_messages
from app.services.ranren_ai.engine import (
    GROUP_AI_INTRODUCTION,
    RanrenAIEngine,
    _current_query_body,
    _image_description_is_available,
)
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
async def test_qiwe_aggregator_merges_image_then_text():
    engine = FakeEngine()
    aggregator = QiWeMessageAggregator(FakeChannel(), window_seconds=0.01, engine=engine)

    await aggregator.enqueue(
        ChannelMessage(
            channel="qiwe",
            chat_id="room-1",
            chat_type="group",
            text="[图片]",
            message_id="img-1",
            user_id="u1",
            message_type="image",
            raw_payload={"msgType": 101, "msgData": {"fileBigHttpUrl": "https://example.qpic.cn/a.jpg"}},
            timestamp=datetime.now(timezone.utc),
        )
    )
    await aggregator.enqueue(
        ChannelMessage(
            channel="qiwe",
            chat_id="room-1",
            chat_type="group",
            text="这个能吃吗",
            message_id="txt-1",
            user_id="u1",
            timestamp=datetime.now(timezone.utc),
        )
    )
    await asyncio.sleep(0.03)

    assert len(engine.messages) == 1
    assert engine.messages[0].message_type == "text"
    assert engine.messages[0].text == "[图片]\n这个能吃吗"
    assert isinstance(engine.messages[0].raw_payload, list)


def test_qiwe_merge_single_image_keeps_image_type():
    image = ChannelMessage(
        channel="qiwe",
        chat_id="room-1",
        chat_type="group",
        text="[图片]",
        message_id="img-1",
        user_id="u1",
        message_type="image",
        raw_payload={"msgType": 101, "msgData": {"fileBigHttpUrl": "https://example.qpic.cn/a.jpg"}},
        timestamp=datetime.now(timezone.utc),
    )

    merged = _merge_messages([image])

    assert merged.message_type == "image"
    assert merged.text == "[图片]"


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
async def test_qiwe_aggregator_queues_immediate_text_behind_pending_image():
    engine = FakeEngine()
    aggregator = QiWeMessageAggregator(FakeChannel(), window_seconds=0.01, engine=engine)

    await aggregator.enqueue(
        ChannelMessage(
            channel="qiwe",
            chat_id="room-1",
            chat_type="group",
            text="[图片]",
            message_id="img-pending",
            user_id="u1",
            message_type="image",
            raw_payload={"msgType": 101, "msgData": {"fileBigHttpUrl": "https://example.qpic.cn/a.jpg"}},
            timestamp=datetime.now(timezone.utc),
        )
    )
    await aggregator.dispatch_now(
        ChannelMessage(
            channel="qiwe",
            chat_id="room-1",
            chat_type="group",
            text="@机器人 这个能吃吗",
            message_id="txt-immediate",
            user_id="u1",
            timestamp=datetime.now(timezone.utc),
        )
    )
    await asyncio.sleep(0.03)

    assert len(engine.messages) == 1
    assert engine.messages[0].text == "[图片]\n@机器人 这个能吃吗"


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


def test_failed_image_description_is_not_available_for_ai_context():
    assert _image_description_is_available("用户上传了一张图片，但解析描述失败。") is False
    assert _image_description_is_available("用户上传了一张图片，但解析描述超时。") is False
    assert _image_description_is_available("用户上传了一张餐食照片，可见米饭，置信度：高。") is True


def test_current_query_body_uses_compact_speaker_and_image_lines():
    analysis = {
        "_qiwe_meta": {"sender_type": "user"},
        "image_prompt": {"status": "ok", "content": "可见米饭和青菜，置信度：中。"},
    }

    assert _current_query_body(analysis, "这个能吃吗") == "用户：这个能吃吗\n图片内容：可见米饭和青菜，置信度：中。"


def test_current_query_body_for_image_only_failure():
    analysis = {
        "_qiwe_meta": {"sender_type": "user"},
        "image_prompt": {"status": "failed", "content": "识别失败，无法可靠读取。"},
    }

    assert _current_query_body(analysis, "") == "用户：（发了一张图片）\n图片内容：识别失败，无法可靠读取。"


def test_current_query_body_uses_staff_group_nickname():
    analysis = {
        "_qiwe_meta": {
            "sender_type": "staff",
            "sender_name": "奢啡加速燃营养师-勇哥",
        }
    }

    assert _current_query_body(analysis, "这个用户今天第几天？") == (
        "奢啡加速燃营养师-勇哥：这个用户今天第几天？"
    )


def test_agent_prompt_does_not_repeat_staff_speaker_prefix():
    speaker = "奢啡加速燃营养师-勇哥"
    prompt = build_agent_user_prompt(
        user_query=f"{speaker}：这个用户今天第几天？",
        query_speaker=speaker,
        recent_messages=[],
        skill_catalog="无",
        current_time_context="2026-07-16 10:00",
        memory_context="无",
        knowledge_context="无",
    )

    assert prompt.count(f"{speaker}：") == 1


@pytest.mark.asyncio
async def test_qiwe_group_nickname_cache_is_scoped_by_group(db_session):
    await remember_qiwe_group_nicknames(
        db_session,
        "room-nickname-a",
        {"staff-1": "勇哥", "staff-2": "阿杰"},
    )
    await remember_qiwe_group_nicknames(
        db_session,
        "room-nickname-b",
        {"staff-1": "王老师"},
    )

    assert await get_qiwe_group_nickname(db_session, "room-nickname-a", "staff-1") == "勇哥"
    assert await get_qiwe_group_nickname(db_session, "room-nickname-a", "staff-2") == "阿杰"
    assert await get_qiwe_group_nickname(db_session, "room-nickname-b", "staff-1") == "王老师"
    assert await get_qiwe_group_nickname(db_session, "room-nickname-b", "missing") == ""


@pytest.mark.asyncio
async def test_qiwe_room_member_nicknames_uses_group_display_name(monkeypatch):
    channel = QiWeChannel()

    async def fake_get_room_detail(room_id):
        return {
            "roomList": [
                {
                    "roomId": room_id,
                    "memberList": [
                        {"userId": "staff-1", "name": "勇哥"},
                        {"userId": "staff-2", "name": ""},
                    ],
                }
            ]
        }

    monkeypatch.setattr(channel, "get_room_detail", fake_get_room_detail)

    assert await channel.get_room_member_nicknames("room-a") == {"staff-1": "勇哥"}


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
    assert _bypass_aggregator(message) is False


def test_qiwe_callback_file_event_to_channel_message():
    message = _channel_message_from_qiwe(
        {
            "cmd": 15000,
            "msgType": 102,
            "userId": "bot-user",
            "fromRoomId": 10926518308089720,
            "senderId": 7881300658070689,
            "senderName": "用户A",
            "timestamp": 1779951380,
            "msgUniqueIdentifier": "unique-file-1",
            "msgData": {
                "fileHttpUrl": "https://imunion.weixin.qq.com/cgi-bin/mmae-bin/tpdownloadmedia?param=v1_x",
                "fileAesKey": "aes-key-here",
                "fileAuthKey": "auth-key-here",
                "fileSize": 39710,
                "filename": "阿杰_60-90天健康减脂执行方案.docx",
            },
        }
    )

    assert message is not None
    assert message.channel == "qiwe"
    assert message.chat_id == "10926518308089720"
    assert message.chat_type == "group"
    assert message.user_id == "7881300658070689"
    assert message.message_type == "file"
    assert message.text == "[文件]"
    assert _bypass_aggregator(message) is True



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
async def test_first_group_message_creates_case_but_stays_silent_before_introduction(
    db_session, monkeypatch
):
    """问卷不再是建单前置；首条群消息建单并留痕，引荐前不调 Agent。"""
    from sqlalchemy import select
    from app.models.message import Message, MessageDirection
    from app.models.service_case import ServiceCase
    from app.models.user import User

    async def no_staff(*args, **kwargs):
        return []

    async def should_not_analyze(*args, **kwargs):
        raise AssertionError("pre-introduction message must not enter risk/reply processing")

    monkeypatch.setattr("app.services.content_policy.get_qiwei_staff_senders", no_staff)
    monkeypatch.setattr(
        "app.services.ranren_ai.engine.analyze_intervention", should_not_analyze
    )

    group_id = f"room-awaiting-intro-{secrets.token_hex(4)}"
    await RanrenAIEngine(FakeChannel()).handle_message(
        ChannelMessage(
            channel="qiwe",
            chat_id=group_id,
            chat_type="group",
            text="大家好",
            message_id=f"first-{secrets.token_hex(4)}",
            user_id="customer-1",
            user_name="用户小燃",
            timestamp=datetime.now(timezone.utc),
        )
    )

    service_case = await db_session.scalar(
        select(ServiceCase).where(ServiceCase.group_id == group_id)
    )
    assert service_case is not None
    assert service_case.ai_introduced_at is None
    case_user = await db_session.get(User, service_case.user_id)
    assert case_user is not None
    assert case_user.wecom_user_id == "customer-1"
    assert case_user.name == "用户小燃"

    rows = list(
        await db_session.scalars(
            select(Message)
            .where(Message.group_id == group_id)
            .order_by(Message.id.asc())
        )
    )
    assert len(rows) == 1
    assert rows[0].direction == MessageDirection.INBOUND
    assert rows[0].service_case_id == service_case.id
    assert rows[0].analysis_json["_reply_gate"]["state"] == "awaiting_staff_introduction"


@pytest.mark.asyncio
async def test_first_staff_mention_only_introduces_then_opens_normal_reply(
    db_session, monkeypatch
):
    """首次员工 @ 的正文必须被忽略；介绍成功后才开放用户/员工问题。"""
    from sqlalchemy import select
    from app.models.message import Message, MessageDirection
    from app.models.service_case import ServiceCase
    from app.models.user import User
    from app.services.ranren_ai.engine_graph import ReplyGraph

    async def staff_rules(*args, **kwargs):
        return [{"type": "equals", "pattern": "staff-1"}]

    async def no_risk(*args, **kwargs):
        return {}

    monkeypatch.setattr("app.services.content_policy.get_qiwei_staff_senders", staff_rules)
    monkeypatch.setattr("app.services.ranren_ai.engine.analyze_intervention", no_risk)

    sent_texts: list[str] = []

    class RecordingChannel(FakeChannel):
        async def send_text(self, chat_id, content, **kwargs):
            sent_texts.append(content)
            return ChannelSendResult(
                success=True,
                message_id=f"sent-{len(sent_texts)}",
            )

    reply_inputs: list[str] = []

    async def observe_reply(self, state):
        reply_inputs.append(state["message"].text)
        return {"_halt": True}

    monkeypatch.setattr(ReplyGraph, "_prep", observe_reply)

    group_id = f"room-intro-{secrets.token_hex(4)}"
    engine = RanrenAIEngine(RecordingChannel())
    mention_payload = {
        "userId": "bot-1",
        "msgData": {"atList": [{"userId": "bot-1"}]},
    }

    await engine.handle_message(
        ChannelMessage(
            channel="qiwe",
            chat_id=group_id,
            chat_type="group",
            text="@燃人AI 这句内容不应该被处理",
            message_id="intro-trigger",
            user_id="staff-1",
            user_name="班主任",
            raw_payload=mention_payload,
            timestamp=datetime.now(timezone.utc),
        )
    )

    service_case = await db_session.scalar(
        select(ServiceCase).where(ServiceCase.group_id == group_id)
    )
    assert service_case is not None
    assert service_case.ai_introduced_at is not None
    assert service_case.ai_introduced_by_wecom_user_id == "staff-1"
    assert sent_texts == [GROUP_AI_INTRODUCTION]
    assert reply_inputs == []

    intro_trigger = await db_session.scalar(
        select(Message).where(
            Message.group_id == group_id,
            Message.direction == MessageDirection.INBOUND,
            Message.wecom_message_id == "intro-trigger",
        )
    )
    assert intro_trigger is not None
    assert service_case.ai_introduction_trigger_message_id == intro_trigger.id
    assert intro_trigger.analysis_json["_reply_gate"]["state"] == "introduction_trigger"

    # 引荐后：员工未 @ 仍静默，用户直接说话可回，员工 @ 也可回。
    await engine.handle_message(
        ChannelMessage(
            channel="qiwe",
            chat_id=group_id,
            chat_type="group",
            text="用户今天状态不错",
            message_id="staff-plain",
            user_id="staff-1",
            user_name="班主任",
            timestamp=datetime.now(timezone.utc),
        )
    )
    await engine.handle_message(
        ChannelMessage(
            channel="qiwe",
            chat_id=group_id,
            chat_type="group",
            text="我今天有点饿",
            message_id="customer-question",
            user_id="customer-1",
            user_name="用户小燃",
            timestamp=datetime.now(timezone.utc),
        )
    )
    await engine.handle_message(
        ChannelMessage(
            channel="qiwe",
            chat_id=group_id,
            chat_type="group",
            text="@燃人AI 用户最近的记录怎么样？",
            message_id="staff-question",
            user_id="staff-1",
            user_name="班主任",
            raw_payload=mention_payload,
            timestamp=datetime.now(timezone.utc),
        )
    )

    assert reply_inputs == ["我今天有点饿", "用户最近的记录怎么样？"]
    assert sent_texts == [GROUP_AI_INTRODUCTION]
    case_user = await db_session.get(User, service_case.user_id)
    assert case_user is not None
    assert case_user.wecom_user_id == "customer-1"
    assert case_user.name == "用户小燃"


@pytest.mark.asyncio
async def test_sent_introduction_reconciles_state_without_resending(
    db_session, monkeypatch
):
    """若发送成功后在状态落库前崩溃，下一次触发只补状态、不重复发介绍。"""
    from sqlalchemy import select
    from app.models.message import (
        Message,
        MessageDirection,
        MessagePurpose,
        MessageSenderType,
        MessageStatus,
    )
    from app.models.service_case import ServiceCase

    async def staff_rules(*args, **kwargs):
        return [{"type": "equals", "pattern": "staff-reconcile"}]

    monkeypatch.setattr("app.services.content_policy.get_qiwei_staff_senders", staff_rules)

    group_id = f"room-intro-reconcile-{secrets.token_hex(4)}"
    engine = RanrenAIEngine(FakeChannel())
    first_message = ChannelMessage(
        channel="qiwe",
        chat_id=group_id,
        chat_type="group",
        text="先创建群服务单",
        message_id="before-intro",
        user_id="staff-reconcile",
        user_name="班主任",
        timestamp=datetime.now(timezone.utc),
    )
    await engine.handle_message(first_message)

    service_case = await db_session.scalar(
        select(ServiceCase).where(ServiceCase.group_id == group_id)
    )
    assert service_case is not None
    trigger = Message(
        service_case_id=service_case.id,
        group_id=group_id,
        wecom_message_id="intro-trigger-reconcile",
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.STAFF,
        message_type="text",
        content="@燃人AI",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
        analysis_json={
            "_qiwe_meta": {
                "sender_id": "staff-reconcile",
                "sender_type": "staff",
                "explicit_bot_query_text": "燃人AI",
            },
            "_reply_gate": {"state": "introduction_trigger"},
        },
    )
    db_session.add(trigger)
    await db_session.flush()
    sent_at = datetime.now(timezone.utc)
    db_session.add(
        Message(
            service_case_id=service_case.id,
            group_id=group_id,
            wecom_message_id="intro-sent-reconcile",
            direction=MessageDirection.OUTBOUND,
            sender_type=MessageSenderType.BOT,
            message_type="markdown",
            content=GROUP_AI_INTRODUCTION,
            purpose=MessagePurpose.CHAT,
            status=MessageStatus.SENT,
            sent_at=sent_at,
            trigger_id=trigger.id,
            analysis_json={"_system_event": "group_ai_introduction"},
        )
    )
    await db_session.flush()

    class NoResendChannel(FakeChannel):
        async def send_text(self, chat_id, content, **kwargs):
            raise AssertionError("sent introduction must not be sent twice")

    reconciled = await RanrenAIEngine(NoResendChannel())._reply_group_introduction(
        ChannelMessage(
            channel="qiwe",
            chat_id=group_id,
            chat_type="group",
            text="@燃人AI",
            message_id="retry-after-restart",
            user_id="staff-reconcile",
            user_name="班主任",
            timestamp=datetime.now(timezone.utc),
        ),
        service_case_id=service_case.id,
        trigger_id=trigger.id,
    )

    await db_session.refresh(service_case)
    assert reconciled is True
    assert service_case.ai_introduced_at == sent_at
    assert service_case.ai_introduced_by_wecom_user_id == "staff-reconcile"
    assert service_case.ai_introduction_trigger_message_id == trigger.id


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
        ai_introduced_at=datetime.now(timezone.utc),
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
        ai_introduced_at=datetime.now(timezone.utc),
    )

    monkeypatch.setattr("app.services.ranren_ai.engine.SessionLocal", lambda *args, **kwargs: FakeSession())
    monkeypatch.setattr("app.services.content_policy.get_qiwei_staff_senders", fake_get_qiwei_staff_senders)
    monkeypatch.setattr(RanrenAIEngine, "_latest_active_case", lambda self, session, chat_id: asyncio.sleep(0, result=fake_case))
    monkeypatch.setattr(RanrenAIEngine, "_recent_user_messages", lambda *args, **kwargs: asyncio.sleep(0, result=[]))
    monkeypatch.setattr(RanrenAIEngine, "_store_intervention_analysis", lambda *args, **kwargs: asyncio.sleep(0, result=None))

    # reply 子图以「子图即节点」原生挂载后，主流程不再经过 _reply_with_ai；观测钩子随缝
    # 平移到子图入口节点 _prep：记录 (text, trigger.sender_type) 后 _halt 短路（等价旧 fake 的 no-op）。
    from app.services.ranren_ai.engine_graph import ReplyGraph

    async def fake_reply_prep(self, state):
        trigger_id = state.get("trigger_id")
        trigger = added_messages[(trigger_id or 1) - 1]
        reply_inputs.append((state["message"].text, trigger.sender_type))
        return {"_halt": True}

    monkeypatch.setattr(ReplyGraph, "_prep", fake_reply_prep)

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
async def test_qiwe_file_upload_acks_user_but_stays_silent_for_staff(monkeypatch):
    """文件上传：用户发→回执一句“文件收到啦…”；班主任发→静默不回（AI 不拆班主任台）。

    _process_inbound_file 的下载/解密/抽取已在 test_10 单测覆盖，这里打桩成 no-op，
    只验证 handle_message 里“按发送者身份决定是否回执”的分支。
    """
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
        return [{"type": "equals", "pattern": "上海 Lucy"}]

    fake_case = SimpleNamespace(
        id=42,
        case_no="CASE-42",
        group_id="room-file",
        questionnaire_token="token-42",
        ai_introduced_at=datetime.now(timezone.utc),
    )

    processed: list[int] = []

    async def fake_process_inbound_file(self, message_id, message, service_case_id):
        processed.append(message_id)

    sent_texts: list[str] = []

    async def fake_send_and_record(self, *, message, service_case_id, content, trigger_id, send):
        # _send_and_record 的 record-first 落库已在 test_send_and_record_is_record_first 覆盖，
        # 这里只记录回执内容，隔离“是否回执”的分支判断。
        sent_texts.append(content)
        return ChannelSendResult(success=True, message_id="ack-1")

    monkeypatch.setattr("app.services.ranren_ai.engine.SessionLocal", lambda *a, **k: FakeSession())
    monkeypatch.setattr("app.services.content_policy.get_qiwei_staff_senders", fake_get_qiwei_staff_senders)
    monkeypatch.setattr(RanrenAIEngine, "_latest_active_case", lambda self, session, chat_id: asyncio.sleep(0, result=fake_case))
    monkeypatch.setattr(RanrenAIEngine, "_recent_user_messages", lambda *a, **k: asyncio.sleep(0, result=[]))
    monkeypatch.setattr(RanrenAIEngine, "_process_inbound_file", fake_process_inbound_file)
    monkeypatch.setattr(RanrenAIEngine, "_send_and_record", fake_send_and_record)

    engine = RanrenAIEngine(FakeChannel())

    # 用户上传文件 → 回执一句
    user_file = ChannelMessage(
        channel="qiwe",
        chat_id="room-file",
        chat_type="group",
        text="[文件]",
        message_type="file",
        message_id="f-user",
        user_id="user-1",
        user_name="普通客户",
        timestamp=datetime.now(timezone.utc),
    )
    await engine.handle_message(user_file)

    # 班主任上传文件 → 静默不回
    staff_file = ChannelMessage(
        channel="qiwe",
        chat_id="room-file",
        chat_type="group",
        text="[文件]",
        message_type="file",
        message_id="f-staff",
        user_id="user-2",
        user_name="上海 Lucy",
        timestamp=datetime.now(timezone.utc),
    )
    await engine.handle_message(staff_file)

    assert processed == [1, 2]  # 两条都解析入库
    assert sent_texts == ["文件收到啦，需要我帮你看什么？"]  # 只对用户回执一次


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
        ai_introduced_at=now - timedelta(days=1),
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
        ai_introduced_at=now - timedelta(days=1),
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
        ai_introduced_at=now - timedelta(days=1),
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
        ai_introduced_at=now - timedelta(days=1),
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
        ai_introduced_at=now - timedelta(days=1),
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
        ai_introduced_at=now - timedelta(days=1),
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

    # Scenario G: 阶段已完结群的明确续服指令（应恢复，否则部署窗口会丢指令）
    case_g = ServiceCase(
        user_id=user.id,
        case_no="CASE-G",
        status=ServiceCaseStatus.COMPLETED,
        ai_auto_reply_enabled=True,
        group_id="group-g",
        questionnaire_token="token-g",
        ai_introduced_at=now - timedelta(days=1),
    )
    db_session.add(case_g)
    await db_session.flush()
    msg_g = Message(
        service_case_id=case_g.id,
        group_id="group-g",
        wecom_message_id="msg-g",
        content="@燃人AI 继续服务",
        message_type="text",
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.USER,
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
        analysis_json={"_qiwe_meta": {"explicit_bot_query_text": "继续服务"}},
        created_at=now - timedelta(minutes=1),
    )
    db_session.add(msg_g)

    # Scenario H: 引荐前用户消息即使后来已引荐，也不得在重启后迟到补回。
    case_h = ServiceCase(
        user_id=user.id,
        case_no="CASE-H",
        status=ServiceCaseStatus.CREATED,
        ai_auto_reply_enabled=True,
        group_id="group-h",
        questionnaire_token="token-h",
        ai_introduced_at=now - timedelta(seconds=30),
    )
    db_session.add(case_h)
    await db_session.flush()
    msg_h = Message(
        service_case_id=case_h.id,
        group_id="group-h",
        wecom_message_id="msg-h",
        content="这是引荐之前说的话。",
        message_type="text",
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.USER,
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
        analysis_json={
            "_qiwe_meta": {},
            "_reply_gate": {"state": "awaiting_staff_introduction"},
        },
        created_at=now - timedelta(minutes=1),
    )
    db_session.add(msg_h)
    
    await db_session.flush()
    
    reprocess_calls = []
    async def fake_reprocess_message(self, message):
        reprocess_calls.append(message)
    monkeypatch.setattr(RanrenAIEngine, "reprocess_message", fake_reprocess_message)
    
    await recover_stuck_inbound_messages(FakeChannel(), now)
    
    assert {item.id for item in reprocess_calls} == {msg_a.id, msg_g.id}
    assert {item.content for item in reprocess_calls} == {
        "我需要补发这个回复。",
        "@燃人AI 继续服务",
    }


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
async def test_qiwe_immediate_dispatch_persists_and_marks_inbound_event(db_session):
    """绕过聚合的控制/文件类消息也要先落 durable inbox，成功后标 processed。"""
    from sqlalchemy import select
    from app.models.inbound_event import InboundEvent

    engine = FakeEngine()
    aggregator = QiWeMessageAggregator(FakeChannel(), window_seconds=10.0, engine=engine)
    message_id = f"inbox-now-{secrets.token_hex(4)}"
    message = ChannelMessage(
        channel="qiwe", chat_id="room-inbox-now", chat_type="group",
        text="[文件]", message_id=message_id, user_id="u1",
        message_type="file", timestamp=datetime.now(timezone.utc),
    )

    await aggregator.dispatch_now(message)

    event = await db_session.scalar(select(InboundEvent).where(InboundEvent.message_id == message_id))
    assert event is not None
    assert len(engine.messages) == 1
    assert event.event_type == InboundEvent.EVENT_MESSAGE
    assert event.status == InboundEvent.STATUS_PROCESSED
    assert event.processed_at is not None


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


def test_spam_block_notice_parses_and_ignores_normal():
    from app.api.routes.qiwe import _spam_block_notice

    # 风控拦截通知：msgType=1011 + spam_block_ 前缀 → 抽出渠道id和解码后的原因
    blocked = {
        "cmd": 15000,
        "msgType": 1011,
        "msgUniqueIdentifier": "spam_block_CAQQqb2i0gYY=",
        "msgData": {
            "base64RawData": "5Zug6LSm5Y+36L+d6KeE77yM5b2T5YmN5Yqf6IO95bey6KKr6ZmQ5Yi25L2/55So44CCIOafpeeci+ivpuaDhQ=="
        },
    }
    result = _spam_block_notice(blocked)
    assert result is not None
    channel_message_id, reason = result
    assert channel_message_id == "CAQQqb2i0gYY="
    assert "因账号违规" in reason

    # 普通文本消息不应被识别为风控通知
    normal = {
        "cmd": 15000,
        "msgType": 2,
        "msgUniqueIdentifier": "123",
        "msgData": {"content": "你好"},
    }
    assert _spam_block_notice(normal) is None


@pytest.mark.asyncio
async def test_mark_outbound_blocked_flips_sent_to_failed(db_session):
    from app.services.ranren_ai.engine import mark_outbound_blocked_by_channel_id
    from app.models.service_case import ServiceCase, ServiceCaseStatus
    from app.models.message import Message, MessageDirection, MessagePurpose, MessageSenderType, MessageStatus
    from app.models.user import User

    user = User(name="风控测试用户")
    db_session.add(user)
    await db_session.flush()
    case = ServiceCase(
        user_id=user.id, case_no="CASE-BLK", status=ServiceCaseStatus.IN_SERVICE,
        ai_auto_reply_enabled=True, group_id="group-blk", questionnaire_token="token-blk",
    )
    db_session.add(case)
    await db_session.flush()

    sent = Message(
        service_case_id=case.id, group_id="group-blk", wecom_message_id="CAQQblocked=",
        content="这条其实被风控拦了。", message_type="text",
        direction=MessageDirection.OUTBOUND, sender_type=MessageSenderType.BOT,
        purpose=MessagePurpose.CHAT, status=MessageStatus.SENT,
        sent_at=datetime.now(timezone.utc),
    )
    db_session.add(sent)
    await db_session.flush()

    hit = await mark_outbound_blocked_by_channel_id("CAQQblocked=", "QiWe风控拦截：因账号违规")
    assert hit is True
    await db_session.refresh(sent)
    assert sent.status == MessageStatus.FAILED
    assert "风控" in (sent.error or "")
    assert sent.sent_at is None

    # 不存在的渠道id → 不命中
    assert await mark_outbound_blocked_by_channel_id("NOPE=", "x") is False


@pytest.mark.asyncio
async def test_qiwe_spam_block_event_persists_processes_and_marks_processed(db_session):
    from sqlalchemy import select
    from app.models.inbound_event import InboundEvent
    from app.models.message import Message, MessageDirection, MessagePurpose, MessageSenderType, MessageStatus
    from app.services.qiwe_service.inbox import (
        persist_qiwe_spam_block_event,
        process_qiwe_spam_block_event,
    )

    sent = Message(
        group_id="group-spam-persist", wecom_message_id="CAQQspam-persist=",
        content="这条稍后被风控通知拦截。", message_type="text",
        direction=MessageDirection.OUTBOUND, sender_type=MessageSenderType.BOT,
        purpose=MessagePurpose.CHAT, status=MessageStatus.SENT,
        sent_at=datetime.now(timezone.utc),
    )
    db_session.add(sent)
    await db_session.flush()

    raw_payload = {
        "cmd": 15000,
        "msgType": 1011,
        "msgUniqueIdentifier": "spam_block_CAQQspam-persist=",
        "msgData": {"base64RawData": "5Zug6LSm5Y+36L+d6KeE"},
    }
    event_id = await persist_qiwe_spam_block_event(
        channel_message_id="CAQQspam-persist=",
        reason="企业微信官方风控拦截：因账号违规",
        raw_payload=raw_payload,
    )

    event = await db_session.scalar(select(InboundEvent).where(InboundEvent.id == event_id))
    assert event is not None
    assert event.event_type == InboundEvent.EVENT_QIWE_SPAM_BLOCK
    assert event.status == InboundEvent.STATUS_PENDING

    handled = await process_qiwe_spam_block_event(
        event_id,
        "CAQQspam-persist=",
        "企业微信官方风控拦截：因账号违规",
    )

    assert handled is True
    await db_session.refresh(sent)
    await db_session.refresh(event)
    assert sent.status == MessageStatus.FAILED
    assert sent.sent_at is None
    assert "风控" in (sent.error or "")
    assert event.status == InboundEvent.STATUS_PROCESSED
    assert event.processed_at is not None


@pytest.mark.asyncio
async def test_replay_pending_inbound_events_processes_qiwe_spam_block_event(db_session):
    from datetime import timedelta
    from app.models.inbound_event import InboundEvent
    from app.models.message import Message, MessageDirection, MessagePurpose, MessageSenderType, MessageStatus
    from app.services.qiwe_service.inbox import replay_pending_inbound_events

    now = datetime.now(timezone.utc)
    sent = Message(
        group_id="group-spam-replay", wecom_message_id="CAQQspam-replay=",
        content="重启窗口丢了后台对账。", message_type="text",
        direction=MessageDirection.OUTBOUND, sender_type=MessageSenderType.BOT,
        purpose=MessagePurpose.CHAT, status=MessageStatus.SENT,
        sent_at=now - timedelta(minutes=1),
    )
    event = InboundEvent(
        event_type=InboundEvent.EVENT_QIWE_SPAM_BLOCK,
        channel="qiwe", chat_id="CAQQspam-replay=", user_id=None,
        message_id="spam_block_CAQQspam-replay=",
        text="企业微信官方风控拦截：因账号违规",
        message_type=InboundEvent.EVENT_QIWE_SPAM_BLOCK,
        raw_payload={
            "cmd": 15000,
            "msgType": 1011,
            "msgUniqueIdentifier": "spam_block_CAQQspam-replay=",
            "msgData": {"base64RawData": "5Zug6LSm5Y+36L+d6KeE"},
        },
        message_timestamp=now - timedelta(minutes=1),
        status=InboundEvent.STATUS_PENDING,
        created_at=now - timedelta(minutes=1),
    )
    db_session.add_all([sent, event])
    await db_session.flush()

    await replay_pending_inbound_events(FakeChannel(), now)

    await db_session.refresh(sent)
    await db_session.refresh(event)
    assert sent.status == MessageStatus.FAILED
    assert sent.sent_at is None
    assert event.status == InboundEvent.STATUS_PROCESSED
