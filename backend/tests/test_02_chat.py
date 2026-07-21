from __future__ import annotations

import inspect
import secrets
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from app.services.service_case_queries import checkin_day, effective_service_day

import pytest
from sqlalchemy import func, select

from app.models.message import (
    Message,
    MessageDirection,
    MessagePurpose,
    MessageSenderType,
    MessageStatus,
)
from app.models.questionnaire import QuestionnaireSubmission
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user import User
from app.models.user_document import DocumentStatus, DocumentType, UserDocument
from app.services.ranren_ai.engine import (
    RanrenAIEngine,
    _format_chat_history_line,
    _group_bot_control_command,
    _requires_health_handoff,
)
from app.services.channels.base import ChannelMessage
from app.services.intervention_service import (
    mark_intake_age_risk,
    merge_case_insight,
    normalize_insight,
)
from app.services.ai_service.client import AIService


async def _setup_case_with_intake(db_session, cai_haoran_answers):
    group_id = f"test-chat-{secrets.token_hex(6)}"
    token = secrets.token_urlsafe(24)

    user = User(name="聊天测试用户")
    db_session.add(user)
    await db_session.flush()

    case = ServiceCase(
        case_no=f"TEST-CHAT-{secrets.token_hex(4).upper()}",
        user_id=user.id,
        group_id=group_id,
        questionnaire_token=token,
        status=ServiceCaseStatus.GENERATING,
        started_at=datetime.now(timezone.utc),
        ai_introduced_at=datetime.now(timezone.utc),
    )
    db_session.add(case)
    await db_session.flush()

    submission = QuestionnaireSubmission(
        service_case_id=case.id,
        template_key="intake",
        answers_json=cai_haoran_answers,
    )
    db_session.add(submission)
    await db_session.flush()

    return case.id, group_id


async def _ingest_and_analyze(db_session, case_id, group_id, content, mock_ai):
    insight = await mock_ai.extract_intervention_insight(
        source="chat_message",
        content=content,
    )
    insight = normalize_insight(insight, source="chat_message", content=content)
    case = await db_session.scalar(select(ServiceCase).where(ServiceCase.id == case_id))
    assert case is not None
    merge_case_insight(case, insight)

    db_session.add(Message(
        service_case_id=case_id,
        group_id=group_id,
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.USER,
        message_type="text",
        content=content,
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
        analysis_json=insight,
    ))
    await db_session.flush()
    return insight


def test_format_chat_history_line_labels_bot_as_assistant():
    message = Message(
        service_case_id=1,
        group_id="test-group",
        direction=MessageDirection.OUTBOUND,
        sender_type=MessageSenderType.BOT,
        message_type="text",
        content="排时间线是我刚才整理的",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.SENT,
        created_at=datetime(2026, 7, 3, 8, 41, tzinfo=timezone.utc),
    )

    line = _format_chat_history_line(message)

    assert "助教: 排时间线是我刚才整理的" in line
    assert "营养师:" not in line


def test_format_chat_history_line_labels_staff_with_group_nickname():
    message = Message(
        service_case_id=1,
        group_id="test-group",
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.STAFF,
        message_type="text",
        content="晚餐主食减半",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
        analysis_json={"_qiwe_meta": {"sender_name": "奢啡加速燃营养师-勇哥"}},
        created_at=datetime(2026, 7, 3, 8, 41, tzinfo=timezone.utc),
    )

    line = _format_chat_history_line(message)

    assert "奢啡加速燃营养师-勇哥: 晚餐主食减半" in line
    assert "（工作人员）" not in line
    assert "班主任:" not in line


@pytest.mark.asyncio
async def test_normal_message_no_risk(db_session, mock_ai, cai_haoran_answers):
    case_id, group_id = await _setup_case_with_intake(db_session, cai_haoran_answers)
    await _ingest_and_analyze(
        db_session, case_id, group_id,
        "老师好，我已经提交问卷了，接下来要做什么？",
        mock_ai,
    )
    case = await db_session.scalar(select(ServiceCase).where(ServiceCase.id == case_id))
    assert case.risk_level != "high"


@pytest.mark.asyncio
async def test_high_risk_chronic_disease(db_session, mock_ai, cai_haoran_answers):
    case_id, group_id = await _setup_case_with_intake(db_session, cai_haoran_answers)
    await _ingest_and_analyze(
        db_session, case_id, group_id,
        "我有高血压，一直在吃降压药，这个产品有影响吗",
        mock_ai,
    )
    case = await db_session.scalar(select(ServiceCase).where(ServiceCase.id == case_id))
    assert case.risk_level == "high"
    risk_codes = [rf["code"] for rf in (case.risk_flags or [])]
    assert "chronic_disease" in risk_codes


