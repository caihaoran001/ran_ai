from __future__ import annotations

import asyncio
import secrets
from io import BytesIO
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from docx import Document
from sqlalchemy import select

from app.core.config import get_settings
from app.models.archivist_case_memory import ArchivistCaseMemory
from app.models.checkin import Checkin
from app.models.checkin_followup_preference import CheckinFollowupPreference
from app.models.message import Message, MessageDirection, MessagePurpose, MessageSenderType, MessageStatus
from app.models.knowledge_chunk import KnowledgeChunk
from app.models.knowledge_faq import KnowledgeFAQ
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user import User
from app.models.user_document import DocumentStatus, DocumentType, UserDocument
from app.models.user_profile import UserProfile
from app.models.user_state_profile import UserStateProfile
from app.services.agent.context_builder import build_agent_memory_context
from app.services.agent.graph import AgentReplyResult, AgentRunContext, RanrenAgentGraph
from app.services.agent.skill_registry import SkillRegistry
from app.services.agent.tools.knowledge_tools import SearchSuccessCaseArgs, build_knowledge_tools
from app.services.agent.tools.service_case_tools import build_service_case_tools
from app.services.agent.tools.followup_preference_tools import build_followup_preference_tools
from app.services.agent.tools.web_search_tools import _normalize_current_query_year, build_web_search_tools
from app.services.channels.base import ChannelMessage
from app.services.content_policy import set_ai_reply_enabled
from app.services.message_insight_service import normalize_insight
from app.services.profile_memory_service import load_profile_memory_context
from app.services.ranren_ai.engine import RanrenAIEngine
from app.services.knowledge_index import IndexedKnowledgeHit, IndexedKnowledgeResult
from app.services import knowledge_index as knowledge_index_mod
from app.services.ranren_ai.engine_graph import (
    _insight_recent_messages,
    _knowledge_observation,
    _knowledge_query_from_analysis,
    _reply_run_metadata,
)


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


def test_success_case_tool_keeps_single_rewritten_query_argument():
    fields = SearchSuccessCaseArgs.model_fields
    assert list(fields) == ["query"]
    assert "resolve references and omitted subjects" in (fields["query"].description or "")

    tool = build_knowledge_tools(settings=get_settings())[0]
    assert "Rewrite the search query at call time" in tool.description
    assert "Never add facts" in tool.description


def test_knowledge_query_analysis_preserves_search_skip_and_fallback_states():
    assert _knowledge_query_from_analysis({"knowledge_query": "  晚A怎么喝？  "}) == "晚A怎么喝？"
    assert _knowledge_query_from_analysis({"knowledge_query": None}) == ""
    assert _knowledge_query_from_analysis({}) is None
    assert _knowledge_query_from_analysis({"knowledge_query": "   "}) is None
    assert _knowledge_query_from_analysis({"knowledge_query": ["非法类型"]}) is None


def test_insight_context_keeps_latest_ten_without_shrinking_reply_history():
    reply_history = [f"message-{index:02d}" for index in range(20)]

    insight_history = _insight_recent_messages(reply_history)

    assert insight_history == reply_history[-10:]
    assert len(reply_history) == 20


def test_product_retrieval_keyword_whitelist_covers_failure_anchors():
    from app.services.knowledge_base import _keyword_whitelist, _query_terms

    _keyword_whitelist.cache_clear()
    assert _query_terms("早C冰水冲泡可以吗") == ["冰水", "冲泡", "早C"]
    assert _query_terms("早C和午B可以餐前或餐后喝吗") == [
        "午B",
        "早C",
        "餐前",
        "餐后",
    ]
    assert _query_terms("午B漏喝后能用晚A替代补喝吗") == [
        "午B",
        "晚A",
        "替代",
        "漏喝",
        "补喝",
    ]
    assert _query_terms("先喝产品再吃正餐，还是随餐边吃边喝？") == [
        "边吃边喝",
        "随餐",
    ]
    assert _query_terms("喝完手抖心慌是不是咖啡因不良反应") == [
        "不良反应",
        "咖啡因",
        "心慌",
        "手抖",
    ]
    assert _query_terms("两天没上厕所要不要加通便胶囊") == [
        "没上厕所",
        "胶囊",
        "通便",
    ]


