from __future__ import annotations

import secrets
from io import BytesIO
from datetime import date, datetime, timezone

import pytest
from langchain_core.messages import AIMessage
from docx import Document
from sqlalchemy import select

from app.core.config import get_settings
from app.models.checkin import Checkin
from app.models.checkin_followup_preference import CheckinFollowupPreference
from app.models.message import Message, MessageDirection, MessagePurpose, MessageSenderType, MessageStatus
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user import User
from app.models.user_document import DocumentStatus, DocumentType, UserDocument
from app.models.user_profile import UserProfile
from app.models.user_state_profile import UserStateProfile
from app.services.agent.context_builder import build_agent_memory_context
from app.services.agent.graph import AgentReplyResult, AgentRunContext, RanrenAgentGraph
from app.services.agent.skill_registry import SkillRegistry
from app.services.agent.tools.service_case_tools import build_service_case_tools
from app.services.agent.tools.followup_preference_tools import build_followup_preference_tools
from app.services.agent.tools.web_search_tools import _normalize_current_query_year, build_web_search_tools
from app.services.channels.base import ChannelMessage
from app.services.content_policy import set_ai_reply_enabled
from app.services.profile_memory_service import load_profile_memory_context
from app.services.ranren_ai.engine import RanrenAIEngine


class FakeToolCallingModel:
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
            return AIMessage(content="默认回复")
        return self.responses.pop(0)


class FakeAsyncTool:
    def __init__(self, result):
        self.calls = []
        self.result = result

    async def ainvoke(self, args):
        self.calls.append(args)
        return self.result


class FakeSequenceTool:
    def __init__(self, results):
        self.calls = []
        self.results = list(results)

    async def ainvoke(self, args):
        self.calls.append(args)
        if not self.results:
            return {"ok": True}
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FakeFileResponse:
    def __init__(self, content: bytes):
        self.content = content
        self.status_code = 200
        self.headers: dict[str, str] = {}

    def raise_for_status(self):
        return None


class FakeFileHttpClient:
    def __init__(self, content: bytes, *, timeout=None, follow_redirects=None):
        self.content = content
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def get(self, url, headers=None):
        self.calls.append((url, headers or {}))
        return FakeFileResponse(self.content)


async def _case(db_session, *, started_at=None):
    user = User(name="Agent测试用户")
    db_session.add(user)
    await db_session.flush()
    service_case = ServiceCase(
        case_no=f"AGENT-{secrets.token_hex(4).upper()}",
        user_id=user.id,
        group_id=f"agent-{secrets.token_hex(4)}",
        questionnaire_token=secrets.token_urlsafe(16),
        status=ServiceCaseStatus.IN_SERVICE,
        started_at=started_at or datetime.now(timezone.utc),
    )
    db_session.add(service_case)
    await db_session.flush()
    return service_case


async def _inbound(db_session, service_case: ServiceCase, content: str = "我现在第几天？") -> Message:
    inbound = Message(
        service_case_id=service_case.id,
        group_id=service_case.group_id,
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.USER,
        message_type="text",
        content=content,
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
    )
    db_session.add(inbound)
    await db_session.flush()
    return inbound


def _docx_bytes(*paragraphs: str) -> bytes:
    document = Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_skill_registry_loads_skill_and_rejects_path_escape():
    registry = SkillRegistry()
    catalog = registry.catalog_text()
    assert "product_usage" in catalog
    assert "checkin_analysis" in catalog

    skill = registry.read_skill("checkin_analysis")
    assert "get_weight_trend" in skill

    product_skill = registry.read_skill("product_usage")
    assert "14:00 以后不要建议补喝午B" in product_skill
    assert "今天先跳过午B" in product_skill
    assert "严禁建议午餐时喝晚A" in product_skill
    assert "晚A别放午餐喝" in product_skill

    with pytest.raises(ValueError):
        registry.read_skill_file("checkin_analysis", "../product_usage/SKILL.md")


@pytest.mark.asyncio
async def test_service_progress_tool_only_uses_bound_context(db_session):
    settings = get_settings()
    service_case = await _case(db_session)
    tools = {
        tool.name: tool
        for tool in build_service_case_tools(
            context=AgentRunContext(service_case_id=service_case.id),
            settings=settings,
        )
    }

    result = await tools["get_service_progress"].ainvoke({})

    assert result["ok"] is True
    assert result["status"] == "in_service"
    assert "service_day" in result


@pytest.mark.asyncio
async def test_agent_can_read_and_update_bound_checkin_followup_preferences(db_session):
    """The chat Agent can change only the current user's case, with current message evidence."""
    connection = await db_session.connection()
    await connection.run_sync(
        lambda sync_connection: CheckinFollowupPreference.__table__.create(
            sync_connection, checkfirst=True
        )
    )
    await db_session.commit()
    service_case = await _case(db_session)
    inbound = await _inbound(db_session, service_case, "别再问我体重和腰围了")
    # Tool implementation intentionally opens its own transaction, like production.
    await db_session.commit()
    tools = {
        tool.name: tool
        for tool in build_followup_preference_tools(
            context=AgentRunContext(
                service_case_id=service_case.id,
                trigger_id=inbound.id,
            )
        )
    }

    initial = await tools["get_checkin_followup_preferences"].ainvoke({})
    assert all(item["enabled"] for item in initial["preferences"])

    updated = await tools["update_checkin_followup_preferences"].ainvoke(
        {"topics": ["weight", "waist"], "enabled": False}
    )
    assert updated["ok"] is True
    states = {item["topic"]: item["enabled"] for item in updated["preferences"]}
    assert states["weight"] is False
    assert states["waist"] is False
    assert states["daily_checklist"] is True

    rows = list(await db_session.scalars(select(CheckinFollowupPreference)))
    assert {(row.topic, row.enabled, row.source_message_id) for row in rows} == {
        ("weight", False, inbound.id),
        ("waist", False, inbound.id),
    }

    restored = await tools["update_checkin_followup_preferences"].ainvoke(
        {"topics": ["weight"], "enabled": True}
    )
    restored_states = {item["topic"]: item["enabled"] for item in restored["preferences"]}
    assert restored_states["weight"] is True
    assert restored_states["waist"] is False