@pytest.mark.asyncio
async def test_emotional_crisis(db_session, mock_ai, cai_haoran_answers):
    case_id, group_id = await _setup_case_with_intake(db_session, cai_haoran_answers)
    await _ingest_and_analyze(
        db_session, case_id, group_id,
        "最近压力太大快崩溃了，什么都不想做",
        mock_ai,
    )
    case = await db_session.scalar(select(ServiceCase).where(ServiceCase.id == case_id))
    assert case.risk_level == "high"

@pytest.mark.asyncio
async def test_refund_intent(db_session, mock_ai, cai_haoran_answers):
    case_id, group_id = await _setup_case_with_intake(db_session, cai_haoran_answers)
    await _ingest_and_analyze(
        db_session, case_id, group_id,
        "我想退款，效果不好想退了",
        mock_ai,
    )
    case = await db_session.scalar(select(ServiceCase).where(ServiceCase.id == case_id))
    assert case.refund_intent_flags is not None
    assert len(case.refund_intent_flags) > 0
    assert any(item.get("code") == "refund_request" for item in (case.intervention_reasons or []))


@pytest.mark.asyncio
async def test_renewal_intent(db_session, mock_ai, cai_haoran_answers):
    case_id, group_id = await _setup_case_with_intake(db_session, cai_haoran_answers)
    await _ingest_and_analyze(
        db_session, case_id, group_id,
        "28天快到了想继续续一期",
        mock_ai,
    )
    case = await db_session.scalar(select(ServiceCase).where(ServiceCase.id == case_id))
    assert case.renewal_intent_flags is not None
    assert len(case.renewal_intent_flags) > 0


def test_explicit_robot_stop_becomes_human_handoff_analysis():
    insight = normalize_insight(
        {
            "requires_human_intervention": True,
            "requires_immediate_safety_reply": False,
            "intervention_reasons": [
                {
                    "code": "robot_stop",
                    "level": "medium",
                    "reason": "用户明确要求停止机器人继续解释。",
                    "evidence": "我不接受机器人继续解释",
                    "source": "chat_message",
                }
            ],
        },
        source="chat_message",
        content="我不接受机器人继续解释，别说了",
    )

    assert insight["requires_human_intervention"] is True
    assert insight["requires_immediate_safety_reply"] is False


def test_intervention_normalizes_agent_context_plan():
    insight = normalize_insight(
        {
            "requires_human_intervention": False,
            "risk_flags": [],
            "intervention_reasons": [],
            "renewal_intent": {"detected": False},
            "refund_intent": {"detected": False},
            "emotion_signal": {"detected": False, "level": "low"},
            "health_consultation": False,
            "agent_context_plan": {
                "message_scene": "casual_interest",
                "memory_scopes": ["preferences", "recent_topics", "warnings", "unknown_scope", "preferences"],
                "tool_hints": ["web_search", "service_case_data", "bad_hint", "web_search"],
                "reason": "用户在聊潮玩兴趣，后续只需要兴趣和最近话题。",
            },
        },
        source="chat_message",
        content="LABUBU之后出圈的是啥",
    )

    assert insight["agent_context_plan"] == {
        "message_scene": "casual_interest",
        # recent_topics 已下线 → 从合法 memory_scopes 过滤掉。
        "memory_scopes": ["preferences", "warnings"],
        "tool_hints": ["web_search", "service_case_data"],
        "reason": "用户在聊潮玩兴趣，后续只需要兴趣和最近话题。",
    }


@pytest.mark.asyncio
async def test_intervention_prompt_keeps_diet_preferences_in_agent_context():
    service = AIService.__new__(AIService)
    captured: dict = {}

    async def fake_generate_text(**kwargs):
        captured.update(kwargs)
        return "{}"

    service.generate_text = fake_generate_text
    await service.extract_intervention_insight(
        source="chat_message",
        content="中午吃了半碗杂粮米，五点就饿了",
        context={},
    )

    prompt = captured["system_prompt"]
    assert "饮食建议和身体反馈至少选择 preferences" in prompt
    assert "需要作息、饮食模式或运动习惯时再选 habits/current_state/warnings" in prompt


def test_high_risk_health_consultation_handoff():
    # 高风险用户(risk_level=high)问需专业判断的健康/方案问题 → @班主任(即便当前句本身无新风险)
    health_q = {"requires_human_intervention": False, "risk_flags": [], "health_consultation": True}
    assert _requires_health_handoff(health_q, "high") is True
    # 高风险用户的普通消息(打卡/闲聊,health_consultation=false) → 不@,AI 正常回
    normal_q = {"requires_human_intervention": False, "risk_flags": [], "health_consultation": False}
    assert _requires_health_handoff(normal_q, "high") is False
    # 非高风险用户问健康问题 → 不因此@(AI 正常答)
    assert _requires_health_handoff(health_q, "normal") is False
    assert _requires_health_handoff(health_q, None) is False


