"""档案官成效/画像收编——直写真表健康自测（决策 A：档案官独家抽取）。

验证：档案官 extract 单轮一次性抽打卡+成效+画像；commit 里打卡照常 live upsert、
成效/画像经 apply_archived_outcome **直接写真表**——outcome_events(source=archivist、
message_id=anchor、当前里程碑)、user_state_profiles 出现对应 domain、outcome_stage_states
更新；影子对账已删除，archivist_shadow_outcomes 不再写入。无可上报内容时零写入。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import select

from app.models.archivist_extract_trace import ArchivistExtractTrace
from app.models.archivist_progress import ArchivistProgress
from app.models.archivist_shadow_outcome import ArchivistShadowOutcome
from app.models.checkin import Checkin
from app.models.checkin_followup_cycle import CheckinFollowupCycle
from app.models.knowledge_draft import KnowledgeDraft
from app.models.message import (
    Message,
    MessageDirection,
    MessagePurpose,
    MessageSenderType,
    MessageStatus,
)
from app.models.outcome import OutcomeEvent, OutcomeStageState
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user import User
from app.models.user_document import DocumentStatus, DocumentType, UserDocument
from app.models.user_state_profile import UserStateProfile
from app.services.archivist.graph import run_archivist
from app.services.profile_memory_service import load_profile_memory_context


class FakeToolCallingModel:
    """注入档案官单轮抽取器的假模型：bind_tools 返回自身，ainvoke 顺序吐预设 AIMessage。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.bound_tools = []

    def bind_tools(self, tools):
        self.bound_tools = list(tools)
        return self

    async def ainvoke(self, messages):
        self.calls.append(list(messages))
        if not self.responses:
            return AIMessage(content="")
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_archivist_maps_normal_chat_to_delivered_checklist_item(db_session):
    """不是回复机器人提醒的普通聊天，也必须进入执行单对应题目并恢复追问资格。"""
    case, msgs, progress = await _setup_case_with_messages(db_session)
    document = UserDocument(
        service_case_id=case.id,
        document_type=DocumentType.DAY28_EXECUTION_STANDARD,
        title="28天执行标准与打卡要求",
        content_md="test",
        content_json={
            "blocks": [
                {"key": "daily_checkin", "items": [
                    "早C/午B/晚A是否全部完成",
                    "今天是否至少吃到2拳蔬菜",
                    "今天是否完成计划内快走",
                    "睡眠是否按计划收口",
                    "情绪稳定度评分",
                ]}
            ]
        },
        status=DocumentStatus.SENT,
        sent_at=datetime.now(timezone.utc),
    )
    db_session.add(document)
    normal_chat = Message(
        service_case_id=case.id,
        group_id=case.group_id,
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.USER,
        message_type="text",
        content="今天蔬菜吃够两拳了。",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
    )
    db_session.add(normal_chat)
    await db_session.flush()
    target_date = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    item_id = f"document:{document.id}:2"
    fake = FakeToolCallingModel([
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "report_checkin",
                    "args": {
                        "checklist_answers": [
                            {
                                "item_id": item_id,
                                "target_date": target_date,
                                "answer": "蔬菜吃够两拳",
                                "evidence_message_id": str(normal_chat.id),
                            }
                        ]
                    },
                    "id": "normal-checklist-answer",
                }
            ],
        )
    ])

    await run_archivist(db_session, service_case=case, progress=progress, chat_model=fake)
    await db_session.flush()

    checkin = await db_session.scalar(
        select(Checkin).where(
            Checkin.service_case_id == case.id,
            Checkin.checkin_date == datetime.fromisoformat(target_date).date(),
        )
    )
    assert checkin is not None
    assert checkin.parsed_json["custom_checkin"][item_id]["answer"] == "蔬菜吃够两拳"
    assert checkin.parsed_json["custom_checkin"][item_id]["question"] == "今天是否至少吃到2拳蔬菜"
    assert checkin.parsed_json["custom_checkin"][item_id]["source"] == "chat"
    assert "<daily_checklist_context>" in fake.calls[0][1].content