@pytest.mark.asyncio
async def test_keyword_retrieval_ranks_meal_timing_above_product_only_match(db_session):
    timing_chunk = KnowledgeChunk(
        chunk_uid="faq:meal-timing-match",
        source_type="faq",
        source_uri="knowledge_faqs/meal-timing-match",
        title="早C和午B餐前或餐后喝法",
        content="早C和午B优先随餐，餐前或餐后饮用也可以。",
        content_hash="1" * 64,
        status="active",
    )
    product_only_chunk = KnowledgeChunk(
        chunk_uid="faq:product-only-match",
        source_type="faq",
        source_uri="knowledge_faqs/product-only-match",
        title="产品不能替代正餐",
        content="早C和午B不是代餐，不能替代正常饮食。",
        content_hash="2" * 64,
        status="active",
    )
    db_session.add_all([timing_chunk, product_only_chunk])
    await db_session.flush()

    hits = await knowledge_index_mod._retrieve_by_text(
        db_session,
        "早C和午B可以餐前或餐后喝吗",
        top_k=2,
        exclude_source_types={"case"},
    )

    assert hits[0][0] == timing_chunk.id


def test_knowledge_observation_records_decision_hits_and_skipped_product_terms():
    """三态决策 + 召回条数 + 跳过时的产品词探针，都要落到可观测记录里。"""
    searched = _knowledge_observation(
        knowledge_query="经期期间晚A应该如何使用？",
        user_query="那经期呢？",
        hit_count=3,
        hits=[
            {
                "rank": 1,
                "chunk_id": 12,
                "chunk_uid": "faq:8",
                "title": "晚A使用规则",
                "source_type": "faq",
                "updated_at": "2026-08-10T00:00:00+00:00",
                "content_excerpt": "一瓶盖约等于一点五个小刻度。",
            }
        ],
    )
    assert searched == {
        "decision": "searched",
        "original_query": "那经期呢？",
        "query": "经期期间晚A应该如何使用？",
        "hit_count": 3,
        "hits": [
            {
                "rank": 1,
                "chunk_id": 12,
                "chunk_uid": "faq:8",
                "title": "晚A使用规则",
                "source_type": "faq",
                "updated_at": "2026-08-10T00:00:00+00:00",
                "content_excerpt": "一瓶盖约等于一点五个小刻度。",
            }
        ],
    }

    # 查了但库里没有 → hit_count 0，与"根本没查"必须区分开。
    assert _knowledge_observation(
        knowledge_query="卡点钥匙怎么用？", user_query="卡点钥匙咋用", hit_count=0
    )["hit_count"] == 0

    fallback = _knowledge_observation(knowledge_query=None, user_query="我现在第几天？")
    assert fallback == {
        "decision": "fallback",
        "original_query": "我现在第几天？",
        "query": "我现在第几天？",
    }

    # 跳过检索且用户提到产品名 → 这正是删掉 product_usage skill 后最可能漏规则的轮次，必须能捞出来。
    skipped = _knowledge_observation(
        knowledge_query="", user_query="今天体重78.2，午B忘了喝"
    )
    assert skipped == {
        "decision": "skipped",
        "original_query": "今天体重78.2，午B忘了喝",
        "skipped_product_terms": ["午B"],
    }
    # 跳过检索但与产品无关（普通闲聊/上报）→ 不记产品词，避免噪声。
    assert _knowledge_observation(knowledge_query="", user_query="今天心情不错") == {
        "decision": "skipped",
        "original_query": "今天心情不错",
    }
    # 跳过时不记召回条数：没查库，任何数字都是假的。
    assert "hit_count" not in _knowledge_observation(
        knowledge_query="", user_query="好的", hit_count=5
    )

    # 观测字段进 agent_runs.metadata_json；缺 knowledge 的历史结构不应凭空造出该键。
    assert _reply_run_metadata({"data_tool_failed": False, "knowledge": skipped}) == {
        "data_tool_failed": False,
        "knowledge": skipped,
    }
    assert _reply_run_metadata({"data_unavailable": True}) == {"data_tool_failed": True}


def test_knowledge_observation_records_rewrite_zero_hit_fallback():
    observation = _knowledge_observation(
        knowledge_query="改写后问题",
        user_query="包含产品与症状的原问题",
        hit_count=1,
        hits=[{"chunk_id": 8}],
        query_used="包含产品与症状的原问题",
        fallback_used=True,
        primary_hit_count=0,
    )

    assert observation == {
        "decision": "searched",
        "original_query": "包含产品与症状的原问题",
        "query": "改写后问题",
        "hit_count": 1,
        "primary_hit_count": 0,
        "rewrite_fallback_used": True,
        "effective_query": "包含产品与症状的原问题",
        "hits": [{"chunk_id": 8}],
    }