@pytest.mark.asyncio
async def test_weight_trend_tool_returns_data_insufficient(db_session):
    settings = get_settings()
    service_case = await _case(db_session)
    db_session.add(
        Checkin(
            service_case_id=service_case.id,
            group_id=service_case.group_id,
            checkin_date=date.today(),
            content="今日体重 65.2",
            parsed_json={"weight": "65.2"},
        )
    )
    await db_session.flush()
    tools = {
        tool.name: tool
        for tool in build_service_case_tools(
            context=AgentRunContext(service_case_id=service_case.id),
            settings=settings,
        )
    }

    result = await tools["get_weight_trend"].ainvoke({"days": 7})

    assert result["ok"] is True
    assert result["trend"] is None
    assert "数据不足" in result["note"]


@pytest.mark.asyncio
async def test_recent_checkins_separate_general_and_personalized_items(db_session):
    settings = get_settings()
    service_case = await _case(db_session)
    document = UserDocument(
        service_case_id=service_case.id,
        document_type=DocumentType.DAY28_EXECUTION_STANDARD,
        title="28天执行标准与打卡要求",
        content_md="ok",
        content_json={
            "daily_checkin": [
                "今天是否完成早C/午B/晚A？",
                "今天是否在23:30前上床？",
                "今天蔬菜是否达到两拳？",
                "今天是否完成7000步？",
                "今天情绪评分（1-5分）",
            ]
        },
    )
    db_session.add(document)
    await db_session.flush()
    checkin = Checkin(
        service_case_id=service_case.id,
        group_id=service_case.group_id,
        checkin_date=date.today(),
        content="今天体重65kg，专属项已答",
        parsed_json={
            "weight": "65",
            "sleep": "7小时",
            "custom_checkin": {
                f"document:{document.id}:2": {"answer": "是"},
                f"document:{document.id}:5": {"answer": "4"},
            },
        },
    )
    db_session.add(checkin)
    await db_session.flush()
    tools = {
        tool.name: tool
        for tool in build_service_case_tools(
            context=AgentRunContext(service_case_id=service_case.id),
            settings=settings,
        )
    }

    result = await tools["get_recent_checkins"].ainvoke({"days": 7})

    item = result["items"][0]
    assert item["general"] == {"weight": "65", "sleep": "7小时"}
    assert item["personalized"] == [
        {
            "item_id": f"document:{document.id}:2",
            "label": "今天是否在23:30前上床？",
            "answer": "是",
        },
        {
            "item_id": f"document:{document.id}:5",
            "label": "今天情绪评分（1-5分）",
            "answer": "4",
        },
    ]


@pytest.mark.asyncio
async def test_weight_trend_tool_returns_initial_weight_when_no_checkins(db_session):
    settings = get_settings()
    service_case = await _case(db_session)
    db_session.add(
        UserProfile(
            service_case_id=service_case.id,
            profile_json={"overview": {"height_weight": "170cm / 65kg"}},
            summary="入营初始体重 65kg",
        )
    )
    await db_session.flush()
    tools = {
        tool.name: tool
        for tool in build_service_case_tools(
            context=AgentRunContext(service_case_id=service_case.id),
            settings=settings,
        )
    }

    result = await tools["get_weight_trend"].ainvoke({"days": 7})

    assert result["ok"] is True
    assert result["points"] == []
    assert result["initial_weight"] == 65
    assert result["trend"] is None
    assert "入营初始体重" in result["note"]


@pytest.mark.asyncio
async def test_read_group_file_tool_returns_latest_parsed_docx(db_session):
    settings = get_settings()
    service_case = await _case(db_session)
    other_case = await _case(db_session)
    db_session.add(
        Message(
            service_case_id=other_case.id,
            group_id=other_case.group_id,
            direction=MessageDirection.INBOUND,
            sender_type=MessageSenderType.USER,
            message_type="file",
            content="[文件] 其他服务单.docx",
            purpose=MessagePurpose.CHAT,
            status=MessageStatus.RECEIVED,
            analysis_json={
                "file_info": {
                    "filename": "其他服务单.docx",
                    "parse_status": "ok",
                    "extracted_text": "不应该跨服务单读取",
                }
            },
        )
    )
    db_session.add(
        Message(
            service_case_id=service_case.id,
            group_id=service_case.group_id,
            direction=MessageDirection.INBOUND,
            sender_type=MessageSenderType.STAFF,
            message_type="file",
            content="[文件] 执行方案.docx",
            purpose=MessagePurpose.CHAT,
            status=MessageStatus.RECEIVED,
            analysis_json={
                "file_info": {
                    "filename": "执行方案.docx",
                    "parse_status": "ok",
                    "extracted_text": "早餐先吃蛋白质，午餐主食减半。",
                }
            },
        )
    )
    await db_session.flush()
    tools = {
        tool.name: tool
        for tool in build_service_case_tools(
            context=AgentRunContext(service_case_id=service_case.id),
            settings=settings,
        )
    }

    result = await tools["read_group_file"].ainvoke({})

    assert result["ok"] is True
    assert result["filename"] == "执行方案.docx"
    assert result["parse_status"] == "ok"
    assert "早餐先吃蛋白质" in result["text"]
    assert "不应该跨服务单读取" not in result["text"]