async def _setup_case_with_messages(db_session):
    # Shared developer test DB can lag migration head; production applies this table.
    connection = await db_session.connection()
    await connection.run_sync(
        lambda sync_connection: CheckinFollowupCycle.__table__.create(
            sync_connection, checkfirst=True
        )
    )
    # 抽取观测留痕表（R1）：commit 节点凡有 emit/丢弃就写，故所有档案官测试都需它存在。
    await connection.run_sync(
        lambda sync_connection: ArchivistExtractTrace.__table__.create(
            sync_connection, checkfirst=True
        )
    )
    user = User(name="档案官直写测试用户")
    db_session.add(user)
    await db_session.flush()
    case = ServiceCase(
        case_no=f"TEST-ARCHV-{uuid4().hex[:6].upper()}",
        user_id=user.id,
        group_id=f"archv-{uuid4().hex[:8]}",
        questionnaire_token=uuid4().hex,
        status=ServiceCaseStatus.IN_SERVICE,
        started_at=datetime.now(timezone.utc) - timedelta(days=6),  # service_day 7 → 里程碑 day7
    )
    db_session.add(case)
    await db_session.flush()

    msgs = []
    for content in ("今天早C喝了，体重168斤", "这周瘦了2斤，睡眠也变好了"):
        m = Message(
            service_case_id=case.id,
            group_id=case.group_id,
            direction=MessageDirection.INBOUND,
            sender_type=MessageSenderType.USER,
            message_type="text",
            content=content,
            purpose=MessagePurpose.CHAT,
            status=MessageStatus.RECEIVED,
        )
        db_session.add(m)
        msgs.append(m)
    await db_session.flush()

    progress = ArchivistProgress(service_case_id=case.id, last_archived_message_id=0)
    db_session.add(progress)
    await db_session.flush()
    return case, msgs, progress