@pytest.mark.asyncio
async def test_indexed_knowledge_result_keeps_ranked_hit_identity_and_content(
    db_session,
    monkeypatch,
):
    faq = KnowledgeFAQ(
        faq_uid="faq-observation-test",
        status="active",
        title="瓶盖和刻度怎么换算",
        question="一瓶盖是多少？",
        answer="一瓶盖约等于一点五个小刻度。",
        version=3,
    )
    db_session.add(faq)
    await db_session.flush()
    faq_chunk = KnowledgeChunk(
        chunk_uid=f"faq:{faq.id}",
        source_type="faq",
        source_uri=f"knowledge_faqs/{faq.id}",
        source_title="参考问答",
        title=faq.title,
        content=faq.answer,
        content_hash="a" * 64,
        metadata_json={"version": 3},
        status="active",
    )
    noise_chunk = KnowledgeChunk(
        chunk_uid="md:observation-noise",
        source_type="markdown",
        source_uri="knowledge_base/noise.md",
        source_title="普通素材",
        title="无关内容",
        content="无关内容",
        content_hash="b" * 64,
        status="active",
    )
    db_session.add_all([faq_chunk, noise_chunk])
    await db_session.flush()

    async def fake_vector(*args, **kwargs):
        return [
            (faq_chunk.id, faq_chunk.content, 0.12),
            (noise_chunk.id, noise_chunk.content, 0.34),
        ]

    async def fake_text(*args, **kwargs):
        return [(faq_chunk.id, faq_chunk.content)]

    monkeypatch.setattr(knowledge_index_mod, "_retrieve_by_embedding", fake_vector)
    monkeypatch.setattr(knowledge_index_mod, "_retrieve_by_text", fake_text)
    result = await knowledge_index_mod.retrieve_indexed_knowledge_result(
        db_session,
        "一瓶盖够吗",
        settings=SimpleNamespace(embedding_api_key="configured", knowledge_base_top_k=2),
        exclude_source_types={"case"},
    )

    assert [hit.chunk_id for hit in result.hits] == [faq_chunk.id, noise_chunk.id]
    assert result.hits[0].title == "瓶盖和刻度怎么换算"
    assert result.hits[0].observation()["chunk_uid"] == f"faq:{faq.id}"
    assert result.hits[0].observation()["content_excerpt"] == faq.answer
    assert "[知识库命中 1]" in result.context


@pytest.mark.asyncio
async def test_rrf_tie_prefers_curated_text_hit_over_vector_only_hit(
    db_session,
    monkeypatch,
):
    vector_chunk = KnowledgeChunk(
        chunk_uid="faq:vector-generic",
        source_type="faq",
        source_uri="knowledge_faqs/vector-generic",
        title="泛化语义命中",
        content="出现不适时先暂停。",
        content_hash="c" * 64,
        status="active",
    )
    text_chunk = KnowledgeChunk(
        chunk_uid="faq:text-exact",
        source_type="faq",
        source_uri="knowledge_faqs/text-exact",
        title="咖啡因手抖边界",
        content="手抖不代表代谢启动。",
        content_hash="d" * 64,
        status="active",
    )
    db_session.add_all([vector_chunk, text_chunk])
    await db_session.flush()

    async def fake_vector(*args, **kwargs):
        return [(vector_chunk.id, vector_chunk.content, 0.25)]

    async def fake_text(*args, **kwargs):
        return [(text_chunk.id, text_chunk.content)]

    monkeypatch.setattr(knowledge_index_mod, "_retrieve_by_embedding", fake_vector)
    monkeypatch.setattr(knowledge_index_mod, "_retrieve_by_text", fake_text)
    result = await knowledge_index_mod.retrieve_indexed_knowledge_result(
        db_session,
        "手抖是不是代谢启动",
        settings=SimpleNamespace(embedding_api_key="configured", knowledge_base_top_k=2),
        exclude_source_types={"case"},
    )

    assert [hit.chunk_id for hit in result.hits] == [
        text_chunk.id,
        vector_chunk.id,
    ]


