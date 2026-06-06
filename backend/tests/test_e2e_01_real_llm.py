from __future__ import annotations

import asyncio
import json
import os
import secrets
from datetime import datetime, timezone, timedelta, date
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import BackgroundTasks
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.api.routes.documents import generate_case_stage_report
from app.api.routes.questionnaires import _personalized_checkin_fields, submit_questionnaire
from app.core.config import get_settings
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user_document import UserDocument, DocumentStatus, DocumentType
from app.models.checkin import Checkin
from app.models.message import Message, MessageDirection, MessageSenderType, MessageStatus
from app.models.knowledge_draft import KnowledgeDraft
from app.models.reminder import ReminderEvent
from app.models.user import User
from app.models.user_profile import UserProfile
from app.services.ai_service import AIService
from app.services.ai_service.json_utils import parse_json_object
from app.services.document_service import personalized_checkin_fields_from_document_payloads
from app.services.reminder_service import ReminderService
from app.services.checkin_questionnaires import get_checkin_questionnaire_schedule, save_checkin_questionnaire_schedule
from app.services.ranren_ai.engine import RanrenAIEngine
from app.services.channels.base import ChannelMessage
from app.schemas.questionnaire import QuestionnaireSubmit
from tests.conftest import _make_test_app
from tests.fixtures.chat_scenarios import REAL_LLM_PERSONA_JOURNEYS, build_daily_checkin, build_stage_review

# Skip this file entirely if no AI API KEY is set to prevent pipeline crashes
settings = get_settings()
_DEFAULT_E2E_RUN_ID = f"pytest-e2e-{secrets.token_hex(6)}"
NEUTRAL_PERSONA_OVERRIDES = {
    "CHECKBOX1774104416332mn0fyoni": "暂无明显困扰",
    "CHECKBOX1774104667027mn0g4239": "每天节律更稳定、白天精力更稳",
    "CHECKBOX1774104915668mn0g9dxy": "暂无明显执行卡点",
    "RADIO1774106385794mn0h4was": "提神稳定，没有明显不适",
    "RADIO1774106601945mn0h9j2z": "白天精力基本稳定",
    "CHECKBOX1774106739991mn0hchln": "忙碌时也能基本稳定",
    "RADIO1774106869788mn0hf9r2": "压力不明显，在应对能力范围内",
    "CHECKBOX1774107036716mn0hiujy": "情绪整体稳定",
    "CHECKBOX1774107333066mn0hp77x": "暂无明显睡眠困扰",
    "SIMPLE1774108071693mn0i515a": "无",
}
pytestmark = pytest.mark.skipif(
    not settings.ai_api_key,
    reason="Requires active AI_API_KEY in .env for real LLM E2E validation"
)


@pytest.fixture(autouse=True)
def mark_real_e2e_usage_as_diagnostic(monkeypatch):
    """Persist only E2E cost telemetry; all simulated customer data rolls back."""
    run_id = os.getenv("RAN_AI_DIAGNOSTIC_RUN_ID") or _DEFAULT_E2E_RUN_ID
    monkeypatch.setenv("RAN_AI_DIAGNOSTIC_RUN_ID", run_id)


@pytest.fixture(autouse=True)
def cleanup_real_e2e_documents():
    """Remove generated files after assertions so E2E leaves no business artifacts."""
    output_dir = Path(settings.document_output_dir)
    if not output_dir.is_absolute():
        output_dir = Path.cwd() / output_dir
    before = {path.resolve() for path in output_dir.rglob("*") if path.is_file()} if output_dir.exists() else set()
    yield
    if output_dir.exists():
        for path in output_dir.rglob("*"):
            if path.is_file() and path.resolve() not in before:
                path.unlink(missing_ok=True)


