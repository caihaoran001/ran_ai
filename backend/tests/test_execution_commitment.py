from datetime import datetime, timezone

import pytest

from app.services.execution_commitment import (
    agent_execution_context,
    apply_commitment_choice,
    initialize_execution_commitment,
    minimum_complete_days,
    review_execution_commitment,
)


def _state(*, candidate: bool = True) -> dict:
    return initialize_execution_commitment(
        {
            "high_commitment_candidate": candidate,
            "high_commitment_basis": ["满足"],
            "basic_target_jin_or_range": "28天减重5-7斤",
            "challenge_target_jin_or_review_text": "28天向8-10斤推进",
        }
    )


def _choose(state: dict, choice: str) -> dict:
    return apply_commitment_choice(
        state,
        choice=choice,
        source_message_id=123,
        source_text=f"选择{choice}",
        occurred_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        sender_id="user-1",
        sender_name="用户",
    )


def test_cumulative_threshold_days_round_up():
    assert [minimum_complete_days(day, 0.60) for day in (7, 14, 21, 28)] == [5, 9, 13, 17]
    assert [minimum_complete_days(day, 0.85) for day in (7, 14, 21, 28)] == [6, 12, 18, 24]


def test_agent_context_blocks_selection_before_invitation():
    context = agent_execution_context(_state())
    assert "当前执行档位：待确认，尚未生效" in context
    assert "邀请未发出或未成功，不询问用户选档" in context
    assert "高承诺资格：通过" in context


def test_agent_context_allows_selection_only_after_invitation():
    state = _state()
    state["invitation"] = {"status": "sent", "message_id": 456}
    context = agent_execution_context(state)
    assert "邀请已发出，等待用户明确选择" in context


def test_agent_context_does_not_repeat_selection_after_confirmation():
    state = _choose(_state(), "high_commitment")
    state["invitation"] = {"status": "sent", "message_id": 456}
    context = agent_execution_context(state)
    assert "已确认，不主动重复追问选档" in context


def test_execution_commitment_faq_specs_cover_approved_rules():
    from app.services.execution_commitment_knowledge import (
        load_execution_commitment_faq_specs,
    )

    source_type, specs = load_execution_commitment_faq_specs()
    user_visible = "\n".join(
        f"{item['title']}\n{item['question']}\n{item['answer']}" for item in specs
    )

    assert source_type == "manual_curated"
    assert len(specs) == 5
    assert "标准档：服务第7天至少5天、第14天至少9天、第21天至少13天、第28天至少17天" in user_visible
    assert "高承诺档：服务第7天至少6天、第14天至少12天、第21天至少18天、第28天至少24天" in user_visible
    assert "服务第14天统计第1至第14天" in user_visible
    assert "不是断一天就失败" in user_visible
    assert "未收到邀请时机器人只能解释规则，不能催选" in user_visible
    assert not any(alias in user_visible for alias in ("D0", "D1", "D7", "D14", "D21", "D28"))
    # 「断一天算不算失败」必须有独立问法承载：混进长问句会被语义稀释，检索不到。
    assert "断一天不等于失败" in user_visible
    assert any("我断了一天是不是就失败了" in item["question"] for item in specs)


@pytest.mark.asyncio
async def test_execution_commitment_faqs_retrieve_for_rule_followups(db_session):
    from types import SimpleNamespace

    from app.models.knowledge_chunk import KnowledgeChunk
    from app.models.knowledge_faq import KnowledgeFAQ
    from app.services.execution_commitment_knowledge import (
        load_execution_commitment_faq_specs,
    )
    from app.services.knowledge_index import retrieve_indexed_knowledge_result
    from sqlalchemy import select

    _, specs = load_execution_commitment_faq_specs()
    active_faqs = list(
        await db_session.scalars(
            select(KnowledgeFAQ).where(KnowledgeFAQ.status == "active")
        )
    )
    for faq in active_faqs:
        faq.status = "disabled"

    created_faqs: list[KnowledgeFAQ] = []
    created_chunks: list[KnowledgeChunk] = []
    try:
        for index, spec in enumerate(specs, start=1):
            faq = KnowledgeFAQ(
                faq_uid=f"execution-faq-{index}",
                status="active",
                title=spec["title"],
                question=spec["question"],
                answer=spec["answer"],
                category=spec["category"],
                tags=spec["tags"],
                version=1,
            )
            db_session.add(faq)
            await db_session.flush()
            created_faqs.append(faq)
            chunk = KnowledgeChunk(
                chunk_uid=f"faq:{faq.id}",
                source_type="faq",
                source_uri=f"knowledge_faq:{faq.id}",
                title=faq.title,
                content=f"## {faq.title}\n\n{faq.question}\n\n{faq.answer}",
                content_hash=f"{index:064x}",
                status="active",
            )
            db_session.add(chunk)
            created_chunks.append(chunk)
        await db_session.flush()

        settings = SimpleNamespace(embedding_api_key="", knowledge_base_top_k=3)
        probes = {
            "高承诺档能自由选吗": "高承诺档的筛选、邀请与确认",
            "服务第14天要完整打卡几天才达标": "标准档和高承诺档的达标天数",
            "D14要完整打卡几天才达标": "标准档和高承诺档的达标天数",
            "断一天是不是就不达标": "标准档和高承诺档的达标天数",
            "服务第7天打卡率不够会怎样": "复盘节点的保留与降档规则",
            "D7打卡率不够会怎样": "复盘节点的保留与降档规则",
        }
        for query, expected_title in probes.items():
            result = await retrieve_indexed_knowledge_result(
                db_session,
                query,
                settings=settings,
                exclude_source_types={"case"},
            )
            assert expected_title in {hit.title for hit in result.hits}
            assert all(
                not any(alias in hit.content for alias in ("D0", "D1", "D7", "D14", "D21", "D28"))
                for hit in result.hits
            )
    finally:
        for chunk in created_chunks:
            await db_session.delete(chunk)
        for faq in created_faqs:
            await db_session.delete(faq)
        for faq in active_faqs:
            faq.status = "active"
        await db_session.flush()


