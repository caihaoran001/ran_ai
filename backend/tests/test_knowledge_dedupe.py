from __future__ import annotations

import json

import pytest

from app.models.knowledge_draft import KnowledgeDraft
from app.services.knowledge_base import _knowledge_files
from app.services.knowledge_dedupe import (
    KnowledgeCandidate,
    _judge_candidates,
    normalize_knowledge_text,
    normalized_content_hash,
)
from app.services.knowledge_evolution import KnowledgeEvolutionService


class StubAI:
    def __init__(self, payload: dict):
        self.payload = payload

    async def generate_text(self, **_: object) -> str:
        return json.dumps(self.payload, ensure_ascii=False)


def _draft(*, draft_type: str = "qa") -> KnowledgeDraft:
    return KnowledgeDraft(
        id=101,
        draft_type=draft_type,
        status="pending",
        title="晚A饮用时间",
        question="晚A应该什么时候喝？",
        answer="睡前一到两小时喝完。",
        category="产品使用",
        tags=["晚A"],
        source_message_ids=[11, 12],
        content_json={"service_case_id": 7} if draft_type == "case" else {},
        content_hash="unused",
    )


def _candidate(*, draft_type: str = "qa") -> KnowledgeCandidate:
    return KnowledgeCandidate(
        chunk_id=9,
        item_id=3,
        draft_type=draft_type,
        title="晚A时间",
        question="晚A什么时候喝？",
        answer="晚餐时喝。",
        category="产品使用",
        tags=["晚A"],
        distance=0.12,
        source_message_ids=[11, 13] if draft_type == "case" else [],
        service_case_id=7 if draft_type == "case" else None,
    )


def test_exact_hash_only_normalizes_formatting() -> None:
    assert normalize_knowledge_text("　晚Ａ\n  怎么喝？ ") == "晚A 怎么喝?"
    assert normalized_content_hash("qa", "晚Ａ\n怎么喝？", "睡前喝") == normalized_content_hash(
        "qa",
        "晚A 怎么喝?",
        "睡前喝",
    )
    assert normalized_content_hash("qa", "晚A怎么喝？", "睡前喝") != normalized_content_hash(
        "qa",
        "晚A怎么喝？",
        "随晚餐喝",
    )


@pytest.mark.asyncio
async def test_semantic_judge_returns_one_call_merged_canonical_answer() -> None:
    decision = await _judge_candidates(
        _draft(),
        [_candidate()],
        ai=StubAI(
            {
                "matched_chunk_id": 9,
                "relation": "same_meaning",
                "answer_relation": "candidate_adds_value",
                "reason": "问法一致，新答案补齐了时间边界。",
                "canonical_question": "晚A应该什么时候喝？",
                "canonical_answer": "晚A在睡前1至2小时喝完；如方案另有安排，以审核后的方案为准。",
            }
        ),
    )
    assert decision.action == "update"
    assert decision.target is not None
    assert decision.canonical_answer == "晚A在睡前1至2小时喝完;如方案另有安排,以审核后的方案为准。"


@pytest.mark.asyncio
async def test_conditional_variant_is_published_separately() -> None:
    decision = await _judge_candidates(
        _draft(),
        [_candidate()],
        ai=StubAI(
            {
                "matched_chunk_id": None,
                "relation": "conditional_variant",
                "answer_relation": "not_applicable",
                "reason": "新问题限定了咖啡因敏感人群，适用条件不同。",
                "canonical_question": None,
                "canonical_answer": None,
            }
        ),
    )
    assert decision.action == "create"
    assert decision.target is None


def test_qa_extraction_prompt_does_not_force_emotional_fluff() -> None:
    prompt = KnowledgeEvolutionService._qa_extraction_prompt()
    assert "禁止为了显得温暖而强行安慰" in prompt
    assert "只有用户原问题本身是焦虑" in prompt
    assert "首句必须共情" not in prompt
    assert "必须赞美和表扬" not in prompt


def test_legacy_markdown_manifest_is_fail_closed(tmp_path) -> None:
    from app.core.config import Settings

    (tmp_path / "legacy.md").write_text("# old", encoding="utf-8")
    (tmp_path / "knowledge_sources.json").write_text(
        json.dumps({"active": [], "retained_legacy": ["legacy.md"]}),
        encoding="utf-8",
    )
    settings = Settings(KNOWLEDGE_BASE_DIR=str(tmp_path))
    assert _knowledge_files(settings) == []