@pytest.mark.asyncio
async def test_read_group_file_tool_reports_latest_unparsed_file(db_session):
    settings = get_settings()
    service_case = await _case(db_session)
    db_session.add(
        Message(
            service_case_id=service_case.id,
            group_id=service_case.group_id,
            direction=MessageDirection.INBOUND,
            sender_type=MessageSenderType.STAFF,
            message_type="file",
            content="[文件] 已解析.docx",
            purpose=MessagePurpose.CHAT,
            status=MessageStatus.RECEIVED,
            analysis_json={
                "file_info": {
                    "filename": "已解析.docx",
                    "parse_status": "ok",
                    "extracted_text": "旧文件内容",
                }
            },
        )
    )
    db_session.add(
        Message(
            service_case_id=service_case.id,
            group_id=service_case.group_id,
            direction=MessageDirection.INBOUND,
            sender_type=MessageSenderType.STAFF,
            message_type="file",
            content="[文件]",
            purpose=MessagePurpose.CHAT,
            status=MessageStatus.RECEIVED,
            analysis_json={"_qiwe_meta": {"sender_type": "staff"}},
        )
    )
    await db_session.flush()
    tools = {
        tool.name: tool
        for tool in build_service_case_tools(
            context=AgentRunContext(service_case_id=service_case.id),
            settings=settings,
        )
    }

    result = await tools["read_group_file"].ainvoke({})

    assert result["ok"] is True
    assert result["parse_status"] == "missing"
    assert result["text"] == ""
    assert "旧文件内容" not in result.get("note", "")


def _file_msg(service_case, *, filename, text, status="ok", sender=MessageSenderType.USER):
    return Message(
        service_case_id=service_case.id,
        group_id=service_case.group_id,
        direction=MessageDirection.INBOUND,
        sender_type=sender,
        message_type="file",
        content=f"[文件] {filename}",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
        analysis_json={"file_info": {"filename": filename, "parse_status": status, "extracted_text": text}},
    )


async def _tools(db_session, service_case):
    return {
        tool.name: tool
        for tool in build_service_case_tools(
            context=AgentRunContext(service_case_id=service_case.id),
            settings=get_settings(),
        )
    }


@pytest.mark.asyncio
async def test_list_group_files_lists_current_case_newest_first(db_session):
    service_case = await _case(db_session)
    other_case = await _case(db_session)
    db_session.add(_file_msg(other_case, filename="别的服务单.docx", text="不该出现"))
    db_session.add(_file_msg(service_case, filename="阿杰方案.docx", text="早餐蛋白质"))
    db_session.add(_file_msg(service_case, filename="第二份.docx", text="", status="empty"))
    await db_session.flush()
    tools = await _tools(db_session, service_case)

    result = await tools["list_group_files"].ainvoke({})

    assert result["ok"] is True
    assert result["count"] == 2  # 不含别的服务单
    names = [f["filename"] for f in result["files"]]
    assert names == ["第二份.docx", "阿杰方案.docx"]  # 最新在前
    assert "别的服务单.docx" not in names
    first = result["files"][0]
    assert first["readable"] is False and first["parse_status"] == "empty"
    assert {"message_id", "uploaded_at", "sender_type", "text_length"} <= set(first)
    assert result["files"][1]["readable"] is True


@pytest.mark.asyncio
async def test_read_group_file_by_message_id_reads_exact_file(db_session):
    service_case = await _case(db_session)
    older = _file_msg(service_case, filename="阿杰方案.docx", text="阿杰的执行方案正文")
    db_session.add(older)
    db_session.add(_file_msg(service_case, filename="最新.docx", text="最新文件正文"))
    await db_session.flush()
    tools = await _tools(db_session, service_case)

    result = await tools["read_group_file"].ainvoke({"message_id": older.id})

    assert result["filename"] == "阿杰方案.docx"
    assert "阿杰的执行方案正文" in result["text"]
    assert result["message_id"] == older.id


@pytest.mark.asyncio
async def test_read_group_file_by_filename_fuzzy_match(db_session):
    service_case = await _case(db_session)
    db_session.add(_file_msg(service_case, filename="阿杰_60-90天健康减脂执行方案.docx", text="阿杰方案正文"))
    db_session.add(_file_msg(service_case, filename="最新体检报告.docx", text="体检正文"))
    await db_session.flush()
    tools = await _tools(db_session, service_case)

    result = await tools["read_group_file"].ainvoke({"filename": "阿杰"})

    assert "阿杰" in result["filename"]
    assert "阿杰方案正文" in result["text"]