def test_one_off_medical_boundary_does_not_persist_high_risk():
    insight = normalize_insight(
        {
            "requires_human_intervention": True,
            "risk_flags": [{
                "code": "medical_claim",
                "level": "high",
                "reason": "用户要求判断是否应停药",
                "evidence": "我能不能停降压药改吃这个",
                "source": "chat_message",
            }],
        },
        source="chat_message",
        content="我能不能停降压药改吃这个",
    )
    case = ServiceCase(case_no="MEDICAL-BOUNDARY", group_id="g", questionnaire_token="t")

    merge_case_insight(case, insight)

    assert insight["requires_human_intervention"] is True
    assert insight["risk_flags"][0]["level"] == "medium"
    assert case.risk_level != "high"


def test_other_risk_cannot_persist_as_high_risk():
    insight = normalize_insight(
        {
            "requires_human_intervention": True,
            "risk_flags": [{
                "code": "other_risk",
                "level": "high",
                "reason": "模型未能归类的疑似风险",
                "evidence": "用户原话",
                "source": "chat_message",
            }],
        },
        source="chat_message",
        content="用户原话",
    )
    case = ServiceCase(case_no="OTHER-RISK", group_id="g", questionnaire_token="t")

    merge_case_insight(case, insight)

    assert insight["risk_flags"][0]["level"] == "medium"
    assert case.risk_level != "high"


@pytest.mark.asyncio
async def test_handoff_throttle_persists_across_engine_restart(
    db_session, mock_message_channel, cai_haoran_answers
):
    # Fix①：转人工@班主任的节流时间戳落库(service_cases.last_handoff_at)，
    # 部署重启(全新引擎实例、内存态清零)后仍生效，30 分钟内不重复@。
    case_id, _ = await _setup_case_with_intake(db_session, cai_haoran_answers)

    engine_before = RanrenAIEngine(mock_message_channel)
    assert await engine_before._recently_handed_off(case_id) is False
    await engine_before._mark_handoff(case_id)
    assert await engine_before._recently_handed_off(case_id) is True

    # 模拟重启：换一个没有任何内存态的新引擎实例（旧的内存字典实现在这里会返回 False → 重复@）
    engine_after = RanrenAIEngine(mock_message_channel)
    assert await engine_after._recently_handed_off(case_id) is True


@pytest.mark.asyncio
async def test_ai_reply_stored_as_outbound(db_session, mock_ai, cai_haoran_answers):
    case_id, group_id = await _setup_case_with_intake(db_session, cai_haoran_answers)
    await _ingest_and_analyze(
        db_session, case_id, group_id,
        "老师好，今天感觉不错",
        mock_ai,
    )

    db_session.add(Message(
        service_case_id=case_id,
        group_id=group_id,
        direction=MessageDirection.OUTBOUND,
        sender_type=MessageSenderType.BOT,
        message_type="markdown",
        content="收到您的反馈, 继续加油!",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.SENT,
    ))
    await db_session.flush()

    msgs_result = await db_session.scalars(
        select(Message)
        .where(
            Message.service_case_id == case_id,
            Message.direction == MessageDirection.OUTBOUND,
            Message.sender_type == MessageSenderType.BOT,
        )
    )
    outbound_msgs = list(msgs_result)
    assert len(outbound_msgs) > 0
    assert all(m.sender_type == MessageSenderType.BOT for m in outbound_msgs)


async def _count_outbound_bot(db_session, case_id) -> int:
    result = await db_session.scalars(
        select(func.count(Message.id)).where(
            Message.service_case_id == case_id,
            Message.direction == MessageDirection.OUTBOUND,
            Message.sender_type == MessageSenderType.BOT,
        )
    )
    return result.one()


@pytest.mark.asyncio
@pytest.mark.parametrize("marker", ["<no_reply>", "<no_reply/>", "< no_reply />", "<NO_REPLY>"])
async def test_no_reply_marker_agent_path_suppresses_reply_without_toggle(
    db_session, mock_message_channel, mock_ai, cai_haoran_answers, monkeypatch, marker
):
    """Agent 输出 <no_reply>（含 <no_reply/> 等格式变体）时：不发送、不入库、不进待审核。"""
    from app.services.agent.graph import AgentReplyResult
    import app.services.ranren_ai.engine as engine_mod

    case_id, group_id = await _setup_case_with_intake(db_session, cai_haoran_answers)
    engine = RanrenAIEngine(mock_message_channel)

    async def _fake_agent(**kwargs):
        return AgentReplyResult(reply=marker)

    monkeypatch.setattr(engine_mod, "generate_agent_reply", _fake_agent)

    before = await _count_outbound_bot(db_session, case_id)
    await engine._reply_with_ai(
        ChannelMessage(channel="qiwe", chat_id=group_id, chat_type="group", text="[OK]", message_id="m-ok-1"),
        service_case_id=case_id,
        analysis={},
        trigger_id=None,
    )
    after = await _count_outbound_bot(db_session, case_id)
    assert after == before, f"含 {marker} 标记的回复不应产生任何出站消息"