@pytest_asyncio.fixture
async def real_admin_client(db_session):
    """Provides a client that does NOT mock AIService to test real prompt outputs"""
    app = _make_test_app()
    
    from app.db.session import get_session
    async def override_get_session():
        yield db_session

    app.dependency_overrides[get_session] = override_get_session

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test/api",
        headers={"x-admin-api-key": "change-me-internal-api-key"},
    ) as ac:
        yield ac


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "journey",
    REAL_LLM_PERSONA_JOURNEYS,
    ids=[journey.key for journey in REAL_LLM_PERSONA_JOURNEYS],
)
async def test_real_llm_persona_journey(
    real_admin_client,
    db_session,
    mock_message_channel,
    cai_haoran_answers,
    wait_background,
    journey,
):
    """Validate one questionnaire-first 28-day journey and visible content.

    Pytest executes the ten personas as isolated transactions. This keeps the
    long-running real-model suite stable and lets later personas continue even
    when one journey exposes a failure.
    """
    print(
        f"\n🧪 [E2E-01] 启动人设旅程: {journey.user_name} / {journey.key}，"
        f"消息 {len(journey.messages)} 条，打卡 {len(journey.checkin_days)} 天",
        flush=True,
    )
    await real_admin_client.post("/settings/ai-reply-delivery-policy", json={"mode": "auto_send"})
    schedule = await get_checkin_questionnaire_schedule(db_session)
    schedule["inactive_followup"] = {
        **(schedule.get("inactive_followup") or {}),
        "enabled": True,
        "hours": 1,
    }
    await save_checkin_questionnaire_schedule(db_session, schedule)
    engine = RanrenAIEngine(mock_message_channel)

    try:
        group_id = f"test-real-persona-{journey.key}-{secrets.token_hex(4)}"
        created_case = await _create_sandbox_service_case(
            db_session,
            group_id=group_id,
            user_name=journey.user_name,
        )
        case_id = created_case.id
        questionnaire_token = created_case.questionnaire_token
        answers = {**cai_haoran_answers, **NEUTRAL_PERSONA_OVERRIDES, **journey.answer_overrides}
        answers["SIMPLE1774103358253mn0fc08d"] = journey.user_name
        answers["SIMPLE1774103480574mn0femm6"] = journey.user_name
        submission = await submit_questionnaire(
            questionnaire_token,
            QuestionnaireSubmit(answers=answers),
            BackgroundTasks(),
            template="intake",
            session=db_session,
        )
        assert submission.service_case_id == case_id
        await wait_background()

        await _assert_intake_content_contract(
            db_session,
            case_id=case_id,
            expected_name=journey.user_name,
        )

        service_case = await db_session.get(ServiceCase, case_id)
        assert service_case is not None
        service_case.status = ServiceCaseStatus.IN_SERVICE
        messages_by_day: dict[int, list] = {}
        for item in journey.messages:
            messages_by_day.setdefault(item.day, []).append(item)

        for day in range(1, 29):
            if day in {1, 7, 14, 21, 28}:
                print(
                    f"   [E2E-01] {journey.user_name} 推进到 Day {day}: "
                    f"messages={len(messages_by_day.get(day, []))}, "
                    f"checkin={'yes' if day in journey.checkin_days else 'no'}",
                    flush=True,
                )
            service_case.started_at = datetime.now(timezone.utc) - timedelta(days=day)
            if day in journey.checkin_days:
                if day in (1, 3, 7, 14, 21, 28):
                    parsed_json = build_stage_review(journey.user_name, day, journey.key)
                else:
                    parsed_json = build_daily_checkin(journey.user_name, day, journey.key)
                db_session.add(
                    Checkin(
                        service_case_id=case_id,
                        group_id=group_id,
                        user_id=service_case.user_id,
                        checkin_date=date.today() - timedelta(days=28 - day),
                        content=f"{journey.user_name} 第{day}天结构化打卡",
                        parsed_json=parsed_json,
                    )
                )
            for index, item in enumerate(messages_by_day.get(day, []), start=1):
                await engine.handle_message(
                    ChannelMessage(
                        channel="qiwe",
                        chat_id=group_id,
                        chat_type="group",
                        text=item.content,
                        message_id=f"msg-real-{journey.key}-d{day}-{index}-{secrets.token_hex(3)}",
                        user_id=f"qiwe-user-{journey.key}",
                        message_type="text",
                    )
                )
                await wait_background()
            await db_session.flush()

        if journey.expect_inactive_reminder:
            reminder_settings = settings.model_copy(
                update={
                    "inactive_followup_enabled": True,
                    "inactive_followup_hours": 1,
                    "reminder_at": "00:00",
                    "reminder_weekdays": "1,2,3,4,5,6,7",
                }
            )
            await ReminderService(settings=reminder_settings).check_inactive_followups_once()

        db_session.expire_all()
        service_case = await db_session.get(ServiceCase, case_id)
        assert service_case is not None
        inbound_messages = list(
            await db_session.scalars(
                select(Message)
                .where(
                    Message.service_case_id == case_id,
                    Message.direction == MessageDirection.INBOUND,
                )
                .order_by(Message.id.asc())
            )
        )
        assert len(inbound_messages) == len(journey.messages)
        assert all(isinstance(message.analysis_json, dict) for message in inbound_messages)
        outcome_results = [
            message.analysis_json["outcome"]
            for message in inbound_messages
            if isinstance(message.analysis_json, dict)
            and isinstance(message.analysis_json.get("outcome"), dict)
        ]
        if inbound_messages:
            assert outcome_results, "真实聊天必须至少产出一次成效/状态提取结果"
        if "risk" in journey.expected_flags:
            assert service_case.risk_level == "high"
        if "no_high_risk" in journey.expected_flags:
            assert service_case.risk_level != "high", "普通低落或执行受挫不应被升级为健康高风险"
        if "human_intervention" in journey.expected_flags:
            assert service_case.needs_human_intervention is True
        if "refund" in journey.expected_flags:
            assert service_case.refund_intent_flags
        if "renewal" in journey.expected_flags:
            assert service_case.renewal_intent_flags
        if "no_renewal" in journey.expected_flags:
            assert not service_case.renewal_intent_flags, "继续执行当前方案不应被误记为续购意愿"
        if journey.expect_inactive_reminder:
            reminder = await db_session.scalar(
                select(ReminderEvent).where(
                    ReminderEvent.service_case_id == case_id,
                    ReminderEvent.rule_id == "inactive_followup",
                )
            )
            assert reminder is not None, "沉默用户超过阈值后应触发主动提醒"
        if "human_intervention" in journey.expected_flags or "refund" in journey.expected_flags:
            assert not (
                (outcome_results[-1].get("followup_plan") or {}).get("should_ask")
            ), "触发人工介入或退款的当前轮不得继续插入成效追问"

        quality_result = await _judge_persona_journey_quality(
            db_session,
            service_case=service_case,
            journey=journey,
            inbound_messages=inbound_messages,
        )
        _assert_quality_bundle(journey, quality_result)

        print(
            f" -> [28天用户旅程验证] {journey.user_name}: "
            f"risk={service_case.risk_level}, human={service_case.needs_human_intervention}, "
            f"refund={bool(service_case.refund_intent_flags)}, renewal={bool(service_case.renewal_intent_flags)}, "
            f"messages={len(inbound_messages)}, checkins={len(journey.checkin_days)}"
        )
    finally:
        await real_admin_client.post("/settings/ai-reply-delivery-policy", json={"mode": "manual_review"})