@pytest.mark.asyncio
async def test_read_group_file_message_id_not_found_returns_note(db_session):
    service_case = await _case(db_session)
    db_session.add(_file_msg(service_case, filename="唯一.docx", text="正文"))
    await db_session.flush()
    tools = await _tools(db_session, service_case)

    result = await tools["read_group_file"].ainvoke({"message_id": 99999999})

    assert result["ok"] is True
    assert result["text"] == ""
    assert "没找到" in result["note"]


def _delivery_doc(
    service_case,
    *,
    document_type: DocumentType,
    title: str,
    content_md: str,
    status: DocumentStatus = DocumentStatus.REVIEWING,
) -> UserDocument:
    return UserDocument(
        service_case_id=service_case.id,
        document_type=document_type,
        title=title,
        content_md=content_md,
        content_json={},
        status=status,
    )


@pytest.mark.asyncio
async def test_list_delivery_documents_lists_current_case_with_real_document_type(db_session):
    service_case = await _case(db_session)
    other_case = await _case(db_session)
    db_session.add(
        _delivery_doc(
            other_case,
            document_type=DocumentType.ENERGY_RESTART_PRESCRIPTION,
            title="其他服务单处方单",
            content_md="不应该跨服务单读取",
            status=DocumentStatus.SENT,
        )
    )
    db_session.add(
        _delivery_doc(
            service_case,
            document_type=DocumentType.ENERGY_RESTART_PRESCRIPTION,
            title="能量重启处方单",
            content_md="处方单正文",
            status=DocumentStatus.SENT,
        )
    )
    db_session.add(
        _delivery_doc(
            service_case,
            document_type=DocumentType.STAGE_REPORT,
            title="第7天阶段报告",
            content_md="阶段报告正文",
            status=DocumentStatus.ARCHIVED,
        )
    )
    await db_session.flush()
    tools = await _tools(db_session, service_case)

    result = await tools["list_delivery_documents"].ainvoke({})

    assert result["ok"] is True
    assert result["count"] == 1
    titles = [item["title"] for item in result["documents"]]
    assert titles == ["能量重启处方单"]
    assert "第7天阶段报告" not in titles
    assert "其他服务单处方单" not in titles
    first = result["documents"][0]
    assert first["document_type"] == DocumentType.ENERGY_RESTART_PRESCRIPTION.value
    assert first["document_type_label"] == "能量重启处方单"
    assert first["readable"] is True
    assert {"document_id", "status", "created_at", "updated_at", "text_length"} <= set(first)


@pytest.mark.asyncio
async def test_list_delivery_documents_filters_by_document_type_enum(db_session):
    service_case = await _case(db_session)
    db_session.add(
        _delivery_doc(
            service_case,
            document_type=DocumentType.ENERGY_RESTART_PRESCRIPTION,
            title="能量重启处方单",
            content_md="处方单正文",
            status=DocumentStatus.SENT,
        )
    )
    db_session.add(
        _delivery_doc(
            service_case,
            document_type=DocumentType.DAY28_EXECUTION_STANDARD,
            title="28天执行单",
            content_md="执行单正文",
            status=DocumentStatus.SENT,
        )
    )
    await db_session.flush()
    tools = await _tools(db_session, service_case)

    result = await tools["list_delivery_documents"].ainvoke(
        {"document_type": DocumentType.DAY28_EXECUTION_STANDARD.value}
    )

    assert result["ok"] is True
    assert result["count"] == 1
    assert result["documents"][0]["document_type"] == DocumentType.DAY28_EXECUTION_STANDARD.value
    assert result["documents"][0]["title"] == "28天执行单"


@pytest.mark.asyncio
async def test_read_delivery_document_by_document_id_reads_exact_document(db_session):
    service_case = await _case(db_session)
    other_case = await _case(db_session)
    target = _delivery_doc(
        service_case,
        document_type=DocumentType.STAGE_REPORT,
        title="第7天阶段报告",
        content_md="第7天报告：晚A执行稳定。",
        status=DocumentStatus.SENT,
    )
    db_session.add(target)
    db_session.add(
        _delivery_doc(
            service_case,
            document_type=DocumentType.STAGE_REPORT,
            title="第14天阶段报告",
            content_md="第14天报告正文",
            status=DocumentStatus.SENT,
        )
    )
    db_session.add(
        _delivery_doc(
            other_case,
            document_type=DocumentType.STAGE_REPORT,
            title="其他服务单第7天报告",
            content_md="不应该跨服务单读取",
            status=DocumentStatus.SENT,
        )
    )
    await db_session.flush()
    tools = await _tools(db_session, service_case)

    result = await tools["read_delivery_document"].ainvoke({"document_id": target.id})

    assert result["ok"] is True
    assert result["document_id"] == target.id
    assert result["document_type"] == DocumentType.STAGE_REPORT.value
    assert result["title"] == "第7天阶段报告"
    assert "晚A执行稳定" in result["text"]
    assert "不应该跨服务单读取" not in result["text"]