@pytest.mark.asyncio
async def test_retrieval_observation_records_both_channel_ranks_and_distance(
    db_session,
    monkeypatch,
):
    faq_chunk = KnowledgeChunk(
        chunk_uid="faq:both-channels",
        source_type="faq",
        source_uri="knowledge_faqs/both-channels",
        title="双路命中观测",
        content="午B建议随餐。",
        content_hash="e" * 64,
        metadata_json={"confirm_state": "confirmed"},
        status="active",
    )
    db_session.add(faq_chunk)
    await db_session.flush()

    async def fake_vector(*args, **kwargs):
        return [(faq_chunk.id, faq_chunk.content, 0.123456)]

    async def fake_text(*args, **kwargs):
        return [(faq_chunk.id, faq_chunk.content)]

    monkeypatch.setattr(knowledge_index_mod, "_retrieve_by_embedding", fake_vector)
    monkeypatch.setattr(knowledge_index_mod, "_retrieve_by_text", fake_text)
    result = await knowledge_index_mod.retrieve_indexed_knowledge_result(
        db_session,
        "午B随餐吗",
        settings=SimpleNamespace(embedding_api_key="configured", knowledge_base_top_k=2),
    )

    hit = result.hits[0]
    assert hit.rank == 1
    assert hit.vector_rank == 1
    assert hit.vector_distance == pytest.approx(0.123456)
    assert hit.keyword_rank == 1
    assert hit.confirmation_state == "confirmed"

    observation = hit.observation()
    assert observation["vector_rank"] == 1
    assert observation["vector_distance"] == pytest.approx(0.123456)
    assert observation["keyword_rank"] == 1
    assert observation["confirmation_state"] == "confirmed"


@pytest.mark.asyncio
async def test_retrieval_observation_leaves_missing_channel_fields_empty(
    db_session,
    monkeypatch,
):
    vector_only = KnowledgeChunk(
        chunk_uid="faq:vector-only-obs",
        source_type="faq",
        source_uri="knowledge_faqs/vector-only-obs",
        title="仅向量命中",
        content="出现不适时先暂停。",
        content_hash="f" * 64,
        metadata_json={"confirm_state": "pending"},
        status="active",
    )
    keyword_only = KnowledgeChunk(
        chunk_uid="faq:keyword-only-obs",
        source_type="faq",
        source_uri="knowledge_faqs/keyword-only-obs",
        title="仅关键词命中",
        content="手抖不代表代谢启动。",
        content_hash="a1" * 32,
        metadata_json={"confirm_state": None},
        status="active",
    )
    db_session.add_all([vector_only, keyword_only])
    await db_session.flush()

    async def fake_vector(*args, **kwargs):
        return [(vector_only.id, vector_only.content, 0.321)]

    async def fake_text(*args, **kwargs):
        return [(keyword_only.id, keyword_only.content)]

    monkeypatch.setattr(knowledge_index_mod, "_retrieve_by_embedding", fake_vector)
    monkeypatch.setattr(knowledge_index_mod, "_retrieve_by_text", fake_text)
    result = await knowledge_index_mod.retrieve_indexed_knowledge_result(
        db_session,
        "手抖是不是代谢启动",
        settings=SimpleNamespace(embedding_api_key="configured", knowledge_base_top_k=2),
    )

    by_chunk_id = {hit.chunk_id: hit for hit in result.hits}
    vector_hit = by_chunk_id[vector_only.id]
    keyword_hit = by_chunk_id[keyword_only.id]

    assert vector_hit.vector_rank is not None
    assert vector_hit.vector_distance == pytest.approx(0.321)
    assert vector_hit.keyword_rank is None
    assert vector_hit.confirmation_state == "pending"
    assert keyword_hit.vector_rank is None
    assert keyword_hit.vector_distance is None
    assert keyword_hit.keyword_rank is not None
    assert keyword_hit.confirmation_state is None


@pytest.mark.asyncio
async def test_non_faq_confirmation_state_is_not_recorded(
    db_session,
    monkeypatch,
):
    markdown_chunk = KnowledgeChunk(
        chunk_uid="markdown:observation",
        source_type="markdown",
        source_uri="knowledge_base/observation.md",
        title="普通知识",
        content="普通知识内容。",
        content_hash="b1" * 32,
        # 即使历史数据意外带了 confirm_state，非 FAQ 也不能展示确认状态。
        metadata_json={"confirm_state": "confirmed"},
        status="active",
    )
    db_session.add(markdown_chunk)
    await db_session.flush()

    async def fake_vector(*args, **kwargs):
        return [(markdown_chunk.id, markdown_chunk.content, 0.2)]

    async def fake_text(*args, **kwargs):
        return [(markdown_chunk.id, markdown_chunk.content)]

    monkeypatch.setattr(knowledge_index_mod, "_retrieve_by_embedding", fake_vector)
    monkeypatch.setattr(knowledge_index_mod, "_retrieve_by_text", fake_text)
    result = await knowledge_index_mod.retrieve_indexed_knowledge_result(
        db_session,
        "普通知识",
        settings=SimpleNamespace(embedding_api_key="configured", knowledge_base_top_k=2),
    )

    hit = result.hits[0]
    assert hit.confirmation_state is None
    assert "confirmation_state" not in hit.observation()


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