async def _assert_intake_content_contract(
    session,
    *,
    case_id: int,
    expected_name: str,
) -> None:
    profile = await session.scalar(select(UserProfile).where(UserProfile.service_case_id == case_id))
    documents = list(
        await session.scalars(
            select(UserDocument)
            .where(
                UserDocument.service_case_id == case_id,
                UserDocument.document_type.in_(
                    [
                        DocumentType.ENERGY_RESTART_PRESCRIPTION,
                        DocumentType.DAY28_EXECUTION_STANDARD,
                    ]
                ),
            )
            .order_by(UserDocument.id.asc())
        )
    )
    # Enum storage differs across existing databases, so fall back to all intake docs.
    if len(documents) != 2:
        documents = list(
            await session.scalars(
                select(UserDocument)
                .where(UserDocument.service_case_id == case_id)
                .order_by(UserDocument.id.asc())
            )
        )[:2]
    assert profile is not None
    assert len(documents) == 2
    assert all(document.content_json and document.content_md for document in documents)
    assert expected_name in "\n".join(document.content_md for document in documents)

    execution = next(document for document in documents if document.document_type == DocumentType.DAY28_EXECUTION_STANDARD)
    expected_fields = personalized_checkin_fields_from_document_payloads(
        {"day28_execution_standard": execution.content_json or {}}
    )
    service_case = await session.get(ServiceCase, case_id)
    assert service_case is not None
    actual_fields = await _personalized_checkin_fields(session, service_case)
    assert [item["label"] for item in actual_fields] == [item["label"] for item in expected_fields]
    assert [item["type"] for item in actual_fields] == [item["type"] for item in expected_fields]

    visible_text = "\n".join(document.content_md for document in documents)
    banned_terms = ("阴虚火旺", "阳虚", "痰湿", "气血不足", "保证减重", "替代医生")
    assert not any(term in visible_text for term in banned_terms)