@pytest.mark.asyncio
async def test_read_delivery_document_by_type_returns_single_match(db_session):
    service_case = await _case(db_session)
    db_session.add(
        _delivery_doc(
            service_case,
            document_type=DocumentType.ENERGY_RESTART_PRESCRIPTION,
            title="能量重启处方单",
            content_md="早餐先吃蛋白质。",
            status=DocumentStatus.SENT,
        )
    )
    await db_session.flush()
    tools = await _tools(db_session, service_case)

    result = await tools["read_delivery_document"].ainvoke(
        {"document_type": DocumentType.ENERGY_RESTART_PRESCRIPTION.value}
    )

    assert result["ok"] is True
    assert result["document_type"] == DocumentType.ENERGY_RESTART_PRESCRIPTION.value
    assert "早餐先吃蛋白质" in result["text"]


@pytest.mark.asyncio
async def test_read_delivery_document_by_type_returns_candidates_when_multiple(db_session):
    service_case = await _case(db_session)
    db_session.add(
        _delivery_doc(
            service_case,
            document_type=DocumentType.STAGE_REPORT,
            title="第7天阶段报告",
            content_md="第7天正文",
            status=DocumentStatus.SENT,
        )
    )
    db_session.add(
        _delivery_doc(
            service_case,
            document_type=DocumentType.STAGE_REPORT,
            title="第14天阶段报告",
            content_md="第14天正文",
            status=DocumentStatus.SENT,
        )
    )
    await db_session.flush()
    tools = await _tools(db_session, service_case)

    result = await tools["read_delivery_document"].ainvoke({"document_type": DocumentType.STAGE_REPORT.value})

    assert result["ok"] is True
    assert result["text"] == ""
    assert len(result["candidates"]) == 2
    assert {item["title"] for item in result["candidates"]} == {"第7天阶段报告", "第14天阶段报告"}
    assert "document_id" in result["note"]


@pytest.mark.asyncio
async def test_read_delivery_document_rejects_freeform_document_type(db_session):
    service_case = await _case(db_session)
    tools = await _tools(db_session, service_case)

    with pytest.raises(Exception):
        await tools["read_delivery_document"].ainvoke({"document_type": "处方"})


@pytest.mark.asyncio
async def test_read_delivery_document_does_not_read_archived_document_by_id(db_session):
    service_case = await _case(db_session)
    archived = _delivery_doc(
        service_case,
        document_type=DocumentType.STAGE_REPORT,
        title="第7天阶段报告",
        content_md="这份驳回稿不应被群聊 Agent 读取。",
        status=DocumentStatus.ARCHIVED,
    )
    db_session.add(archived)
    await db_session.flush()
    tools = await _tools(db_session, service_case)

    result = await tools["read_delivery_document"].ainvoke({"document_id": archived.id})

    assert result["ok"] is True
    assert result["text"] == ""
    assert "已发送" in result["note"]
    assert "驳回稿" not in result["note"]


@pytest.mark.asyncio
async def test_process_inbound_file_extracts_docx_text(db_session, monkeypatch):
    service_case = await _case(db_session)
    inbound = Message(
        service_case_id=service_case.id,
        group_id=service_case.group_id,
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.STAFF,
        message_type="file",
        content="[文件]",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
        analysis_json={"_qiwe_meta": {"sender_type": "staff"}},
    )
    db_session.add(inbound)
    await db_session.flush()
    inbound_id = inbound.id
    await db_session.commit()
    docx_content = _docx_bytes("早餐先吃蛋白质", "午餐主食减半")

    def fake_client(*args, **kwargs):
        return FakeFileHttpClient(docx_content, *args, **kwargs)

    monkeypatch.setattr("app.services.ranren_ai.engine.httpx.AsyncClient", fake_client)
    message = ChannelMessage(
        channel="qiwe",
        chat_id=service_case.group_id,
        chat_type="group",
        text="[文件]",
        message_type="file",
        raw_payload={
            "msgData": {
                "fileHttpUrl": "https://imunion.weixin.qq.com/cgi-bin/mmae-bin/tpdownloadmedia?param=v1_test",
                "fileAuthKey": "auth-key",
                "fileSize": len(docx_content),
                "filename": "执行方案.docx",
            }
        },
    )

    await RanrenAIEngine(FakeAsyncTool({}))._process_inbound_file(inbound_id, message, service_case.id)
    await db_session.refresh(inbound)

    assert inbound.content == "[文件] 执行方案.docx"
    info = inbound.analysis_json["file_info"]
    assert info["filename"] == "执行方案.docx"
    assert info["parse_status"] == "ok"
    assert "早餐先吃蛋白质" in info["extracted_text"]
    assert "午餐主食减半" in info["extracted_text"]
    assert message.text == "[文件] 执行方案.docx"


@pytest.mark.asyncio
async def test_agent_memory_context_compacts_profile_and_state(db_session):
    service_case = await _case(db_session)
    db_session.add(
        UserProfile(
            service_case_id=service_case.id,
            profile_json={
                "summary": "用户经常加班，晚餐容易吃得晚。",
                "main_problem": "节律不稳定",
                "day28_goal": "白天精力更稳",
                "need_analysis": {"priority_need": "先稳住晚餐和睡前节律"},
            },
            summary="用户经常加班，晚餐容易吃得晚。",
            generated_by_model="test",
        )
    )
    db_session.add(
        UserStateProfile(
            service_case_id=service_case.id,
            state_json={
                "domains": {
                    "personal_preferences": {
                        "current": {
                            "value": {
                                "hobby": {
                                    "value": "喜欢潮玩，最近聊到 LABUBU 后面的热门款",
                                    "event_type": "profile_signal",
                                    "milestone": "profile",
                                }
                            }
                        }
                    },
                    "lifestyle_habits": {
                        "current": {"value": {"work": "工作日晚间经常加班"}}
                    },
                }
            },
            summary="动态状态",
            source="test",
        )
    )
    await db_session.flush()

    structured = await load_profile_memory_context(db_session, service_case_id=service_case.id)
    assert structured.stable_facts
    assert any("喜欢潮玩" in item for item in structured.preferences)
    assert not any("profile_signal" in item for item in structured.preferences)

    memory = await build_agent_memory_context(db_session, service_case_id=service_case.id)

    assert "画像摘要：用户经常加班" in memory
    assert "个人偏好：喜欢潮玩" in memory
    assert "稳定习惯：工作日晚间经常加班" in memory