@pytest.mark.asyncio
async def test_archivist_writes_outcome_and_profile_to_real_tables(db_session):
    case, msgs, progress = await _setup_case_with_messages(db_session)

    # 假模型：两条消息同一自然日 → 一次 ainvoke，emit 打卡+成效+画像 report。
    fake = FakeToolCallingModel([
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "report_checkin",
                    "args": {"weight": "168斤", "product_schedule": {"morning_c": "已喝"}},
                    "id": "c1",
                },
                {
                    "name": "report_outcome",  # day7 白名单内
                    "args": {"field_key": "weight_change", "event_type": "improved", "value": "瘦了2斤", "evidence_text": "这周瘦了2斤", "confidence": 0.9},
                    "id": "o1",
                },
                {
                    "name": "report_outcome",  # day7 白名单内
                    "args": {"field_key": "sleep_change", "event_type": "improved", "value": "睡眠变好", "evidence_text": "睡眠也变好了", "confidence": 0.8},
                    "id": "o2",
                },
                {
                    "name": "report_outcome",  # 非 day7 白名单字段 → 被 _normalize_events 丢弃
                    "args": {"field_key": "mood_change", "event_type": "improved", "value": "心情好", "evidence_text": "变好了", "confidence": 0.9},
                    "id": "o3",
                },
                {
                    "name": "report_profile_fact",  # confidence>=0.65 才落
                    "args": {
                        "domain": "lifestyle_habits",
                        "signal_type": "supplement_habit",
                        "value": "正在吃鱼油",
                        "supplement_name": "鱼油",
                        "habit_status": "using",
                        "evidence_text": "我一直在吃鱼油",
                        "confidence": 0.9,
                    },
                    "id": "p1",
                },
                {
                    "name": "report_state_fact",
                    "args": {"state_type": "current_obstacle", "value": "晚上经常忘记执行", "evidence_text": "晚上总忘了", "confidence": 0.86},
                    "id": "s1",
                },
            ],
        )
    ])

    result = await run_archivist(db_session, service_case=case, progress=progress, chat_model=fake)
    await db_session.flush()

    # 当前状态是同一次 extract 调用里的新 report-only 工具，不另起一次 LLM。
    assert len(fake.calls) == 1
    assert "report_state_fact" in {tool.name for tool in fake.bound_tools}

    # 1) 打卡照常 live upsert（Agent report_checkin 落库）。
    checkin = await db_session.scalar(
        select(Checkin).where(Checkin.service_case_id == case.id, Checkin.deleted_at.is_(None))
    )
    assert checkin is not None
    assert (checkin.parsed_json or {}).get("weight") == "168斤"

    # 2) 成效直写真表：两条合法事件落库，source=archivist、message_id=anchor(最后一条)、里程碑 day7；
    #    非白名单 mood_change 被丢弃。
    events = list(await db_session.scalars(
        select(OutcomeEvent).where(OutcomeEvent.service_case_id == case.id)
    ))
    assert {e.field_key for e in events} == {"weight_change", "sleep_change"}
    assert all(e.source == "archivist" for e in events)
    assert all(e.milestone == "day7" for e in events)
    assert all(e.message_id == msgs[-1].id for e in events)
    assert result.outcome_written == 2

    # 3) 画像投影：user_state_profiles 出现成效 domain（weight_change→outcome_changes）+ 画像信号 domain。
    profile = await db_session.scalar(
        select(UserStateProfile).where(UserStateProfile.service_case_id == case.id)
    )
    assert profile is not None
    domains = profile.state_json.get("domains", {})
    assert domains.get("outcome_changes", {}).get("current")
    assert domains.get("lifestyle_habits", {}).get("current")
    supplement_fact = domains["lifestyle_habits"]["current"]["value"]["supplement_habit"][0]
    assert supplement_fact["value"] == "正在使用鱼油"
    assert supplement_fact["supplement_name"] == "鱼油"
    assert supplement_fact["habit_status"] == "using"
    memory = await load_profile_memory_context(db_session, service_case_id=case.id)
    assert any("正在使用鱼油" in item for item in memory.habits)
    state_fact = domains["service_state"]["current"]["value"]["current_obstacle"]
    assert state_fact["value"] == "晚上经常忘记执行"
    assert state_fact["evidence"] == "晚上总忘了"
    assert state_fact["source"] == "archivist_state_signal"
    assert result.profile_written == 1
    assert result.state_written == 1

    # 4) 阶段状态收集字段更新。
    state = await db_session.scalar(
        select(OutcomeStageState).where(
            OutcomeStageState.service_case_id == case.id,
            OutcomeStageState.milestone == "day7",
        )
    )
    assert state is not None
    assert "weight_change" in (state.collected_fields or {})

    # 5) 影子对账已删除：archivist_shadow_outcomes 不再写入。
    shadow = await db_session.scalar(
        select(ArchivistShadowOutcome).where(ArchivistShadowOutcome.service_case_id == case.id)
    )
    assert shadow is None


@pytest.mark.asyncio
async def test_archivist_no_reportable_writes_nothing(db_session):
    case, msgs, progress = await _setup_case_with_messages(db_session)

    # 假模型不 emit 任何工具调用（无可上报内容）。
    fake = FakeToolCallingModel([AIMessage(content="", tool_calls=[])])
    result = await run_archivist(db_session, service_case=case, progress=progress, chat_model=fake)
    await db_session.flush()

    assert result.outcome_written == 0
    assert result.profile_written == 0
    events = list(await db_session.scalars(
        select(OutcomeEvent).where(OutcomeEvent.service_case_id == case.id)
    ))
    assert events == []
    shadow = await db_session.scalar(
        select(ArchivistShadowOutcome).where(ArchivistShadowOutcome.service_case_id == case.id)
    )
    assert shadow is None
    # 水位线仍推进（避免下轮重复处理）。
    assert progress.last_archived_message_id == msgs[-1].id