@pytest.mark.asyncio
async def test_execution_commitment_faq_publish_is_idempotent(db_session, monkeypatch):
    from app.services import knowledge_faq_service
    from app.services.execution_commitment_knowledge import (
        load_execution_commitment_faq_specs,
        upsert_execution_commitment_faqs,
    )

    synced: list[int] = []

    async def fake_sync(_session, faq):
        synced.append(faq.id)

    monkeypatch.setattr(knowledge_faq_service, "sync_faq_to_index", fake_sync)
    _, specs = load_execution_commitment_faq_specs()
    expected = len(specs)
    first = await upsert_execution_commitment_faqs(db_session)
    synced_after_first = len(synced)
    second = await upsert_execution_commitment_faqs(db_session)

    assert len(first) == len(second) == expected
    assert [faq.id for faq in first] == [faq.id for faq in second]
    # 幂等的判据是"重复发布不再改动"，不是"版本恒等于1"：库里已存在旧口径时，
    # 首次发布本来就该升版本，只有第二次必须原地不动。
    assert [faq.version for faq in first] == [faq.version for faq in second]
    assert all(faq.confirmation_state == "confirmed" for faq in second)
    # 第二次发布内容已一致，不应再写库、也不该重新同步索引。
    # 首次同步次数取决于库内既有状态，不能写死。
    assert len(synced) == synced_after_first


def test_high_commitment_below_85_downgrades_to_standard():
    state = _choose(_state(), "high_commitment")
    updated, message = review_execution_commitment(
        state,
        checkpoint=14,
        complete_days=10,
        occurred_at=datetime(2026, 9, 15, tzinfo=timezone.utc),
    )
    assert updated["execution_path"] == "standard"
    assert updated["status"] == "active"
    assert "10/14天" in updated["high_commitment_downgrade_reason"]
    assert "调整为标准档" in (message or "")


def test_below_standard_reasks_user_instead_of_silently_pausing():
    state = _choose(_state(), "standard")
    updated, message = review_execution_commitment(
        state,
        checkpoint=7,
        complete_days=4,
        occurred_at=datetime(2026, 9, 8, tzinfo=timezone.utc),
    )
    assert updated["status"] == "reconfirming"
    assert "4天完整打卡" in (message or "")
    assert "继续按标准目标推进" in (message or "")
    assert "先按目前能做到的节奏稳住" in (message or "")


def test_current_pace_pauses_target_until_explicit_recommitment():
    paused = _choose(_state(), "current_pace")
    assert paused["status"] == "paused"
    resumed = _choose(paused, "standard")
    assert resumed["status"] == "active"
    assert resumed["execution_path"] == "standard"


def test_non_candidate_cannot_choose_high_commitment():
    with pytest.raises(ValueError, match="不具备高承诺档资格"):
        _choose(_state(candidate=False), "high_commitment")


def test_render_commitment_invitation_merges_checkin_examples():
    from app.services.execution_commitment import render_commitment_invitation

    state = _state(candidate=True)
    examples = [
        "1. 早C/午B/晚A｜早C已喝；午B已喝；晚A未完成",
        "2. 蔬菜总量｜已吃够2拳",
        "3. 运动步数｜快走30分钟",
        "4. 睡前收口｜22:30后未吃东西",
        "5. 晨起体重｜62.5kg",
    ]
    text = render_commitment_invitation(state, checkin_example_lines=examples)
    assert "【每日打卡】" in text
    assert "早C已喝" in text
    assert "高承诺档" in text
    assert "完整打卡率需达到85%" in text