@pytest.mark.asyncio
async def test_agent_memory_context_respects_selected_scopes(db_session):
    service_case = await _case(db_session)
    db_session.add(
        UserProfile(
            service_case_id=service_case.id,
            profile_json={
                "summary": "用户晚餐容易拖晚，体重目标是稳定精力。",
                "risks": "咖啡因敏感",
            },
            summary="用户晚餐容易拖晚，体重目标是稳定精力。",
            generated_by_model="test",
        )
    )
    db_session.add(
        UserStateProfile(
            service_case_id=service_case.id,
            state_json={
                "domains": {
                    "personal_preferences": {
                        "current": {"value": {"hobby": {"value": "喜欢潮玩和动漫"}}}
                    },
                    "lifestyle_habits": {
                        "current": {"value": {"work": "工作日晚间经常加班"}}
                    },
                    "product_usage": {
                        "current": {"value": {"timing": "午B容易忘记"}}
                    },
                }
            },
            summary="动态状态",
            source="test",
        )
    )
    await db_session.flush()

    interest_memory = await build_agent_memory_context(
        db_session,
        service_case_id=service_case.id,
        memory_scopes=["preferences"],
    )
    assert "个人偏好：喜欢潮玩和动漫" in interest_memory
    assert "咖啡因敏感" not in interest_memory
    assert "午B容易忘记" not in interest_memory
    assert "工作日晚间经常加班" not in interest_memory

    product_memory = await build_agent_memory_context(
        db_session,
        service_case_id=service_case.id,
        memory_scopes=["current_state", "warnings", "habits"],
    )
    assert "午B容易忘记" in product_memory
    assert "咖啡因敏感" in product_memory
    assert "工作日晚间经常加班" in product_memory
    assert "喜欢潮玩和动漫" not in product_memory


@pytest.mark.asyncio
async def test_graph_runs_agent_tools_loop(db_session):
    settings = get_settings()
    service_case = await _case(db_session)
    db_session.add(
        UserProfile(
            service_case_id=service_case.id,
            profile_json={
                "summary": "用户晚餐容易拖晚，喜欢潮玩话题。",
                "main_problem": "节律不稳定",
            },
            summary="用户晚餐容易拖晚，喜欢潮玩话题。",
            generated_by_model="test",
        )
    )
    await db_session.flush()
    model = FakeToolCallingModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "get_service_progress",
                        "args": {},
                        "id": "call-progress",
                    }
                ],
            ),
            AIMessage(content="你现在是服务进行中，今天先稳住一个动作。"),
        ]
    )

    result = await RanrenAgentGraph(
        context=AgentRunContext(service_case_id=service_case.id, trigger_id=123),
        settings=settings,
        chat_model=model,
    ).run(user_query="我现在第几天了？", recent_messages=[])

    assert "服务" in result.reply
    assert result.tool_trace[0]["name"] == "get_service_progress"
    assert len(model.calls) == 2
    assert "【当前时间】" in model.calls[0][1].content
    assert "【隐性用户背景】" in model.calls[0][1].content
    assert "用户晚餐容易拖晚" in model.calls[0][1].content
    assert "现在、最近、最新、今年" in model.calls[0][1].content
    assert "短、直接、别演" in model.calls[0][0].content
    assert "让我想想" in model.calls[0][0].content
    assert "根据搜索结果" in model.calls[0][0].content
    assert "不要说“根据你的画像/资料/系统记录”" in model.calls[0][0].content
    assert "不要把它说成营养师、班主任或其他真人说过的话" in model.calls[0][0].content
    assert "只用与当前问题直接相关的部分" in model.calls[0][0].content
    assert "只参考长期偏好/最近话题" in model.calls[0][0].content
    assert "只参考当前状态/风险边界/产品执行" in model.calls[0][0].content
    assert "实时数据一律查工具" in model.calls[0][0].content
    assert "这是群聊接话不是考试答题" in model.calls[0][0].content
    assert "上下文不够先追问一个关键点" in model.calls[0][0].content
    assert "不确定可以承认不知道" in model.calls[0][0].content
    assert "多数时候只回当下这一下" in model.calls[0][0].content
    assert "零称呼起头" in model.calls[0][0].content
    assert "哥、姐、宝子、亲、阿姨" in model.calls[0][0].content
    assert "你说 LABUBU 后面那个？" in model.calls[0][0].content
    assert "咒术回战：节奏快" in model.calls[0][0].content
    assert "今天没排便体重还涨了" in model.calls[0][0].content
    assert "晚餐能吃荞麦面吗" in model.calls[0][0].content