@pytest.mark.asyncio
async def test_archivist_routes_personal_correction_and_only_records_general_correction(
    db_session,
):
    case, user_msgs, progress = await _setup_case_with_messages(db_session)
    bot = Message(
        service_case_id=case.id,
        group_id=case.group_id,
        direction=MessageDirection.OUTBOUND,
        sender_type=MessageSenderType.BOT,
        message_type="text",
        content="每天喝三次就可以。",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.SENT,
        trigger_id=user_msgs[-1].id,
    )
    db_session.add(bot)
    await db_session.flush()
    archived_marker = Message(
        service_case_id=case.id,
        group_id=case.group_id,
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.USER,
        message_type="text",
        content="这一条已经被上一轮档案官处理。",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
    )
    db_session.add(archived_marker)
    await db_session.flush()
    progress.last_archived_message_id = archived_marker.id
    staff = Message(
        service_case_id=case.id,
        group_id=case.group_id,
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.STAFF,
        message_type="text",
        content="她是个性化安排，每天五次。以后回答先说明这是她的特殊安排。",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
    )
    db_session.add(staff)
    await db_session.flush()

    fake = FakeToolCallingModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "report_correction",
                        "args": {
                            "scope": "personal",
                            "summary": "当前用户每天按个性化安排使用五次",
                            "applicability": "仅当前用户的产品使用安排",
                            "bot_message_id": bot.id,
                            "staff_message_id": staff.id,
                            "confidence": 0.95,
                        },
                        "id": "personal",
                    },
                    {
                        "name": "report_correction",
                        "args": {
                            "scope": "general",
                            "summary": "产品使用次数需先核对个性化安排",
                            "applicability": "用户询问产品每天使用次数",
                            "bot_message_id": bot.id,
                            "staff_message_id": staff.id,
                            "confidence": 0.9,
                        },
                        "id": "knowledge",
                    },
                ],
            )
        ]
    )

    result = await run_archivist(
        db_session,
        service_case=case,
        progress=progress,
        chat_model=fake,
    )
    await db_session.flush()

    assert {tool.name for tool in fake.bound_tools} >= {"report_correction"}
    prompt = fake.calls[0][-1].content
    assert f"[{bot.id}] 助教" in prompt
    assert f"[{staff.id}] 班主任" in prompt
    assert result.personal_corrections_written == 1
    assert result.corrections_recorded == 2
    assert result.corrections_skipped == 0
    assert progress.last_archived_message_id == staff.id

    correction_tool = next(
        tool for tool in fake.bound_tools if tool.name == "report_correction"
    )
    correction_schema = correction_tool.args_schema.model_json_schema()
    assert correction_schema["properties"]["scope"]["enum"] == ["personal", "general"]
    assert {
        "question",
        "corrected_answer",
        "user_message_id",
        "evidence_text",
    }.isdisjoint(correction_schema["properties"])

    memory = await load_profile_memory_context(db_session, service_case_id=case.id)
    rendered = memory.render_for_agent()
    assert "当前用户每天按个性化安排使用五次" in rendered

    drafts = [
        draft
        for draft in await db_session.scalars(select(KnowledgeDraft))
        if (draft.content_json or {}).get("service_case_id") == case.id
    ]
    assert drafts == []
    traces = list(
        await db_session.scalars(
            select(ArchivistExtractTrace).where(
                ArchivistExtractTrace.service_case_id == case.id
            )
        )
    )
    reported_scopes = {
        call["args"]["scope"]
        for trace in traces
        for call in trace.emitted_json
        if call.get("name") == "report_correction"
    }
    assert reported_scopes == {"personal", "general"}