@pytest.mark.asyncio
async def test_reference_qa_uses_knowledge_query_and_falls_back_to_user_query(
    db_session,
    monkeypatch,
):
    service_case = await _case(db_session)
    retrieved_queries: list[str] = []

    async def fake_retrieve(
        session,
        query,
        *,
        settings=None,
        exclude_source_types=None,
    ):
        retrieved_queries.append(query)
        assert exclude_source_types == {"case"}
        hits = [
            IndexedKnowledgeHit(
                rank=index,
                chunk_id=index,
                chunk_uid=f"faq:{index}",
                title=f"参考问答 {index}",
                source_type="faq",
                updated_at=None,
                content=content,
            )
            for index, content in enumerate(("参考问答命中", "另一条"), start=1)
        ]
        return IndexedKnowledgeResult(
            context="\n\n".join(
                f"[知识库命中 {hit.rank}]\n{hit.content}" for hit in hits
            ),
            hits=hits,
        )

    monkeypatch.setattr(
        "app.services.agent.graph.retrieve_indexed_knowledge_result",
        fake_retrieve,
    )

    for knowledge_query, expected_query in (
        ("经期期间晚A应该如何使用？", "经期期间晚A应该如何使用？"),
        (None, "那经期呢？"),
    ):
        model = FakeToolCallingModel([AIMessage(content="正常回复")])
        result = await RanrenAgentGraph(
            context=AgentRunContext(
                service_case_id=service_case.id,
                trigger_id=123,
            ),
            settings=get_settings(),
            chat_model=model,
        ).run(
            user_query="那经期呢？",
            knowledge_query=knowledge_query,
            recent_messages=["用户: 晚A什么时候喝？", "助教: 一般按执行方案安排。"],
        )

        assert result.reply == "正常回复"
        prompt = model.calls[0][1].content
        assert "【本次提问】\n用户：那经期呢？" in prompt
        assert retrieved_queries[-1] == expected_query
        # 观测：searched / fallback 两种都查了库，召回条数按命中标记计数。
        assert result.knowledge_hit_count == 2
        assert [hit["chunk_id"] for hit in result.knowledge_hits] == [1, 2]

    assert retrieved_queries == ["经期期间晚A应该如何使用？", "那经期呢？"]

    skip_model = FakeToolCallingModel([AIMessage(content="<no_reply>")])
    skip_result = await RanrenAgentGraph(
        context=AgentRunContext(
            service_case_id=service_case.id,
            trigger_id=124,
        ),
        settings=get_settings(),
        chat_model=skip_model,
    ).run(
        user_query="好的",
        knowledge_query="",
        recent_messages=[],
    )

    assert skip_result.reply == "<no_reply>"
    assert retrieved_queries == ["经期期间晚A应该如何使用？", "那经期呢？"]
    assert "【参考问答】\n暂无相关参考问答。" in skip_model.calls[0][1].content
    # 观测：跳过检索时召回条数必须是 None，不能是 0——"根本没查"和"查了没命中"是两种病。
    assert skip_result.knowledge_hit_count is None


@pytest.mark.asyncio
async def test_agent_retries_empty_final_content_once(db_session, monkeypatch):
    service_case = await _case(db_session)
    model = FakeToolCallingModel(
        [
            AIMessage(content=""),
            AIMessage(content="三点已超过时间，今天跳过午B，不要补喝。"),
        ]
    )
    usage_records = []

    async def fake_record_model_usage(**payload):
        usage_records.append(payload)

    monkeypatch.setattr(
        "app.services.agent.graph.record_model_usage",
        fake_record_model_usage,
    )

    result = await RanrenAgentGraph(
        context=AgentRunContext(
            service_case_id=service_case.id,
            trigger_id=125,
            agent_run_id=999,
        ),
        settings=get_settings(),
        chat_model=model,
    ).run(
        user_query="今天水喝够2升了，不过午B拖到三点还能补吗？",
        knowledge_query="",
        recent_messages=[],
    )

    assert result.reply == "三点已超过时间，今天跳过午B，不要补喝。"
    assert len(model.calls) == 2
    assert [item["success"] for item in usage_records] == [False, True]
    assert usage_records[0]["error_type"] == "EmptyAgentReply"
    assert usage_records[1]["error_type"] is None