@pytest.mark.asyncio
async def test_graph_can_call_followup_preference_tool_without_bot_mention(db_session):
    connection = await db_session.connection()
    await connection.run_sync(
        lambda sync_connection: CheckinFollowupPreference.__table__.create(
            sync_connection, checkfirst=True
        )
    )
    service_case = await _case(db_session)
    inbound = await _inbound(db_session, service_case, "别再问我体重和腰围了")
    model = FakeToolCallingModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "update_checkin_followup_preferences",
                        "args": {"topics": ["weight", "waist"], "enabled": False},
                        "id": "call-followup-preference",
                    }
                ],
            ),
            AIMessage(content="好的，后续不再主动问体重和腰围；想恢复时告诉我就行。"),
        ]
    )

    result = await RanrenAgentGraph(
        context=AgentRunContext(service_case_id=service_case.id, trigger_id=inbound.id),
        settings=get_settings(),
        chat_model=model,
    ).run(user_query=inbound.content, recent_messages=[])

    assert "不再主动问体重和腰围" in result.reply
    assert result.tool_trace[0]["name"] == "update_checkin_followup_preferences"
    assert "无需用户 @ 你或回复你的上一条消息" in model.calls[0][0].content
    rows = list(await db_session.scalars(select(CheckinFollowupPreference)))
    assert {(row.topic, row.enabled, row.source_message_id) for row in rows} == {
        ("weight", False, inbound.id),
        ("waist", False, inbound.id),
    }


@pytest.mark.asyncio
async def test_graph_data_tool_failure_resets_after_success(db_session):
    settings = get_settings()
    service_case = await _case(db_session)
    model = FakeToolCallingModel(
        [
            AIMessage(
                content="",
                tool_calls=[{"name": "get_service_progress", "args": {}, "id": "call-progress-1"}],
            ),
            AIMessage(
                content="",
                tool_calls=[{"name": "get_service_progress", "args": {}, "id": "call-progress-2"}],
            ),
            AIMessage(content="刚才查到了，你现在是服务进行中。"),
        ]
    )
    graph = RanrenAgentGraph(
        context=AgentRunContext(service_case_id=service_case.id),
        settings=settings,
        chat_model=model,
    )
    graph.tools_by_name["get_service_progress"] = FakeSequenceTool(
        [{"ok": False, "error": "temporary db error"}, {"ok": True, "status": "in_service"}]
    )

    result = await graph.run(user_query="我现在第几天？", recent_messages=[])

    assert result.data_tool_failed is False
    assert result.tool_trace[0]["data_tool_failed"] is True
    assert result.tool_trace[1]["data_tool_failed"] is False


@pytest.mark.asyncio
async def test_graph_data_tool_final_failure_stays_failed(db_session):
    settings = get_settings()
    service_case = await _case(db_session)
    model = FakeToolCallingModel(
        [
            AIMessage(
                content="",
                tool_calls=[{"name": "get_service_progress", "args": {}, "id": "call-progress-1"}],
            ),
            AIMessage(content="我这边暂时查不到准确数据。"),
        ]
    )
    graph = RanrenAgentGraph(
        context=AgentRunContext(service_case_id=service_case.id),
        settings=settings,
        chat_model=model,
    )
    graph.tools_by_name["get_service_progress"] = FakeSequenceTool([{"ok": False, "error": "db error"}])

    result = await graph.run(user_query="我现在第几天？", recent_messages=[])

    assert result.data_tool_failed is True
    assert result.tool_trace[0]["data_tool_failed"] is True


@pytest.mark.asyncio
async def test_graph_circuits_tool_after_two_consecutive_failures(db_session):
    settings = get_settings()
    service_case = await _case(db_session)
    model = FakeToolCallingModel(
        [
            AIMessage(
                content="",
                tool_calls=[{"name": "get_service_progress", "args": {}, "id": "call-progress-1"}],
            ),
            AIMessage(
                content="",
                tool_calls=[{"name": "get_service_progress", "args": {}, "id": "call-progress-2"}],
            ),
            AIMessage(
                content="",
                tool_calls=[{"name": "get_service_progress", "args": {}, "id": "call-progress-3"}],
            ),
            AIMessage(content="我这边暂时查不到准确数据。"),
        ]
    )
    graph = RanrenAgentGraph(
        context=AgentRunContext(service_case_id=service_case.id),
        settings=settings,
        chat_model=model,
    )
    fake_tool = FakeSequenceTool(
        [{"ok": False, "error": "db error"}, {"ok": False, "error": "db error"}]
    )
    graph.tools_by_name["get_service_progress"] = fake_tool

    result = await graph.run(user_query="我现在第几天？", recent_messages=[])

    assert len(fake_tool.calls) == 2
    assert result.tool_trace[2]["ok"] is False
    assert result.tool_trace[2]["circuit_rejected"] is True
    assert "TOOL_CIRCUIT_OPEN" in result.tool_trace[2]["error"]
    assert result.data_tool_failed is True