@pytest.mark.asyncio
async def test_archivist_outcome_write_failure_does_not_advance_watermark(
    db_session, monkeypatch
):
    """成效/画像写库失败时：run_archivist 抛错、水位线**不推进**，防止那批信号随水位前移永久丢失。

    这是可靠性核心保证——trigger 层据此回滚事务、不 commit，下轮重跑（幂等保证安全）。
    """
    case, msgs, progress = await _setup_case_with_messages(db_session)
    original_watermark = progress.last_archived_message_id

    async def _boom(*args, **kwargs):
        raise RuntimeError("simulated outcome write failure")

    monkeypatch.setattr(
        "app.services.archivist.graph.apply_archived_outcome", _boom
    )

    fake = FakeToolCallingModel([
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "report_outcome",
                    "args": {
                        "field_key": "weight_change",
                        "event_type": "improved",
                        "value": "瘦了2斤",
                        "evidence_text": "这周瘦了2斤",
                        "confidence": 0.9,
                    },
                    "id": "o1",
                },
            ],
        )
    ])

    with pytest.raises(RuntimeError, match="simulated outcome write failure"):
        await run_archivist(
            db_session, service_case=case, progress=progress, chat_model=fake
        )

    # 水位线保持不动（未推进），成功清零逻辑也未触达。
    assert progress.last_archived_message_id == original_watermark


@pytest.mark.asyncio
async def test_archivist_model_failure_does_not_advance_watermark(db_session):
    """模型抽取失败时必须抛错且不推进水位线，让 trigger 层能回滚并重试。"""
    case, _, progress = await _setup_case_with_messages(db_session)
    original_watermark = progress.last_archived_message_id

    class FailingToolCallingModel:
        def bind_tools(self, tools):
            return self

        async def ainvoke(self, messages):
            raise TimeoutError("simulated model timeout")

    with pytest.raises(TimeoutError, match="simulated model timeout"):
        await run_archivist(
            db_session,
            service_case=case,
            progress=progress,
            chat_model=FailingToolCallingModel(),
        )

    assert progress.last_archived_message_id == original_watermark


@pytest.mark.asyncio
async def test_archivist_reports_faq_candidate_into_qa_draft(db_session):
    """FAQ 折进档案官：delta 抽取产出 report_faq_candidate → result.faq_candidates →
    create_qa_drafts_from_archivist 转成待审核 qa 草稿（复用 knowledge_evolution 管线）。"""
    from app.services.knowledge_evolution import create_qa_drafts_from_archivist

    case, msgs, progress = await _setup_case_with_messages(db_session)
    staff = Message(
        service_case_id=case.id,
        group_id=case.group_id,
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.STAFF,
        message_type="text",
        content="土豆可以当主食，今天不额外吃米饭就行。",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
    )
    db_session.add(staff)
    await db_session.flush()
    fake = FakeToolCallingModel([
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "report_faq_candidate",
                    "args": {
                        "scope": "general",
                        "question": "减肥期间吃了土豆导致体重上升怎么调整？",
                        "answer": "先别急，一天数字不代表真实脂肪变化。今天不额外吃米饭即可，先菜再肉最后主食。昨晚睡得怎么样？",
                        "category": "体重波动",
                        "tags": ["土豆", "体重波动"],
                        "evidence_text": "吃了土豆体重涨了",
                        "source_message_ids": [msgs[0].id, staff.id],
                        "staff_message_id": staff.id,
                    },
                    "id": "f1",
                },
            ],
        )
    ])

    result = await run_archivist(
        db_session, service_case=case, progress=progress, chat_model=fake
    )
    await db_session.flush()

    assert len(result.faq_candidates) == 1
    assert result.faq_candidates[0]["question"].startswith("减肥期间吃了土豆")

    created = await create_qa_drafts_from_archivist(
        db_session, service_case_id=case.id, candidates=result.faq_candidates
    )
    await db_session.flush()
    assert created == 1

    drafts = list(
        await db_session.scalars(
            select(KnowledgeDraft).where(KnowledgeDraft.draft_type == "qa")
        )
    )
    archivist_drafts = [
        d for d in drafts if (d.content_json or {}).get("scan_type") == "archivist_delta"
    ]
    assert len(archivist_drafts) == 1
    assert archivist_drafts[0].status == "pending"