@pytest.mark.asyncio
async def test_agent_data_unavailable_records_safe_reply(
    db_session, mock_message_channel, mock_ai, cai_haoran_answers, monkeypatch
):
    """Agent 数据工具不可用时发送固定安全文案，并记录可审计标记。"""
    from app.services.agent.graph import AgentDataUnavailableError
    import app.services.ranren_ai.engine as engine_mod

    case_id, group_id = await _setup_case_with_intake(db_session, cai_haoran_answers)
    engine = RanrenAIEngine(mock_message_channel)

    async def _fake_agent(**kwargs):
        raise AgentDataUnavailableError("暂时没查到准确记录，我先不乱说，交给班主任确认一下。")

    monkeypatch.setattr(engine_mod, "generate_agent_reply", _fake_agent)

    before = await _count_outbound_bot(db_session, case_id)
    await engine._reply_with_ai(
        ChannelMessage(channel="qiwe", chat_id=group_id, chat_type="group", text="我现在第几天？", message_id="m-data-1"),
        service_case_id=case_id,
        analysis={},
        trigger_id=None,
    )
    after = await _count_outbound_bot(db_session, case_id)
    assert after == before + 1
    msg = await db_session.scalar(
        select(Message)
        .where(Message.service_case_id == case_id, Message.direction == MessageDirection.OUTBOUND)
        .order_by(Message.id.desc())
        .limit(1)
    )
    assert msg is not None
    # 数据查不到时除固定安全标记外，还结构化留痕信息缺口(第2点)：type + 触发本轮的用户原问作 topic。
    assert msg.analysis_json == {
        "agent": {
            "enabled": True,
            "data_unavailable": True,
            "info_gap": {"type": "data_unavailable", "query": "用户：我现在第几天？"},
        }
    }


@pytest.mark.asyncio
async def test_normal_reply_still_sends_without_marker(
    db_session, mock_message_channel, mock_ai, cai_haoran_answers, monkeypatch
):
    """回归保护：正常回复(不含标记)仍照常产生出站消息，避免误伤。"""
    from app.services.agent.graph import AgentReplyResult
    import app.services.ranren_ai.engine as engine_mod

    case_id, group_id = await _setup_case_with_intake(db_session, cai_haoran_answers)
    engine = RanrenAIEngine(mock_message_channel)

    async def _fake_agent(**kwargs):
        return AgentReplyResult(reply="行，那就坚定走。有问题随时喊我。")

    monkeypatch.setattr(engine_mod, "generate_agent_reply", _fake_agent)

    before = await _count_outbound_bot(db_session, case_id)
    await engine._reply_with_ai(
        ChannelMessage(channel="qiwe", chat_id=group_id, chat_type="group", text="收到", message_id="m-ok-3"),
        service_case_id=case_id,
        analysis={},
        trigger_id=None,
    )
    after = await _count_outbound_bot(db_session, case_id)
    assert after == before + 1, "正常回复应正常产生出站消息"


@pytest.mark.asyncio
async def test_agent_error_is_recorded_on_inbound_without_outbound(
    db_session, mock_message_channel, mock_ai, cai_haoran_answers, monkeypatch
):
    # AI 生成失败：仍不发送、不落出站，但要在触发的入站消息上持久化可审计标记。
    import app.services.ranren_ai.engine as engine_mod

    case_id, group_id = await _setup_case_with_intake(db_session, cai_haoran_answers)
    engine = RanrenAIEngine(mock_message_channel)
    inbound = Message(
        service_case_id=case_id,
        group_id=group_id,
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.USER,
        message_type="text",
        content="收到",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
        analysis_json={"existing_fact": "kept"},
    )
    db_session.add(inbound)
    await db_session.flush()

    before = await db_session.scalar(
        select(func.count(Message.id)).where(
            Message.service_case_id == case_id,
            Message.direction == MessageDirection.OUTBOUND,
        )
    )

    async def _fake_agent(**kwargs):
        raise RuntimeError("agent exploded")

    monkeypatch.setattr(engine_mod, "generate_agent_reply", _fake_agent)

    await engine._reply_with_ai(
        ChannelMessage(channel="qiwe", chat_id=group_id, chat_type="group", text="收到", message_id="m-err-1"),
        service_case_id=case_id,
        analysis={},
        trigger_id=inbound.id,
    )

    after = await db_session.scalar(
        select(func.count(Message.id)).where(
            Message.service_case_id == case_id,
            Message.direction == MessageDirection.OUTBOUND,
        )
    )
    assert after == before  # 没有新增任何出站记录
    await db_session.refresh(inbound)
    assert inbound.analysis_json["existing_fact"] == "kept"
    failure = inbound.analysis_json["agent_reply_failure"]
    assert failure["stage"] == "generation"
    assert failure["error_type"] == "RuntimeError"
    assert failure["trigger_message_id"] == inbound.id
    assert datetime.fromisoformat(failure["failed_at"]).tzinfo is not None