@pytest.mark.asyncio
async def test_graph_responds_to_every_tool_call_when_over_budget(db_session):
    # 一条 AIMessage 里的 tool_call 数超过本轮剩余预算时，超预算的也必须补 ToolMessage，
    # 否则 tool_call 缺响应会让下一轮 LLM 400。这里断言每个 tool_call 都进了 tool_trace。
    settings = get_settings()
    original_max = settings.agent_max_tool_calls
    settings.agent_max_tool_calls = 1
    try:
        service_case = await _case(db_session)
        model = FakeToolCallingModel(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "get_service_progress", "args": {}, "id": "call-a"},
                        {"name": "get_recent_checkins", "args": {}, "id": "call-b"},
                    ],
                ),
                AIMessage(content="好的，帮你看过了。"),
            ]
        )
        graph = RanrenAgentGraph(
            context=AgentRunContext(service_case_id=service_case.id),
            settings=settings,
            chat_model=model,
        )
        graph.tools_by_name["get_service_progress"] = FakeSequenceTool([{"ok": True, "status": "in_service"}])
        graph.tools_by_name["get_recent_checkins"] = FakeSequenceTool([{"ok": True, "items": []}])

        result = await graph.run(user_query="我现在什么情况", recent_messages=[])

        assert result.reply == "好的，帮你看过了。"
        # 两个 tool_call 都要有 trace（= 都补了 ToolMessage）：一个执行、一个 TOOL_LIMIT_REACHED。
        assert len(result.tool_trace) == 2
        errors = [t.get("error") or "" for t in result.tool_trace]
        assert any("TOOL_LIMIT_REACHED" in e for e in errors)
    finally:
        settings.agent_max_tool_calls = original_max


@pytest.mark.asyncio
async def test_web_search_rejects_health_queries_without_network():
    settings = get_settings()
    original_enabled = settings.web_search_enabled
    original_key = settings.tavily_api_key
    try:
        settings.web_search_enabled = True
        settings.tavily_api_key = "test-key"
        tools = {tool.name: tool for tool in build_web_search_tools(settings=settings)}

        result = await tools["web_search_lifestyle"].ainvoke({"query": "高血压减肥药怎么吃"})
    finally:
        settings.web_search_enabled = original_enabled
        settings.tavily_api_key = original_key

    assert result["ok"] is False
    assert "不能联网搜索" in result["error"]


def test_web_search_normalizes_stale_year_for_current_queries():
    normalized = _normalize_current_query_year("2025年现在最火的动漫排行榜")

    assert "2025年" not in normalized
    assert "现在最火的动漫排行榜" in normalized


@pytest.mark.asyncio
async def test_graph_allows_two_web_searches_and_rejects_extra(db_session):
    settings = get_settings()
    service_case = await _case(db_session)
    model = FakeToolCallingModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "web_search_lifestyle",
                        "args": {"query": "近期电影推荐"},
                        "id": "call-web-1",
                    },
                    {
                        "name": "web_search_lifestyle",
                        "args": {"query": "近期电视剧推荐"},
                        "id": "call-web-2",
                    },
                    {
                        "name": "web_search_lifestyle",
                        "args": {"query": "近期综艺推荐"},
                        "id": "call-web-3",
                    },
                ],
            ),
            AIMessage(content="最近可以先看一部电影和一部剧，挑你喜欢的类型就好。"),
        ]
    )
    graph = RanrenAgentGraph(
        context=AgentRunContext(service_case_id=service_case.id, trigger_id=123),
        settings=settings,
        chat_model=model,
    )
    fake_search = FakeAsyncTool({"ok": True, "results": [{"title": "电影推荐"}]})
    graph.tools_by_name["web_search_lifestyle"] = fake_search

    result = await graph.run(user_query="最近有什么好看的电影电视剧？", recent_messages=[])

    assert len(fake_search.calls) == 2
    assert [trace["name"] for trace in result.tool_trace] == [
        "web_search_lifestyle",
        "web_search_lifestyle",
        "web_search_lifestyle",
    ]
    assert result.tool_trace[0]["ok"] is True
    assert result.tool_trace[1]["ok"] is True
    assert result.tool_trace[2]["ok"] is False
    assert "最多联网搜索 2 次" in result.tool_trace[2]["error"]


@pytest.mark.asyncio
async def test_engine_uses_agent_without_global_or_case_toggle(
    db_session,
    mock_message_channel,
    monkeypatch,
):
    await set_ai_reply_enabled(True, db_session)
    calls: list[int] = []

    async def fake_generate_agent_reply(
        *, service_case_id, trigger_id, user_query, query_speaker, recent_messages, analysis, channel=None, settings
    ):
        calls.append(service_case_id)
        assert query_speaker == "用户"
        assert channel == "qiwe"
        assert analysis == {"requires_human_intervention": False}
        return AgentReplyResult(
            reply="agent reply",
            tool_trace=[{"name": "get_service_progress", "ok": True}],
        )

    monkeypatch.setattr("app.services.ranren_ai.engine.generate_agent_reply", fake_generate_agent_reply)

    try:
        service_case = await _case(db_session)
        inbound = await _inbound(db_session, service_case)
        engine = RanrenAIEngine(mock_message_channel)
        message = ChannelMessage(
            channel="qiwe",
            chat_id=service_case.group_id,
            chat_type="group",
            text="我现在第几天？",
            message_id="agent-toggle-1",
            user_id="user-1",
        )

        await engine._reply_with_ai(
            message,
            service_case_id=service_case.id,
            analysis={"requires_human_intervention": False},
            trigger_id=inbound.id,
        )
        assert calls == [service_case.id]
    finally:
        await set_ai_reply_enabled(True, db_session)