@pytest.mark.asyncio
async def test_archivist_does_not_create_faq_from_user_and_bot_only(db_session):
    from app.services.knowledge_evolution import create_qa_drafts_from_archivist

    case, msgs, progress = await _setup_case_with_messages(db_session)
    fake = FakeToolCallingModel([
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "report_faq_candidate",
                    "args": {
                        "scope": "general",
                        "question": "减肥期间能吃土豆吗？",
                        "answer": "可以，控制总量即可。",
                        "category": "饮食执行",
                        "evidence_text": "用户问减肥期间能不能吃土豆。",
                        "source_message_ids": [msgs[0].id],
                    },
                    "id": "bot-only-faq",
                },
            ],
        )
    ])

    result = await run_archivist(
        db_session, service_case=case, progress=progress, chat_model=fake
    )
    created = await create_qa_drafts_from_archivist(
        db_session, service_case_id=case.id, candidates=result.faq_candidates
    )
    await db_session.flush()

    assert created == 0
    drafts = [
        draft
        for draft in await db_session.scalars(select(KnowledgeDraft))
        if (draft.content_json or {}).get("service_case_id") == case.id
    ]
    assert drafts == []


@pytest.mark.asyncio
async def test_archivist_personal_faq_writes_staff_guidance_without_draft(db_session):
    from app.services.knowledge_evolution import create_qa_drafts_from_archivist

    case, msgs, progress = await _setup_case_with_messages(db_session)
    staff = Message(
        service_case_id=case.id,
        group_id=case.group_id,
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.STAFF,
        message_type="text",
        content="你今天晚餐按半份主食加蛋白质执行，明天再反馈。",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
    )
    db_session.add(staff)
    await db_session.flush()
    fake = FakeToolCallingModel([
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "report_faq_candidate",
                    "args": {
                        "scope": "personal",
                        "question": "我今天晚餐怎么安排？",
                        "answer": "今天晚餐按半份主食加蛋白质执行，明天再反馈。",
                        "category": "饮食执行",
                        "evidence_text": "用户询问今天晚餐怎么安排，班主任给出具体安排。",
                        "source_message_ids": [msgs[-1].id],
                        "staff_message_id": staff.id,
                    },
                    "id": "personal-faq",
                },
            ],
        )
    ])

    result = await run_archivist(
        db_session, service_case=case, progress=progress, chat_model=fake
    )
    created = await create_qa_drafts_from_archivist(
        db_session, service_case_id=case.id, candidates=result.faq_candidates
    )
    await db_session.flush()

    assert created == 0
    drafts = [
        draft
        for draft in await db_session.scalars(select(KnowledgeDraft))
        if (draft.content_json or {}).get("service_case_id") == case.id
    ]
    assert drafts == []
    memory = await load_profile_memory_context(db_session, service_case_id=case.id)
    assert "半份主食加蛋白质" in memory.render_for_agent()


@pytest.mark.asyncio
async def test_archivist_general_correction_never_creates_faq_draft(db_session):
    case, user_msgs, progress = await _setup_case_with_messages(db_session)
    bot = Message(
        service_case_id=case.id,
        group_id=case.group_id,
        direction=MessageDirection.OUTBOUND,
        sender_type=MessageSenderType.BOT,
        message_type="text",
        content="这碗是中药吗？",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.SENT,
        trigger_id=user_msgs[-1].id,
    )
    db_session.add(bot)
    await db_session.flush()
    staff = Message(
        service_case_id=case.id,
        group_id=case.group_id,
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.STAFF,
        message_type="text",
        content="这是晚A，注意不要认错。",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
    )
    db_session.add(staff)
    await db_session.flush()
    fake = FakeToolCallingModel([
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "report_correction",
                    "args": {
                        "scope": "general",
                        "summary": "深色液体是晚A",
                        "applicability": "识别产品图片",
                        "bot_message_id": bot.id,
                        "staff_message_id": staff.id,
                        "confidence": 0.95,
                    },
                    "id": "not-a-faq",
                },
            ],
        )
    ])

    result = await run_archivist(
        db_session, service_case=case, progress=progress, chat_model=fake
    )
    await db_session.flush()

    assert result.corrections_recorded == 1
    assert result.personal_corrections_written == 0
    drafts = [
        draft
        for draft in await db_session.scalars(select(KnowledgeDraft))
        if (draft.content_json or {}).get("service_case_id") == case.id
    ]
    assert drafts == []


