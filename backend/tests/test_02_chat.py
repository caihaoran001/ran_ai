from __future__ import annotations

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
    _group_bot_control_command,
    _requires_health_handoff,
    _requires_immediate_handoff,
)
from app.services.channels.base import ChannelMessage
from app.services.intervention_service import (
    mark_intake_age_risk,
    merge_case_insight,
    normalize_insight,
)


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
    assert case.needs_human_intervention is False


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
    assert _requires_immediate_handoff(insight) is True


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
        "memory_scopes": ["preferences", "recent_topics", "warnings"],
        "tool_hints": ["web_search", "service_case_data"],
        "reason": "用户在聊潮玩兴趣，后续只需要兴趣和最近话题。",
    }


def test_high_risk_health_goes_immediate_handoff():
    # 健康红线(孕期/慢病等 level=high)→ ①层直接@班主任
    analysis = {
        "requires_human_intervention": True,
        "requires_immediate_safety_reply": False,
        "risk_flags": [{"code": "pregnancy", "level": "high", "reason": "孕期", "evidence": "我是孕妇", "source": "chat_message"}],
        "intervention_reasons": [{"code": "pregnancy", "level": "high", "reason": "孕期需个案", "evidence": "我是孕妇", "source": "chat_message"}],
    }
    assert _requires_immediate_handoff(analysis) is True


def test_acute_symptom_goes_immediate_handoff():
    # 急性体感(心悸 medium / 急性安全回复)→ ①层
    by_flag = {
        "requires_human_intervention": True,
        "risk_flags": [{"code": "palpitation", "level": "medium", "reason": "心悸", "evidence": "心悸", "source": "chat_message"}],
    }
    by_safety = {"requires_human_intervention": True, "requires_immediate_safety_reply": True, "risk_flags": []}
    assert _requires_immediate_handoff(by_flag) is True
    assert _requires_immediate_handoff(by_safety) is True


def test_medium_risk_not_immediate_handoff():
    # 中风险(非急性、非high、非停机投诉退款)→ 不@班主任,走待审核(②层)
    analysis = {
        "requires_human_intervention": True,
        "requires_immediate_safety_reply": False,
        "risk_flags": [{"code": "other_risk", "level": "medium", "reason": "体感不适需复核", "evidence": "有点累", "source": "chat_message"}],
        "intervention_reasons": [{"code": "other_risk", "level": "medium", "reason": "需复核", "evidence": "有点累", "source": "chat_message"}],
    }
    assert _requires_immediate_handoff(analysis) is False


def test_low_risk_not_immediate_handoff():
    # 低风险/普通(不需人工介入)→ ③层自动回复
    analysis = {"requires_human_intervention": False, "requires_immediate_safety_reply": False, "risk_flags": []}
    assert _requires_immediate_handoff(analysis) is False


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

    assert any("营养师: 两份专属文件已生成，请查收" in line for line in lines)
    assert any("用户: 收到" in line for line in lines)


@pytest.mark.asyncio
async def test_user_context_mentions_document_progress(db_session, mock_message_channel, cai_haoran_answers):
    case_id, group_id = await _setup_case_with_intake(db_session, cai_haoran_answers)
    case = await db_session.scalar(select(ServiceCase).where(ServiceCase.id == case_id))
    assert case is not None
    case.status = ServiceCaseStatus.IN_SERVICE
    db_session.add(
        UserDocument(
            service_case_id=case_id,
            document_type=DocumentType.ENERGY_RESTART_PRESCRIPTION,
            title="个人能量重启处方单",
            content_md="ok",
            content_json={},
            file_path="/tmp/a.docx",
            status=DocumentStatus.SENT,
        )
    )
    db_session.add(
        UserDocument(
            service_case_id=case_id,
            document_type=DocumentType.DAY28_EXECUTION_STANDARD,
            title="28天执行标准与打卡要求",
            content_md="ok",
            content_json={},
            file_path="/tmp/b.docx",
            status=DocumentStatus.REVIEWING,
        )
    )
    db_session.add(
        Message(
            service_case_id=case_id,
            group_id=group_id,
            direction=MessageDirection.OUTBOUND,
            sender_type=MessageSenderType.BOT,
            message_type="document",
            content="已发送文件：个人能量重启处方单.docx",
            purpose=MessagePurpose.DOCUMENT_DELIVERY,
            status=MessageStatus.SENT,
        )
    )
    await db_session.flush()

    engine = RanrenAIEngine(mock_message_channel)
    user_context = await engine._build_user_context(db_session, case)

    assert "当前服务单状态：in_service" in user_context
    assert "个人能量重启处方单：已发送" in user_context
    assert "28天执行标准与打卡要求：待审核" in user_context
    assert "最近一次交付动作：已发送文件：个人能量重启处方单.docx" in user_context


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