@pytest.mark.asyncio
async def test_recent_chat_history_includes_document_delivery(db_session, mock_message_channel, cai_haoran_answers):
    case_id, group_id = await _setup_case_with_intake(db_session, cai_haoran_answers)
    db_session.add(
        Message(
            service_case_id=case_id,
            group_id=group_id,
            direction=MessageDirection.INBOUND,
            sender_type=MessageSenderType.USER,
            message_type="text",
            content="收到",
            purpose=MessagePurpose.CHAT,
            status=MessageStatus.RECEIVED,
        )
    )
    db_session.add(
        Message(
            service_case_id=case_id,
            group_id=group_id,
            direction=MessageDirection.OUTBOUND,
            sender_type=MessageSenderType.BOT,
            message_type="markdown",
            content="两份专属文件已生成，请查收：1.《个人能量重启处方单》 2.《28 天执行标准与打卡要求》",
            purpose=MessagePurpose.DOCUMENT_DELIVERY,
            status=MessageStatus.SENT,
        )
    )
    await db_session.flush()

    engine = RanrenAIEngine(mock_message_channel)
    lines = await engine._recent_user_messages(db_session, case_id)

    assert any("助教: 两份专属文件已生成，请查收" in line for line in lines)
    assert not any("营养师:" in line for line in lines)
    assert any("用户: 收到" in line for line in lines)


@pytest.mark.asyncio
async def test_recent_chat_history_keeps_latest_twenty_with_length_guard(
    db_session, mock_message_channel, cai_haoran_answers
):
    case_id, group_id = await _setup_case_with_intake(db_session, cai_haoran_answers)
    start = datetime(2026, 7, 16, 1, 0, tzinfo=timezone.utc)
    for index in range(22):
        db_session.add(
            Message(
                service_case_id=case_id,
                group_id=group_id,
                direction=MessageDirection.INBOUND,
                sender_type=MessageSenderType.USER,
                message_type="text",
                content=f"history-{index:02d}-" + "x" * 30,
                purpose=MessagePurpose.CHAT,
                status=MessageStatus.RECEIVED,
                created_at=start.replace(minute=index),
            )
        )
    await db_session.flush()

    engine = RanrenAIEngine(mock_message_channel)
    lines = await engine._recent_user_messages(db_session, case_id)
    guarded = await engine._recent_user_messages(db_session, case_id, max_chars=100)

    assert len(lines) == 20
    assert "history-02" in lines[0]
    assert "history-21" in lines[-1]
    assert guarded
    assert "history-21" in guarded[-1]
    assert "history-20" not in guarded[0]
    assert sum(len(line) for line in guarded) <= 100


def _age_case() -> ServiceCase:
    return ServiceCase(case_no="TEST-AGE", group_id="g", questionnaire_token="t")


def test_mark_intake_age_risk_elderly_sets_high():
    case = _age_case()
    fields = [{"key": "age_k", "title": "您的年龄（岁）", "type": "SIMPLE"}]
    mark_intake_age_risk(case, fields, {"age_k": "72"})
    assert case.risk_level == "high"
    assert any((f or {}).get("code") == "elderly_age" for f in (case.risk_flags or []))


def test_mark_intake_age_risk_normal_age_untouched():
    case = _age_case()
    fields = [{"key": "age_k", "title": "您的年龄（岁）", "type": "SIMPLE"}]
    mark_intake_age_risk(case, fields, {"age_k": "30"})
    assert case.risk_level != "high"
    assert not any((f or {}).get("code") == "elderly_age" for f in (case.risk_flags or []))


def test_intervention_prompt_treats_stopped_period_alone_as_normal_context():
    prompt_source = inspect.getsource(AIService.extract_intervention_insight)

    assert "用户仅说月经推迟、没来月经或停经" in prompt_source
    assert "不得推断为怀孕" in prompt_source


def test_effective_service_day_by_plan_date():
    tz = ZoneInfo("Asia/Shanghai")
    now = datetime(2026, 6, 20, 10, 0, tzinfo=tz)
    case = ServiceCase(case_no="T-PLAN", group_id="g", questionnaire_token="t")
    # 计划开始日=当天 → 第1天
    case.service_plan_start_date = date(2026, 6, 20)
    assert effective_service_day(case, now, tz) == 1
    # 计划开始日=未来 → <1(未开始)
    case.service_plan_start_date = date(2026, 6, 22)
    assert effective_service_day(case, now, tz) < 1
    # 计划开始日=过去 → 第3天
    case.service_plan_start_date = date(2026, 6, 18)
    assert effective_service_day(case, now, tz) == 3
    # 未填计划日 → 回退 started_at(文件发送时刻)
    case.service_plan_start_date = None
    case.started_at = datetime(2026, 6, 19, 0, 0, tzinfo=timezone.utc)
    assert effective_service_day(case, now, tz) == 2
    # 计划日与文件都没有(仅自动建单、未开服) → <1(未开始)，不能误报第1天
    case.started_at = None
    assert effective_service_day(case, now, tz) < 1


