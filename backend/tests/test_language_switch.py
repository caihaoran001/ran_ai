from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.message import Message, MessageDirection, MessagePurpose, MessageSenderType
from app.models.service_case import ServiceCase
from app.services.agent.state import AgentRunContext
from app.services.agent.tools.service_lifecycle_tools import build_service_lifecycle_tools
from app.services.ranren_ai.commands import is_language_switch_command


def test_is_language_switch_command():
    assert is_language_switch_command("@燃人助教 此专属群全程使用英文对话（提示 ）") == "en"
    assert is_language_switch_command("请使用英文") == "en"
    assert is_language_switch_command("@燃人助教 切换为英文") == "en"
    assert is_language_switch_command("Switch to English please") == "en"
    assert is_language_switch_command("Please use English in this chat") == "en"
    assert is_language_switch_command("全程英文") == "en"

    assert is_language_switch_command("@燃人助教 全程中文") == "zh"
    assert is_language_switch_command("请使用中文对话") == "zh"
    assert is_language_switch_command("switch to chinese") == "zh"

    assert is_language_switch_command("Hello how are you today?") is None
    assert is_language_switch_command("今天中午吃了鸡胸肉和西兰花") is None


@pytest.mark.asyncio
async def test_set_service_case_language_tool(db_session: AsyncSession, create_case):
    resp = await create_case({
        "group_id": "group_lang_test_01",
        "user": {"name": "Test User"},
    })
    case_id = resp.json()["id"]
    case = await db_session.get(ServiceCase, case_id)
    assert case.language == "zh"

    # Create a trigger message from staff
    trigger_msg = Message(
        service_case_id=case.id,
        group_id=case.group_id,
        direction=MessageDirection.INBOUND,
        purpose=MessagePurpose.CHAT,
        sender_type=MessageSenderType.STAFF,
        content="@燃人助教 请使用英文",
        analysis_json={"_qiwe_meta": {"explicit_bot_query_text": "请使用英文", "sender_name": "Coach"}},
    )
    db_session.add(trigger_msg)
    await db_session.flush()

    context = AgentRunContext(
        service_case_id=case.id,
        trigger_id=trigger_msg.id,
        channel="test",
        language="zh",
    )
    tools = build_service_lifecycle_tools(context=context, settings=get_settings())
    tool_map = {t.name: t for t in tools}
    assert "set_service_case_language" in tool_map

    lang_tool = tool_map["set_service_case_language"]

    # Switch to English
    res = await lang_tool.ainvoke({"language": "en"})
    assert res["ok"] is True
    assert res["language"] == "en"
    assert res["updated"] is True

    await db_session.refresh(case)
    assert case.language == "en"
    assert len(case.service_lifecycle_events or []) == 1
    assert case.service_lifecycle_events[0]["action"] == "set_service_case_language"
    assert case.service_lifecycle_events[0]["language"] == "en"

    # Call again with already-set language
    res2 = await lang_tool.ainvoke({"language": "English"})
    assert res2["ok"] is True
    assert res2["already_set"] is True


@pytest.mark.asyncio
async def test_set_service_case_language_rejects_customer_request(
    db_session: AsyncSession, create_case
):
    """客户一句 "can you speak English?" 不能切掉整个群的服务语言。

    切换会同时换掉问卷、交付文档、阶段报告和定时提醒，和本文件其他生命周期
    工具是同一条边界：只认已识别工作人员在群里明确 @ 机器人的消息。
    """

    resp = await create_case({
        "group_id": "group_lang_test_02",
        "user": {"name": "Test User"},
    })
    case = await db_session.get(ServiceCase, resp.json()["id"])

    customer_msg = Message(
        service_case_id=case.id,
        group_id=case.group_id,
        direction=MessageDirection.INBOUND,
        purpose=MessagePurpose.CHAT,
        sender_type=MessageSenderType.USER,
        content="@燃人助教 can you speak English?",
        analysis_json={
            "_qiwe_meta": {
                "explicit_bot_query_text": "can you speak English?",
                "sender_name": "Client",
            }
        },
    )
    db_session.add(customer_msg)
    await db_session.flush()

    tools = build_service_lifecycle_tools(
        context=AgentRunContext(
            service_case_id=case.id,
            trigger_id=customer_msg.id,
            channel="test",
            language="zh",
        ),
        settings=get_settings(),
    )
    lang_tool = {t.name: t for t in tools}["set_service_case_language"]

    res = await lang_tool.ainvoke({"language": "en"})
    assert res["ok"] is False
    await db_session.refresh(case)
    assert case.language == "zh"
    assert not (case.service_lifecycle_events or [])

    # 没有明确 @ 机器人的工作人员消息同样不放行
    silent_staff_msg = Message(
        service_case_id=case.id,
        group_id=case.group_id,
        direction=MessageDirection.INBOUND,
        purpose=MessagePurpose.CHAT,
        sender_type=MessageSenderType.STAFF,
        content="今天群里聊到英文资料",
        analysis_json={"_qiwe_meta": {"sender_name": "Coach"}},
    )
    db_session.add(silent_staff_msg)
    await db_session.flush()

    tools = build_service_lifecycle_tools(
        context=AgentRunContext(
            service_case_id=case.id,
            trigger_id=silent_staff_msg.id,
            channel="test",
            language="zh",
        ),
        settings=get_settings(),
    )
    lang_tool = {t.name: t for t in tools}["set_service_case_language"]

    res = await lang_tool.ainvoke({"language": "en"})
    assert res["ok"] is False
    await db_session.refresh(case)
    assert case.language == "zh"