async def _create_sandbox_service_case(
    session,
    *,
    group_id: str,
    user_name: str,
    phone: str | None = None,
) -> ServiceCase:
    """Prepare an isolated case without committing the E2E transaction.

    Safe API coverage already validates service-case creation. Real-model E2E
    starts at the customer's questionnaire submission and must remain fully
    rollbackable, so the prerequisite case is flushed directly into its sandbox
    transaction.
    """
    user = User(name=user_name, phone=phone)
    session.add(user)
    await session.flush()
    service_case = ServiceCase(
        case_no=f"E2E-{secrets.token_hex(6).upper()}",
        user_id=user.id,
        group_id=group_id,
        questionnaire_token=secrets.token_urlsafe(24),
    )
    session.add(service_case)
    await session.flush()
    return service_case


async def _judge_persona_journey_quality(
    session,
    *,
    service_case: ServiceCase,
    journey,
    inbound_messages: list[Message],
) -> dict:
    conversation = list(
        await session.scalars(
            select(Message)
            .where(Message.service_case_id == service_case.id)
            .order_by(Message.id.asc())
        )
    )
    profile = await session.scalar(
        select(UserProfile)
        .where(UserProfile.service_case_id == service_case.id)
        .order_by(UserProfile.created_at.desc())
    )
    documents = list(
        await session.scalars(
            select(UserDocument)
            .where(UserDocument.service_case_id == service_case.id)
            .order_by(UserDocument.id.asc())
        )
    )
    checkins = list(
        await session.scalars(
            select(Checkin)
            .where(Checkin.service_case_id == service_case.id)
            .order_by(Checkin.checkin_date.asc())
        )
    )
    reminder_events = list(
        await session.scalars(
            select(ReminderEvent)
            .where(ReminderEvent.service_case_id == service_case.id)
            .order_by(ReminderEvent.created_at.asc())
        )
    )
    base_payload = {
        "persona": journey.description,
        "quality_focus": journey.quality_focus,
        "expected_flags": journey.expected_flags,
        "conversation_timeline": [
            {
                "sequence": index,
                "sender": "user" if message.direction == MessageDirection.INBOUND else "ranren_ai",
                "purpose": str(message.purpose.value if hasattr(message.purpose, "value") else message.purpose),
                "content": message.content,
                "analysis": message.analysis_json or {},
            }
            for index, message in enumerate(conversation, start=1)
        ],
        "profile_summary": profile.summary if profile else "",
        "structured_checkins": [
            {
                "date": checkin.checkin_date.isoformat(),
                "content": checkin.content,
                "parsed": checkin.parsed_json or {},
            }
            for checkin in checkins
        ],
        "delivery_documents": [
            {
                "type": str(document.document_type.value if hasattr(document.document_type, "value") else document.document_type),
                "title": document.title,
                "content": document.content_md,
                "content_json": document.content_json or {},
            }
            for document in documents[:2]
        ],
        "system_result": {
            "risk_level": service_case.risk_level,
            "needs_human_intervention": service_case.needs_human_intervention,
            "renewal_detected": bool(service_case.renewal_intent_flags),
            "refund_detected": bool(service_case.refund_intent_flags),
            "reminder_events": [
                {
                    "rule_id": reminder.rule_id,
                    "event_type": reminder.event_type.value,
                    "event_date": reminder.event_date.isoformat(),
                    "created_at": reminder.created_at.isoformat(),
                }
                for reminder in reminder_events
            ],
        },
    }

    results = await _run_quality_judge(payload=base_payload)

    print(
        f"   [内容质量综合评审] {journey.user_name}: "
        + ", ".join(
            f"{name}={result.get('scores', {})}"
            for name, result in results.items()
        ),
        flush=True,
    )
    return results