def test_checkin_day_plan_vs_legacy_nextday():
    tz = ZoneInfo("Asia/Shanghai")
    now = datetime(2026, 6, 20, 10, 0, tzinfo=tz)
    case = ServiceCase(case_no="T-CK", group_id="g", questionnaire_token="t")
    # 有计划日=当天 → 打卡第1天
    case.service_plan_start_date = date(2026, 6, 20)
    assert checkin_day(case, now, tz) == 1
    # 计划日未来 → None(不发)
    case.service_plan_start_date = date(2026, 6, 22)
    assert checkin_day(case, now, tz) is None
    # 存量(未填计划日)：文件发送 6-18 → 次日 6-19=第1天 → 今天 6-20=第2天(沿用旧次日语义，天数号不变)
    case.service_plan_start_date = None
    case.started_at = datetime(2026, 6, 18, 0, 0, tzinfo=timezone.utc)
    assert checkin_day(case, now, tz) == 2
    # 存量文件发送当天(6-20)：次日才第1天，今天未到 → None(不发，等同旧"入群当天第0天")
    case.started_at = datetime(2026, 6, 20, 0, 0, tzinfo=timezone.utc)
    assert checkin_day(case, now, tz) is None


def _bot_mentioned_payload(bot_user_id: str = "bot-1") -> dict:
    return {
        "userId": bot_user_id,
        "msgData": {"atList": [{"userId": bot_user_id}]},
    }


@pytest.mark.asyncio
async def test_group_at_bot_silence_and_resume_commands(db_session, mock_message_channel, cai_haoran_answers):
    case_id, group_id = await _setup_case_with_intake(db_session, cai_haoran_answers)
    case = await db_session.scalar(select(ServiceCase).where(ServiceCase.id == case_id))
    assert case is not None
    case.status = ServiceCaseStatus.IN_SERVICE
    await db_session.flush()

    engine = RanrenAIEngine(mock_message_channel)
    await engine.handle_message(ChannelMessage(
        channel="qiwe",
        chat_id=group_id,
        chat_type="group",
        text="@HumanBurning.AI 退下",
        user_id="staff-1",
        user_name="运营",
        message_type="text",
        message_id="cmd-silence-1",
        raw_payload=_bot_mentioned_payload(),
    ))
    await db_session.refresh(case)
    assert case.ai_auto_reply_enabled is False

    outbound_count = await db_session.scalar(select(func.count()).select_from(Message).where(
        Message.service_case_id == case_id,
        Message.direction == MessageDirection.OUTBOUND,
    ))
    assert outbound_count == 0

    await engine.handle_message(ChannelMessage(
        channel="qiwe",
        chat_id=group_id,
        chat_type="group",
        text="在？",
        user_id="user-1",
        user_name="用户",
        message_type="text",
        message_id="normal-while-silenced",
        raw_payload={"userId": "bot-1", "msgData": {"atList": []}},
    ))
    await db_session.refresh(case)
    assert case.ai_auto_reply_enabled is False

    normal_msg = await db_session.scalar(select(Message).where(Message.wecom_message_id == "normal-while-silenced"))
    assert normal_msg is not None
    assert normal_msg.analysis_json is not None
    assert set(normal_msg.analysis_json.keys()) == {"_qiwe_meta"}

    outbound_count = await db_session.scalar(select(func.count()).select_from(Message).where(
        Message.service_case_id == case_id,
        Message.direction == MessageDirection.OUTBOUND,
    ))
    assert outbound_count == 0

    await engine.handle_message(ChannelMessage(
        channel="qiwe",
        chat_id=group_id,
        chat_type="group",
        text="@HumanBurning.AI 回来",
        user_id="staff-1",
        user_name="运营",
        message_type="text",
        message_id="cmd-resume-1",
        raw_payload=_bot_mentioned_payload(),
    ))
    await db_session.refresh(case)
    assert case.ai_auto_reply_enabled is True

    # 「回来」真正解除静默时，回执已发送并入库（走 _record_outbound）
    receipt = await db_session.scalar(select(Message).where(
        Message.service_case_id == case_id,
        Message.direction == MessageDirection.OUTBOUND,
        Message.content == "那我继续来跟进哈~",
    ))
    assert receipt is not None
    assert receipt.trigger_id is not None