@pytest.mark.asyncio
async def test_agent_stops_after_one_empty_final_retry(db_session, monkeypatch):
    service_case = await _case(db_session)
    model = FakeToolCallingModel(
        [AIMessage(content=""), AIMessage(content=""), AIMessage(content="不应调用")]
    )
    usage_records = []

    async def fake_record_model_usage(**payload):
        usage_records.append(payload)

    monkeypatch.setattr(
        "app.services.agent.graph.record_model_usage",
        fake_record_model_usage,
    )

    with pytest.raises(RuntimeError, match="Agent returned empty reply"):
        await RanrenAgentGraph(
            context=AgentRunContext(
                service_case_id=service_case.id,
                trigger_id=126,
                agent_run_id=1000,
            ),
            settings=get_settings(),
            chat_model=model,
        ).run(
            user_query="午B拖到三点还能补吗？",
            knowledge_query="",
            recent_messages=[],
        )

    assert len(model.calls) == 2
    assert [item["success"] for item in usage_records] == [False, False]
    assert {item["error_type"] for item in usage_records} == {"EmptyAgentReply"}


@pytest.mark.asyncio
async def test_reference_qa_retries_original_query_only_after_rewrite_zero_hits(
    db_session,
    monkeypatch,
):
    service_case = await _case(db_session)
    queries: list[str] = []

    async def fake_retrieve(
        session,
        query,
        *,
        settings=None,
        exclude_source_types=None,
    ):
        queries.append(query)
        if query == "经期肚子不舒服":
            return IndexedKnowledgeResult(context="", hits=[])
        hit = IndexedKnowledgeHit(
            rank=1,
            chunk_id=8,
            chunk_uid="faq:8",
            title="午B身体反应处理",
            source_type="faq",
            updated_at=None,
            content="如果怀疑与午B使用有关，应先暂停并联系班主任确认。",
        )
        return IndexedKnowledgeResult(
            context=f"[知识库命中 1]\n{hit.content}",
            hits=[hit],
        )

    monkeypatch.setattr(
        "app.services.agent.graph.retrieve_indexed_knowledge_result",
        fake_retrieve,
    )
    model = FakeToolCallingModel([AIMessage(content="先暂停午B，我帮你联系班主任确认。")])

    result = await RanrenAgentGraph(
        context=AgentRunContext(service_case_id=service_case.id, trigger_id=125),
        settings=get_settings(),
        chat_model=model,
    ).run(
        user_query="午B喝完后经期肚子不舒服，是它导致的吗？",
        knowledge_query="经期肚子不舒服",
        recent_messages=[],
    )

    assert queries == [
        "经期肚子不舒服",
        "午B喝完后经期肚子不舒服，是它导致的吗？",
    ]
    assert "如果怀疑与午B使用有关" in model.calls[0][1].content
    assert result.knowledge_hit_count == 1
    assert result.knowledge_primary_hit_count == 0
    assert result.knowledge_fallback_used is True
    assert result.knowledge_query_used == "午B喝完后经期肚子不舒服，是它导致的吗？"


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
    assert "checkin_analysis" in catalog
    assert "interest_companion" in catalog
    # 产品使用规则已迁到 RAG 参考问答，不再有 product_usage skill。
    assert "product_usage" not in catalog

    skill = registry.read_skill("checkin_analysis")
    assert "get_weight_trend" in skill

    with pytest.raises(ValueError):
        registry.read_skill("product_usage")

    with pytest.raises(ValueError):
        registry.read_skill_file("checkin_analysis", "../interest_companion/SKILL.md")


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
    labels = {item["topic"]: item["label"] for item in initial["preferences"]}
    assert labels["milestone_review"] == "次晨空腹打卡提醒"

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
    assert "parsed" not in item
    assert result["complete_checkin_days"] == 0
    assert item["is_complete"] is False
    assert item["counts_toward_complete_total"] is False
    assert item["complete_items"] == []
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
async def test_recent_checkins_returns_authoritative_total_and_exact_complete_items(db_session):
    settings = get_settings()
    service_case = await _case(db_session)
    service_case.service_plan_start_date = date.today()
    db_session.add(
        Checkin(
            service_case_id=service_case.id,
            group_id=service_case.group_id,
            checkin_date=date.today(),
            content="早C喝了，午B没喝，晚A暂停；其余四项已回答",
            parsed_json={
                "product_schedule": {
                    "morning_c": "已喝",
                    "noon_b": "未喝",
                    "night_a": "暂停",
                },
                "custom_checkin": {
                    "default:v1:2": {"question": "蔬菜两拳", "answer": "完成"},
                    "default:v1:3": {"question": "快走25分钟", "answer": "未完成"},
                    "default:v1:4": {"question": "23:30前上床", "answer": "完成"},
                    "default:v1:5": {"question": "情绪评分", "answer": "4分"},
                },
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

    result = await tools["get_recent_checkins"].ainvoke(
        {
            "days": 1,
            "date_from": date.today().isoformat(),
            "date_to": date.today().isoformat(),
        }
    )

    assert result["complete_checkin_days"] == 1
    assert result["has_more"] is False
    item = result["items"][0]
    assert item["is_complete"] is True
    assert item["counts_toward_complete_total"] is True
    assert [entry["question"] for entry in item["complete_items"]] == [
        "早C、午B、晚A",
        "蔬菜两拳",
        "快走25分钟",
        "23:30前上床",
        "情绪评分",
    ]
    assert item["complete_items"][0]["answer"] == "早C：已喝；午B：未喝；晚A：暂停"


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
    assert first["document_type_label"] == "个人能量重启诊断报告"
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
async def test_case_summary_survives_intervention_normalization_into_agent_prompt(db_session):
    service_case = await _case(db_session)
    db_session.add(
        UserProfile(
            service_case_id=service_case.id,
            profile_json={"summary": "稳定画像不应出现在本轮提示词。"},
            summary="稳定画像不应出现在本轮提示词。",
            generated_by_model="test",
        )
    )
    db_session.add(
        ArchivistCaseMemory(
            service_case_id=service_case.id,
            summary="第一周午后容易饿，午餐增加蛋白质后有所改善。",
        )
    )
    await db_session.flush()

    analysis = normalize_insight(
        {
            "requires_human_intervention": False,
            "risk_flags": [],
            "intervention_reasons": [],
            "agent_context_plan": {
                "message_scene": "body_feedback",
                "memory_scopes": ["case_summary", "unknown_scope"],
                "tool_hints": [],
                "reason": "当前反馈需要结合此前尝试判断。",
            },
        },
        source="chat_message",
        content="这两天下午又容易饿了",
    )
    assert analysis["agent_context_plan"]["memory_scopes"] == ["case_summary"]

    model = FakeToolCallingModel([AIMessage(content="先看看最近午餐的蛋白质和总量有没有减少。")])
    result = await RanrenAgentGraph(
        context=AgentRunContext(
            service_case_id=service_case.id,
            trigger_id=123,
            agent_context_plan=analysis["agent_context_plan"],
        ),
        settings=get_settings(),
        chat_model=model,
    ).run(user_query="这两天下午又容易饿了", recent_messages=[])

    assert result.reply == "先看看最近午餐的蛋白质和总量有没有减少。"
    final_user_prompt = model.calls[0][1].content
    assert "【隐性用户背景】" in final_user_prompt
    assert "历程小结：第一周午后容易饿，午餐增加蛋白质后有所改善。" in final_user_prompt
    assert "稳定画像不应出现在本轮提示词" not in final_user_prompt


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
    assert "直接回应当前问题，不表演、不绕弯" in model.calls[0][0].content
    assert "让我想想" in model.calls[0][0].content
    assert "根据搜索结果" in model.calls[0][0].content
    assert "不要说“根据你的画像/资料/系统记录”" in model.calls[0][0].content
    assert "群昵称行是工作人员发言" in model.calls[0][0].content
    assert "不当作用户自述" in model.calls[0][0].content
    assert "只用与当前问题直接相关的部分" in model.calls[0][0].content
    assert "只参考长期偏好" in model.calls[0][0].content
    assert "只参考当前状态/风险边界/产品执行" in model.calls[0][0].content
    assert "实时数据一律查工具" in model.calls[0][0].content
    assert "这是群聊接话不是考试答题" in model.calls[0][0].content
    assert "先追问一个关键点并停下" in model.calls[0][0].content
    assert "不确定就直说，不编细节" in model.calls[0][0].content
    assert "有问题时只回答当前一问，普通回复通常 1～3 句" in model.calls[0][0].content
    assert "健康、饮食、运动和产品问题用“结论 + 一条必要依据或安全边界”" in model.calls[0][0].content
    assert "不展开机制、搭配、备选方案和后续注意事项" in model.calls[0][0].content
    assert "不先分析原因、补全场景或给方案" in model.calls[0][0].content
    assert "不能用“可能/通常/大概率”绕过追问" in model.calls[0][0].content
    assert "回复优先级：准确完整 > 专业克制 > 简洁自然" not in model.calls[0][0].content
    assert "不为简短省略关键信息" not in model.calls[0][0].content
    assert "用户质疑旧回复时先复核，错了直接更正，不补理由" in model.calls[0][0].content
    assert "这句说反了。应该是动作稳定、恢复正常且仍有余力时再逐步加量" in model.calls[0][0].content
    assert "行，你先问问，看谁有空一起" in model.calls[0][0].content
    assert "这顿除了杂粮米，还吃了什么菜和蛋白质" in model.calls[0][0].content
    assert "“算了”→" not in model.calls[0][0].content
    assert "“今天第几天”→" not in model.calls[0][0].content
    assert "刚吃完先别跑，走路回去更稳妥" in model.calls[0][0].content
    assert "先把具体感受分清，不能直接归因" in model.calls[0][0].content
    assert "不要一次把话题讲完整" not in model.calls[0][0].content
    assert "对方延续前文时接前文梗" not in model.calls[0][0].content
    assert "20-60字" not in model.calls[0][0].content
    assert "60-140字" not in model.calls[0][0].content
    assert "零称呼起头" in model.calls[0][0].content
    assert "哥、姐、宝子、亲、阿姨" in model.calls[0][0].content
    assert "你说 LABUBU 后面那个？" in model.calls[0][0].content
    assert "咒术回战：节奏快" in model.calls[0][0].content
    assert model.calls[0][1].content.rfind("【本次提问】") > model.calls[0][1].content.rfind("【可用 Skill】")
    assert "今天没排便体重还涨了" not in model.calls[0][0].content
    assert "晚餐能吃荞麦面吗" not in model.calls[0][0].content


@pytest.mark.asyncio
async def test_shared_agent_graph_isolates_concurrent_service_case_bindings(db_session):
    settings = get_settings()
    first_case = await _case(db_session)
    second_case = await _case(db_session)
    arrivals: set[str] = set()
    both_arrived = asyncio.Event()

    class InterleavingModel(FakeToolCallingModel):
        def __init__(self, label: str):
            super().__init__([])
            self.label = label
            self.call_count = 0

        async def ainvoke(self, messages):
            self.calls.append(list(messages))
            self.call_count += 1
            if self.call_count == 1:
                arrivals.add(self.label)
                if len(arrivals) == 2:
                    both_arrived.set()
                await asyncio.wait_for(both_arrived.wait(), timeout=1)
                return AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "get_service_progress",
                            "args": {},
                            "id": f"call-{self.label}",
                        }
                    ],
                )
            return AIMessage(content=f"reply-{self.label}")

    first_model = InterleavingModel("case-a")
    second_model = InterleavingModel("case-b")
    first_graph = RanrenAgentGraph(
        context=AgentRunContext(service_case_id=first_case.id, trigger_id=101),
        settings=settings,
        chat_model=first_model,
    )
    second_graph = RanrenAgentGraph(
        context=AgentRunContext(service_case_id=second_case.id, trigger_id=202),
        settings=settings,
        chat_model=second_model,
    )
    first_tool = FakeAsyncTool({"ok": True, "case_id": first_case.id})
    second_tool = FakeAsyncTool({"ok": True, "case_id": second_case.id})
    first_graph.tools_by_name["get_service_progress"] = first_tool
    second_graph.tools_by_name["get_service_progress"] = second_tool

    assert first_graph.graph is second_graph.graph
    first_result, second_result = await asyncio.gather(
        first_graph.run(user_query="A 的进度", recent_messages=[]),
        second_graph.run(user_query="B 的进度", recent_messages=[]),
    )

    assert first_result.reply == "reply-case-a"
    assert second_result.reply == "reply-case-b"
    assert first_result.tool_trace[0]["name"] == "get_service_progress"
    assert second_result.tool_trace[0]["name"] == "get_service_progress"
    assert len(first_tool.calls) == 1
    assert len(second_tool.calls) == 1
    assert first_model.call_count == 2
    assert second_model.call_count == 2


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
        *, service_case_id, trigger_id, user_query, knowledge_query, query_speaker, recent_messages, analysis,
        channel=None, group_id=None, bot_user_id=None, settings, agent_run_id=None
    ):
        calls.append(service_case_id)
        assert agent_run_id is not None
        assert knowledge_query == "当前服务进行到第几天？"
        assert query_speaker == "用户"
        assert channel == "qiwe"
        assert analysis == {
            "requires_human_intervention": False,
            "knowledge_query": "当前服务进行到第几天？",
        }
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
            analysis={
                "requires_human_intervention": False,
                "knowledge_query": "当前服务进行到第几天？",
            },
            trigger_id=inbound.id,
        )
        assert calls == [service_case.id]
    finally:
        await set_ai_reply_enabled(True, db_session)