async def _run_quality_judge(
    *,
    payload: dict,
) -> dict:
    ai = AIService(settings=settings)
    try:
        raw = await ai.generate_text(
            scenario="diagnostics_e2e_quality_judge",
            temperature=0.1,
            system_prompt=(
                "你是燃AI真实E2E的综合质量评审员。你会一次性读取完整用户旅程数据，"
                "并同时评审对话、文档、风险意图、打卡成效四个维度。只输出严格 JSON。"
            ),
            user_prompt=(
                "<rubric>\n"
                "1. dialogue_quality：只评审完整对话。scores 必须包含 understanding、naturalness、tone_appropriateness、followup_timing，均为1到5分。重点看是否理解上下文、是否机械复读、是否在合适时机追问或克制追问。\n"
                "2. document_accuracy：只评审用户画像与两份交付文件。scores 必须包含 prescription_accuracy、execution_standard_operability、checkin_fields_relevance，均为1到5分。重点看是否编造事实、是否与用户情况冲突、每日行动是否可操作。\n"
                "3. risk_intent：只评审风险、人工介入、续购、退款判断。scores 必须包含 risk_classification、human_intervention、intent_detection，均为1到5分。漏判健康风险、退款、明确续购时，对应分数必须低于3。\n"
                "4. checkin_outcome：只评审结构化打卡、消息 analysis 中的 outcome、提醒事件和阶段证据。scores 必须包含 checkin_extraction、outcome_extraction、reminder_timing，均为1到5分。沉默用户没有聊天时，不得要求系统编造成效。\n"
                "失败判定只看分数：任一 scores 子项 < 3 即代表该维度不通过。violations 用于解释原因，不再额外使用 critical 或 major 数量规则。\n"
                "</rubric>\n"
                "<journey_data>\n"
                f"{json.dumps(payload, ensure_ascii=False)}\n"
                "</journey_data>\n"
                "输出严格JSON，结构固定为："
                '{"dialogue_quality":{"scores":{"understanding":1-5,"naturalness":1-5,"tone_appropriateness":1-5,"followup_timing":1-5},"violations":[{"type":"违规类型","severity":"minor|major|critical","reason":"原因"}],"reason":"简短说明"},'
                '"document_accuracy":{"scores":{"prescription_accuracy":1-5,"execution_standard_operability":1-5,"checkin_fields_relevance":1-5},"violations":[],"reason":"简短说明"},'
                '"risk_intent":{"scores":{"risk_classification":1-5,"human_intervention":1-5,"intent_detection":1-5},"violations":[],"reason":"简短说明"},'
                '"checkin_outcome":{"scores":{"checkin_extraction":1-5,"outcome_extraction":1-5,"reminder_timing":1-5},"violations":[],"reason":"简短说明"},'
                '"summary":"一句话总评"}'
            ),
        )
    finally:
        await ai.close()
    parsed = parse_json_object(raw)
    return {
        key: parsed.get(key, {})
        for key in ("dialogue_quality", "document_accuracy", "risk_intent", "checkin_outcome")
    }


def _assert_quality_bundle(journey, result: dict) -> None:
    for name, item in result.items():
        scores = item.get("scores") if isinstance(item.get("scores"), dict) else {}
        assert scores, f"{journey.user_name} {name} 未返回评分: {item}"
        assert min(float(value) for value in scores.values()) >= 3, (
            f"{journey.user_name} {name} 内容质量未达标: scores={scores}, reason={item.get('reason', '')}"
        )
    # Violations explain low scores, but pass/fail is intentionally based on
    # one rule only: every scored metric must be >= 3. This avoids contradictory
    # judge output such as high scores plus critical labels.