@pytest.mark.asyncio
async def test_group_silence_still_parses_images_and_files(
    db_session,
    mock_message_channel,
    cai_haoran_answers,
    monkeypatch,
):
    case_id, group_id = await _setup_case_with_intake(db_session, cai_haoran_answers)
    case = await db_session.scalar(select(ServiceCase).where(ServiceCase.id == case_id))
    assert case is not None
    case.status = ServiceCaseStatus.IN_SERVICE
    await db_session.flush()

    async def fake_describe_image(self, *, message_id, image_payload, service_case_id):
        return (
            "用户上传了一张餐盘图片，能看到米饭、鸡蛋和青菜。",
            {
                "local_path": "/tmp/test-image.jpg",
                "filename": "test-image.jpg",
                "content_type": "image/jpeg",
                "parse_status": "ok",
            },
        )

    class FakeFileResponse:
        content = b"plain text"
        status_code = 200
        headers: dict[str, str] = {}

        def raise_for_status(self):
            return None

    class FakeFileClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return False

        async def get(self, *args, **kwargs):
            return FakeFileResponse()

    monkeypatch.setattr(RanrenAIEngine, "_describe_inbound_image", fake_describe_image)
    monkeypatch.setattr("app.services.ranren_ai.engine.httpx.AsyncClient", FakeFileClient)

    engine = RanrenAIEngine(mock_message_channel)
    await engine.handle_message(ChannelMessage(
        channel="qiwe",
        chat_id=group_id,
        chat_type="group",
        text="@HumanBurning.AI 退下",
        user_id="staff-1",
        user_name="运营",
        message_type="text",
        message_id="cmd-silence-parse-media",
        raw_payload=_bot_mentioned_payload(),
    ))
    await db_session.refresh(case)
    assert case.ai_auto_reply_enabled is False

    image_payload = {
        "msgType": 7,
        "msgData": {
            "fileBigHttpUrl": "https://imunion.weixin.qq.com/cgi-bin/mmae-bin/tpdownloadmedia?param=image",
        },
    }
    await engine.handle_message(ChannelMessage(
        channel="qiwe",
        chat_id=group_id,
        chat_type="group",
        text="[图片]",
        user_id="user-1",
        user_name="用户",
        message_type="image",
        message_id="image-while-silenced",
        raw_payload=image_payload,
    ))

    image_msg = await db_session.scalar(select(Message).where(Message.wecom_message_id == "image-while-silenced"))
    assert image_msg is not None
    assert image_msg.content == "用户上传了一张餐盘图片，能看到米饭、鸡蛋和青菜。"
    assert image_msg.analysis_json["image_parse_status"] == "ok"
    assert image_msg.analysis_json["image_prompt"]["content"] == "用户上传了一张餐盘图片，能看到米饭、鸡蛋和青菜。"

    await engine.handle_message(ChannelMessage(
        channel="qiwe",
        chat_id=group_id,
        chat_type="group",
        text="帮我看看\n[图片]",
        user_id="user-1",
        user_name="用户",
        message_type="text",
        message_id="text-image-while-silenced",
        raw_payload=image_payload,
    ))

    text_image_msg = await db_session.scalar(select(Message).where(Message.wecom_message_id == "text-image-while-silenced"))
    assert text_image_msg is not None
    assert text_image_msg.content == "帮我看看\n图片内容：用户上传了一张餐盘图片，能看到米饭、鸡蛋和青菜。"
    assert text_image_msg.analysis_json["image_parse_status"] == "ok"

    await engine.handle_message(ChannelMessage(
        channel="qiwe",
        chat_id=group_id,
        chat_type="group",
        text="[文件]",
        user_id="user-1",
        user_name="用户",
        message_type="file",
        message_id="file-while-silenced",
        raw_payload={
            "msgData": {
                "fileHttpUrl": "https://imunion.weixin.qq.com/cgi-bin/mmae-bin/tpdownloadmedia?param=file",
                "fileSize": 10,
                "filename": "补充记录.txt",
            }
        },
    ))

    file_msg = await db_session.scalar(select(Message).where(Message.wecom_message_id == "file-while-silenced"))
    assert file_msg is not None
    assert file_msg.content == "[文件] 补充记录.txt"
    assert file_msg.analysis_json["file_info"]["filename"] == "补充记录.txt"
    assert file_msg.analysis_json["file_info"]["parse_status"] == "unsupported"

    outbound_count = await db_session.scalar(select(func.count()).select_from(Message).where(
        Message.service_case_id == case_id,
        Message.direction == MessageDirection.OUTBOUND,
    ))
    assert outbound_count == 0