@pytest.mark.asyncio
async def test_archivist_extract_trace_records_kept_and_dropped(db_session):
    """R1 观测性：抽取留痕记 LLM 原始 emit、存活项、越界丢弃项及原因。"""
    case, msgs, progress = await _setup_case_with_messages(db_session)
    document = UserDocument(
        service_case_id=case.id,
        document_type=DocumentType.DAY28_EXECUTION_STANDARD,
        title="28天执行标准与打卡要求",
        content_md="test",
        content_json={
            "blocks": [
                {"key": "daily_checkin", "items": [
                    "早C/午B/晚A是否全部完成",
                    "今天是否至少吃到2拳蔬菜",
                    "今天是否完成计划内快走",
                    "睡眠是否按计划收口",
                    "情绪稳定度评分",
                ]}
            ]
        },
        status=DocumentStatus.SENT,
        sent_at=datetime.now(timezone.utc),
    )
    db_session.add(document)
    normal_chat = Message(
        service_case_id=case.id,
        group_id=case.group_id,
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.USER,
        message_type="text",
        content="今天蔬菜吃够两拳了。",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
    )
    db_session.add(normal_chat)
    await db_session.flush()
    target_date = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    kept_id = f"document:{document.id}:2"       # 在已发执行单白名单内 → 存活
    dropped_id = f"document:{document.id}:99"   # 越界 → 丢弃
    fake = FakeToolCallingModel([
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "report_checkin",
                    "args": {
                        "checklist_answers": [
                            {
                                "item_id": kept_id,
                                "target_date": target_date,
                                "answer": "蔬菜吃够两拳",
                                "evidence_message_id": str(normal_chat.id),
                            },
                            {
                                "item_id": dropped_id,
                                "target_date": target_date,
                                "answer": "越界题目",
                                "evidence_message_id": str(normal_chat.id),
                            },
                        ]
                    },
                    "id": "checklist-with-oob",
                }
            ],
        )
    ])

    await run_archivist(db_session, service_case=case, progress=progress, chat_model=fake)
    await db_session.flush()

    traces = list(
        await db_session.scalars(
            select(ArchivistExtractTrace).where(
                ArchivistExtractTrace.service_case_id == case.id
            )
        )
    )
    assert len(traces) == 1
    trace = traces[0]
    # emitted：LLM 原始 tool_call 原样落库（含被丢弃那条）。
    assert len(trace.emitted_json) == 1
    assert trace.emitted_json[0]["name"] == "report_checkin"
    emitted_ids = {
        a.get("item_id")
        for a in trace.emitted_json[0]["args"]["checklist_answers"]
    }
    assert emitted_ids == {kept_id, dropped_id}
    # kept：命中白名单的项存活。
    assert kept_id in trace.kept_json["checklist_item_ids"]
    assert dropped_id not in trace.kept_json["checklist_item_ids"]
    # dropped：越界项被记，且带精确原因。
    dropped = [d for d in trace.dropped_json if d.get("item_id") == dropped_id]
    assert len(dropped) == 1
    assert dropped[0]["reason"] == "checklist_not_in_whitelist"
    assert dropped[0]["bucket"] == "checklist"


@pytest.mark.asyncio
async def test_archivist_extract_trace_skips_noop_extraction(db_session):
    """无可上报（空 tool_calls）时不产生 trace 行，避免噪声。"""
    case, msgs, progress = await _setup_case_with_messages(db_session)
    fake = FakeToolCallingModel([AIMessage(content="", tool_calls=[])])

    await run_archivist(db_session, service_case=case, progress=progress, chat_model=fake)
    await db_session.flush()

    traces = list(
        await db_session.scalars(
            select(ArchivistExtractTrace).where(
                ArchivistExtractTrace.service_case_id == case.id
            )
        )
    )
    assert traces == []