@pytest.mark.asyncio
async def test_real_llm_full_saga_end_to_end(
    real_admin_client,
    db_session,
    mock_message_channel,
    cai_haoran_answers,
    wait_background,
):
    """
    真枪实弹测试全链路：以一个独立的服务单为依托，逐日深度模拟 28 天真实运营环境：
    1. 真实创建服务单并提交问卷生成处方
    2. 时光加速，按天（Day 1 ~ Day 28）循环推进
    3. 每天模拟打卡，并由用户发起日常聊天，真实触发 AI 自动回复（调用真实大模型）并落库
    4. 每天结束时按群独立触发 Daily FAQ 进化扫描
    5. 在阶段节点（Day 3/7/14/21/28）触发标杆案例提炼
    6. 结营生成毕业总结 Word 文档
    7. 所有业务测试数据在测试结束后回滚，仅保留诊断成本记录。
    """
    print("\n🚀 启动大模型全链路实战测试（28天深度逐日人机对话与案例进化沙盒）...")
    suffix = secrets.token_hex(6)
    group_id = f"test-real-saga-{suffix}"
    existing_draft_ids = set(await db_session.scalars(select(KnowledgeDraft.id)))
    
    # ==========================================
    # 步骤 1: 真实创建服务单
    # ==========================================
    created_case = await _create_sandbox_service_case(
        db_session,
        group_id=group_id,
        user_name="蔡浩然",
        phone="15642876967",
    )
    case_id = created_case.id
    token = created_case.questionnaire_token
    user_id = created_case.user_id
    print(f" -> 步骤 1 完成: 成功创建服务单 {created_case.case_no}, Token={token}")

    # ==========================================
    # 步骤 2: 真实提交入营问卷，触发大模型生成处方单与执行标准
    # ==========================================
    submission = await submit_questionnaire(
        token,
        QuestionnaireSubmit(answers=cai_haoran_answers),
        BackgroundTasks(),
        template="intake",
        session=db_session,
    )
    assert submission.service_case_id == case_id
    print(" -> 步骤 2 完成: 成功提交入营问卷，正在呼叫真实大模型进行分析并生成处方...")
    
    # 真实等待后台生成处方任务完成
    await wait_background()
    print(" -> 真实大模型推理与文档生成处理完成！")

    # ==========================================
    # 步骤 3: 校验真实生成的结构化交付文件内容
    # ==========================================
    docs = list(await db_session.scalars(
        select(UserDocument).where(UserDocument.service_case_id == case_id)
    ))
    assert len(docs) == 2
    for doc in docs:
        assert doc.content_json
        assert doc.content_md
        print(f"   - 校验结构化文档成功: {doc.title} (content_json + content_md 已保存)")

    # ==========================================
    # 步骤 4: 启动 28 天逐日时光推移大模型人机互动深度模拟
    # ==========================================
    print("\n⏳ [TIME SIMULATOR] 启动 28 天逐日时光推移与大模型人机互动深度模拟...")
    
    # 临时将 AI 自动回复策略设为自动发送，以便真实测试机器人实时答复
    await real_admin_client.post("/settings/ai-reply-delivery-policy", json={"mode": "auto_send"})
    # 开启知识自动进化
    await real_admin_client.post("/settings/knowledge-evolution", json={"enabled": True})
    
    from app.services.knowledge_evolution import KnowledgeEvolutionService
    from tests.fixtures.chat_scenarios import ALL_CHAT_MESSAGES, CHECKIN_SEQUENCE
    
    service = KnowledgeEvolutionService()
    engine = RanrenAIEngine(mock_message_channel)
    sc = await db_session.get(ServiceCase, case_id)
    sc.status = ServiceCaseStatus.IN_SERVICE
    await db_session.flush()

    for d in range(1, 29):
        # 1. 模拟时光加速：调整服务单开始服务时间，使“当前天”对应为服务第 d 天
        sc.started_at = datetime.now(timezone.utc) - timedelta(days=d)
        await db_session.flush()
        
        # 2. 模拟用户每日打卡记录入库
        day_checkin = next((c for c in CHECKIN_SEQUENCE if c["day"] == d), None)
        if day_checkin:
            checkin_date = date.today() - timedelta(days=28 - d)
            db_session.add(
                Checkin(
                    service_case_id=case_id,
                    group_id=group_id,
                    user_id=user_id,
                    checkin_date=checkin_date,
                    content=f"打卡D{d}: wt{day_checkin['weight']}",
                    parsed_json={
                        "weight": day_checkin["weight"],
                        "water": day_checkin["water"],
                        "sleep": day_checkin["sleep"],
                        "mood": day_checkin["mood"],
                        "exercise": day_checkin["exercise"],
                    },
                )
            )
            await db_session.flush()

        # 3. 模拟用户在第 d 天在群内发言，并让 AI 机器人实时自动回复！
        day_messages = [m for m in ALL_CHAT_MESSAGES if m.day == d]
        for msg in day_messages:
            print(f"\n💬 [模拟 Day {d} 用户说]: \"{msg.content}\"")
            inbound = ChannelMessage(
                channel="qiwe", chat_id=group_id, chat_type="group", text=msg.content,
                message_id=f"msg-real-d{d}-{secrets.token_hex(4)}", user_id="qiwe-user-cai", message_type="text"
            )
            # 真实送入 AI 自动回复引擎处理（调用真实大模型生成回复并落库！）
            await engine.handle_message(inbound)
            await wait_background()
            
            # 读取并打印大模型生成的最新 bot 答复
            db_session.expire_all()
            bot_reply = list(await db_session.scalars(
                select(Message).where(Message.service_case_id == case_id, Message.direction == MessageDirection.OUTBOUND)
                .order_by(Message.id.desc()).limit(1)
            ))[0]
            print(f"🤖 [模拟 Day {d} AI 自动回复]:\n\"\"\"\n{bot_reply.content}\n\"\"\"")

        # 4. 每日零点触发：扫描该自然日产生的群聊进行物理群 FAQ 扫描并提炼草稿
        target_date = date.today() - timedelta(days=28 - d)
        await service.scan_daily_qa(db_session, target_date=target_date, force=True)

        # 5. 阶段性优秀案例审查窗口 (Day 3, 7, 14, 21, 28)
        if d in [3, 7, 14, 21, 28]:
            print(f"\n🚀 [时光加速] 到达第 {d} 天阶段审查窗口，真实运行优秀案例提取...")
            created, _ = await service.scan_stage_cases(db_session, stage_days=[d], limit_per_case=5, force=True)
            print(f"   - Day {d} 标杆案例扫描结果: 成功提炼了 {created} 条标杆优秀案例草稿！")

    # ==========================================
    # 步骤 5: 真实调用 API 生成结营毕业报告
    # ==========================================
    # 恢复 AI 回复策略为 manual_review 手工审核，防范后续自动回复对生产环境的潜在干扰
    await real_admin_client.post("/settings/ai-reply-delivery-policy", json={"mode": "manual_review"})
    
    print("\n -> 步骤 5: 时光快进至【结营第 28 天】，调用 API 真实通过大模型生成毕业总结报告...")
    grad_doc = await generate_case_stage_report(
        case_id,
        stage="graduation",
        session=db_session,
    )
    assert grad_doc.content_json
    assert grad_doc.content_md
    print(" -> 步骤 5 完成: 成功跨越 28 天，成功生成结营毕业报告结构化内容")

    # ==========================================
    # 步骤 6: 真实测试 FAQ/案例草稿的审核入库、向量化同步、RAG 相似度召回与案例反哺
    # ==========================================
    db_session.expire_all()
    final_drafts = list(await db_session.scalars(
        select(KnowledgeDraft)
        .where(KnowledgeDraft.id.not_in(existing_draft_ids) if existing_draft_ids else True)
        .order_by(KnowledgeDraft.created_at.desc())
    ))
    
    print("\n🚀 [步骤 6.3] 启动「AI 自进化 RAG 终极闭环」与「优秀案例库检索反哺」实战断言...")
    
    # 1. 模拟管理员审核批准一条 FAQ 草稿 (以我们产生的第一个 FAQ 草稿为例)
    qa_draft = next((d for d in final_drafts if d.draft_type == "qa"), None)
    if qa_draft:
        qa_draft_id = qa_draft.id
        qa_draft_title = qa_draft.title
        print(f" -> 模拟管理员审核批准 FAQ 草稿 #{qa_draft_id}: Title='{qa_draft_title}'")
        from app.services.knowledge_evolution import approve_draft
        await approve_draft(db_session, qa_draft)
        await db_session.flush()
        
        # 断言 1: 正式 FAQ 实体记录已产生
        from app.models.knowledge_faq import KnowledgeFAQ
        faq_records = list(await db_session.scalars(
            select(KnowledgeFAQ).where(KnowledgeFAQ.source_id == str(qa_draft_id))
        ))
        assert len(faq_records) == 1
        faq = faq_records[0]
        assert faq.status == "active"
        print(f"   [OK] 正式 FAQ 记录产生成功: faq_uid={faq.faq_uid}")

        # 断言 2: 向量数据库中成功写入了向量块，且 1536 维的 Embedding 已经真实算好入库
        from app.models.knowledge_chunk import KnowledgeChunk
        chunk = await db_session.scalar(
            select(KnowledgeChunk).where(KnowledgeChunk.chunk_uid == f"faq:{faq.id}")
        )
        assert chunk is not None
        assert chunk.embedding is not None
        assert len(chunk.embedding) in (1024, 1536)
        print(f"   [OK] RAG 向量数据库同步成功: Embedding 向量计算入库完成！")
        
        # 断言 3: 模拟真实新用户提问，验证 RAG 相似度召回成功！
        print(f" -> 模拟新群用户相似语料提问，测试向量相似度召回反哺...")
        from app.services.knowledge_index import retrieve_indexed_knowledge
        # 基于刚批准的 FAQ 的具体问题进行提问，确保 100% 命中召回，消除大模型非确定性干扰
        retrieved_context = await retrieve_indexed_knowledge(db_session, faq.question, settings=settings)
        assert faq.title in retrieved_context
        print("   [OK] RAG 向量相似度检索完美命中！新进化出炉的 FAQ 知识实时反哺闭环走通！")

    # 2. 模拟管理员审核批准优秀案例草稿，写入标杆案例向量索引
    case_draft = next((d for d in final_drafts if d.draft_type == "case"), None)
    if case_draft:
        case_draft_id = case_draft.id
        case_draft_title = case_draft.title
        print(f" -> 模拟管理员审核批准标杆优秀案例草稿 #{case_draft_id}: Title='{case_draft_title}'")
        from app.services.knowledge_evolution import approve_draft
        await approve_draft(db_session, case_draft)
        await db_session.flush()
        
        # 断言 4: 校验优秀案例向量块是否成功写入
        from app.models.knowledge_chunk import KnowledgeChunk
        chunk = await db_session.scalar(
            select(KnowledgeChunk).where(KnowledgeChunk.chunk_uid == f"case:{case_draft_id}")
        )
        assert chunk is not None
        assert chunk.embedding is not None
        assert len(chunk.embedding) in (1024, 1536)
        print(f"   [OK] 标杆优秀案例向量入库成功: Case Embedding 向量计算完成！")
        
        # 断言 5: 真实运行优秀案例库数据库语义检索，断言召回成功
        from app.services.knowledge_index import retrieve_case_context_db
        # 基于刚批准的案例具体场景问题进行提问，确保 100% 命中召回，消除大模型非确定性干扰
        retrieved_cases = await retrieve_case_context_db(
            db_session,
            case_draft.question,
            settings=settings
        )
        assert case_draft_title in retrieved_cases
        print("   [OK] 优秀标杆案例库语义检索召回成功！AI 对日常崩溃学员将能自动提供真实标杆案例关怀支持！")

    # ==========================================
    # 步骤 7: 校验最终沉淀数量；业务测试数据由 fixture 统一回滚
    # ==========================================
    print(f"\n🎉 整个大模型全链路 28 天深度逐日模拟沙盒测试 100% 成功通过！")
    print(f"   - 本次服务周期真实产生 FAQ 草稿数: {len([d for d in final_drafts if d.draft_type == 'qa'])} 条")
    print(f"   - 本次服务周期真实产生 经典案例草稿数: {len([d for d in final_drafts if d.draft_type == 'case'])} 条")
    print("   - 内容质量由确定性契约断言与十类用户旅程质量评审共同验证。")
    print("✨ [SUCCESS] 本次模拟客户业务数据将在测试结束后回滚；诊断成本记录单独保留。")