@pytest.mark.asyncio
async def test_group_resume_without_prior_silence_sends_no_receipt(db_session, mock_message_channel, cai_haoran_answers):
    case_id, group_id = await _setup_case_with_intake(db_session, cai_haoran_answers)
    case = await db_session.scalar(select(ServiceCase).where(ServiceCase.id == case_id))
    assert case is not None
    case.status = ServiceCaseStatus.IN_SERVICE
    await db_session.flush()

    # 本就在岗(未静默)，有人@机器人说“回来” → 不应冒出回执
    engine = RanrenAIEngine(mock_message_channel)
    await engine.handle_message(ChannelMessage(
        channel="qiwe",
        chat_id=group_id,
        chat_type="group",
        text="@HumanBurning.AI 回来",
        user_id="staff-1",
        user_name="运营",
        message_type="text",
        message_id="resume-no-prior-silence",
        raw_payload=_bot_mentioned_payload(),
    ))
    await db_session.refresh(case)
    assert case.ai_auto_reply_enabled is True

    outbound_count = await db_session.scalar(select(func.count()).select_from(Message).where(
        Message.service_case_id == case_id,
        Message.direction == MessageDirection.OUTBOUND,
    ))
    assert outbound_count == 0
    message = ChannelMessage(
        channel="qiwe",
        chat_id="group-1",
        chat_type="group",
        text="退下",
        user_id="user-1",
        user_name="用户",
        message_type="text",
        message_id="not-at-command",
        raw_payload={"userId": "bot-1", "msgData": {"atList": []}},
    )

    assert _group_bot_control_command(message) is None


def _staff_ctx_msg(case_id, group_id, direction, sender, content):
    return Message(
        service_case_id=case_id,
        group_id=group_id,
        direction=direction,
        sender_type=sender,
        message_type="text",
        content=content,
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
    )


@pytest.mark.asyncio
async def test_staff_query_recovered_from_prior_staff_message(db_session, mock_message_channel, cai_haoran_answers):
    """班主任把「问题」和「@机器人」拆两条发：回溯紧邻的班主任发言拼成问题。"""
    case_id, group_id = await _setup_case_with_intake(db_session, cai_haoran_answers)

    # 出站(前文已回应过) → 班主任问题 → 班主任只@(trigger 之前一条)
    db_session.add(_staff_ctx_msg(case_id, group_id, MessageDirection.OUTBOUND, MessageSenderType.BOT, "巴黎水很理想"))
    await db_session.flush()
    q = _staff_ctx_msg(case_id, group_id, MessageDirection.INBOUND, MessageSenderType.STAFF, "元气森林 没有它俩好吗 为什么")
    db_session.add(q)
    await db_session.flush()
    at_only = _staff_ctx_msg(case_id, group_id, MessageDirection.INBOUND, MessageSenderType.STAFF, "@郭渊")
    db_session.add(at_only)
    await db_session.flush()

    engine = RanrenAIEngine(mock_message_channel)
    recovered = await engine._staff_query_from_context(service_case_id=case_id, before_message_id=at_only.id)
    assert recovered == "元气森林 没有它俩好吗 为什么"


@pytest.mark.asyncio
async def test_staff_query_joins_consecutive_staff_and_stops_at_outbound(db_session, mock_message_channel, cai_haoran_answers):
    """回溯合并连续班主任发言，遇到出站消息即停（不越过已回应的历史）。"""
    case_id, group_id = await _setup_case_with_intake(db_session, cai_haoran_answers)

    db_session.add(_staff_ctx_msg(case_id, group_id, MessageDirection.OUTBOUND, MessageSenderType.BOT, "上一轮回复"))
    await db_session.flush()
    db_session.add(_staff_ctx_msg(case_id, group_id, MessageDirection.INBOUND, MessageSenderType.STAFF, "气泡水哪款好"))
    await db_session.flush()
    db_session.add(_staff_ctx_msg(case_id, group_id, MessageDirection.INBOUND, MessageSenderType.STAFF, "元气森林可以吗"))
    await db_session.flush()
    at_only = _staff_ctx_msg(case_id, group_id, MessageDirection.INBOUND, MessageSenderType.STAFF, "@郭渊")
    db_session.add(at_only)
    await db_session.flush()

    engine = RanrenAIEngine(mock_message_channel)
    recovered = await engine._staff_query_from_context(service_case_id=case_id, before_message_id=at_only.id)
    assert recovered == "气泡水哪款好\n元气森林可以吗"


@pytest.mark.asyncio
async def test_staff_query_returns_none_when_prior_is_user_or_empty(db_session, mock_message_channel, cai_haoran_answers):
    """@之前紧邻是用户发言（或无可承接发言）→ 不回溯，维持「班主任未提问不打扰」。"""
    case_id, group_id = await _setup_case_with_intake(db_session, cai_haoran_answers)

    db_session.add(_staff_ctx_msg(case_id, group_id, MessageDirection.INBOUND, MessageSenderType.USER, "用户随口一句"))
    await db_session.flush()
    at_only = _staff_ctx_msg(case_id, group_id, MessageDirection.INBOUND, MessageSenderType.STAFF, "@郭渊")
    db_session.add(at_only)
    await db_session.flush()

    engine = RanrenAIEngine(mock_message_channel)
    recovered = await engine._staff_query_from_context(service_case_id=case_id, before_message_id=at_only.id)
    assert recovered is None
