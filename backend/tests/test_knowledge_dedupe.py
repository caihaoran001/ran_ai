from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.models.knowledge_draft import KnowledgeDraft
from app.services.knowledge_base import _knowledge_files
from app.services.knowledge_dedupe import (
    KnowledgeCandidate,
    KnowledgeDedupeUnavailable,
    _judge_candidates,
    assess_knowledge_draft,
    knowledge_draft_idempotency_hash,
    normalize_knowledge_text,
)


class StubAI:
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls = 0

    async def generate_text(self, **_: object) -> str:
        self.calls += 1
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


def test_draft_idempotency_is_scoped_to_source_evidence() -> None:
    assert normalize_knowledge_text("　晚Ａ\n  怎么喝？ ") == "晚A 怎么喝?"
    first = knowledge_draft_idempotency_hash(
        "qa",
        "晚Ａ\n怎么喝？",
        "睡前喝",
        source_message_ids=[11, 12],
        sanitized_text="用户问晚A怎么喝",
        content_json={"scan_type": "archivist_delta", "service_case_id": 7},
    )
    replay = knowledge_draft_idempotency_hash(
        "qa",
        "晚A 怎么喝?",
        "睡前喝",
        source_message_ids=[12, 11],
        sanitized_text="用户问晚A怎么喝",
        content_json={"scan_type": "archivist_delta", "service_case_id": 7},
    )
    other_evidence = knowledge_draft_idempotency_hash(
        "qa",
        "晚A 怎么喝?",
        "睡前喝",
        source_message_ids=[21, 22],
        sanitized_text="另一位用户问晚A怎么喝",
        content_json={"scan_type": "archivist_delta", "service_case_id": 8},
    )
    assert replay == first
    assert other_evidence != first


@pytest.mark.asyncio
async def test_approval_does_not_shortcut_identical_text_with_hash(monkeypatch) -> None:
    draft = _draft()
    identical = replace(
        _candidate(),
        question=draft.question,
        answer=draft.answer,
        distance=0.0,
    )

    async def fake_candidates(*_args, **_kwargs):
        return [identical]

    monkeypatch.setattr(
        "app.services.knowledge_dedupe._find_semantic_candidates",
        fake_candidates,
    )
    ai = StubAI(
        {
            "assessments": [
                {
                    "chunk_id": identical.chunk_id,
                    "relation": "same_meaning",
                    "answer_relation": "equivalent",
                    "reason": "内容一致，无新增信息。",
                    "canonical_question": None,
                    "canonical_answer": None,
                }
            ]
        }
    )
    decision = await assess_knowledge_draft(None, draft, ai=ai)
    assert decision.action == "skip"
    assert ai.calls == 1


@pytest.mark.asyncio
async def test_semantic_judge_returns_one_call_merged_canonical_answer() -> None:
    decision = await _judge_candidates(
        _draft(),
        [_candidate()],
        ai=StubAI(
            {
                "assessments": [
                    {
                        "chunk_id": 9,
                        "relation": "same_meaning",
                        "answer_relation": "candidate_adds_value",
                        "reason": "问法一致，新答案补齐了时间边界。",
                        "canonical_question": "晚A应该什么时候喝？",
                        "canonical_answer": "晚A在睡前1至2小时喝完；如方案另有安排，以审核后的方案为准。",
                    }
                ]
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
                "assessments": [
                    {
                        "chunk_id": 9,
                        "relation": "conditional_variant",
                        "answer_relation": "not_applicable",
                        "reason": "新问题限定了咖啡因敏感人群，适用条件不同。",
                        "canonical_question": None,
                        "canonical_answer": None,
                    }
                ]
            }
        ),
    )
    assert decision.action == "create"
    assert decision.target is None


@pytest.mark.asyncio
@pytest.mark.parametrize("relation", ["conditional_variant", "related"])
async def test_answer_conflict_always_stops_publication(relation: str) -> None:
    decision = await _judge_candidates(
        _draft(),
        [_candidate()],
        ai=StubAI(
            {
                "assessments": [
                    {
                        "chunk_id": 9,
                        "relation": relation,
                        "answer_relation": "conflict",
                        "reason": "新答案说可以补喝，已有答案要求跳过，时间规则直接冲突。",
                        "canonical_question": None,
                        "canonical_answer": None,
                    }
                ]
            }
        ),
    )
    assert decision.action == "conflict"
    assert decision.target is not None
    assert decision.target.chunk_id == 9


@pytest.mark.asyncio
async def test_answer_conflict_without_known_target_fails_closed() -> None:
    with pytest.raises(KnowledgeDedupeUnavailable, match="未知候选"):
        await _judge_candidates(
            _draft(),
            [_candidate()],
            ai=StubAI(
                {
                    "assessments": [
                        {
                            "chunk_id": 4242,
                            "relation": "related",
                            "answer_relation": "conflict",
                            "reason": "两个时间口径直接冲突。",
                            "canonical_question": None,
                            "canonical_answer": None,
                        }
                    ]
                }
            ),
        )


@pytest.mark.asyncio
async def test_conflict_on_any_candidate_beats_non_conflict_verdicts() -> None:
    """RA-030 回归：多候选里只要一条冲突，就不能被"条件变体"盖过去而独立入库。"""

    near_variant = replace(_candidate(), chunk_id=11, distance=0.14)
    far_conflict = replace(_candidate(), chunk_id=22, distance=0.28)
    decision = await _judge_candidates(
        _draft(),
        [near_variant, far_conflict],
        ai=StubAI(
            {
                "assessments": [
                    {
                        "chunk_id": 11,
                        "relation": "conditional_variant",
                        "answer_relation": "candidate_adds_value",
                        "reason": "适用条件不同，补充了新的时间边界。",
                        "canonical_question": None,
                        "canonical_answer": None,
                    },
                    {
                        "chunk_id": 22,
                        "relation": "related",
                        "answer_relation": "conflict",
                        "reason": "已有答案要求随餐，新答案要求空腹，规则互斥。",
                        "canonical_question": None,
                        "canonical_answer": None,
                    },
                ]
            }
        ),
    )
    assert decision.action == "conflict"
    assert decision.target is not None
    assert decision.target.chunk_id == 22
    assert [item.candidate.chunk_id for item in decision.assessments] == [11, 22]


@pytest.mark.asyncio
async def test_multiple_conflicts_are_all_reported_in_reason() -> None:
    first = replace(_candidate(), chunk_id=11, distance=0.14)
    second = replace(_candidate(), chunk_id=22, distance=0.21)
    decision = await _judge_candidates(
        _draft(),
        [first, second],
        ai=StubAI(
            {
                "assessments": [
                    {
                        "chunk_id": 11,
                        "relation": "related",
                        "answer_relation": "conflict",
                        "reason": "时间口径冲突。",
                        "canonical_question": None,
                        "canonical_answer": None,
                    },
                    {
                        "chunk_id": 22,
                        "relation": "related",
                        "answer_relation": "conflict",
                        "reason": "剂量口径冲突。",
                        "canonical_question": None,
                        "canonical_answer": None,
                    },
                ]
            }
        ),
    )
    assert decision.action == "conflict"
    assert decision.target is not None
    assert decision.target.chunk_id == 11
    assert "22" in decision.reason


@pytest.mark.asyncio
async def test_missing_candidate_verdict_fails_closed() -> None:
    """漏判即失败：模型只回一条时不得放过其余候选。"""

    first = replace(_candidate(), chunk_id=11, distance=0.14)
    second = replace(_candidate(), chunk_id=22, distance=0.21)
    with pytest.raises(KnowledgeDedupeUnavailable, match="漏判"):
        await _judge_candidates(
            _draft(),
            [first, second],
            ai=StubAI(
                {
                    "assessments": [
                        {
                            "chunk_id": 11,
                            "relation": "different",
                            "answer_relation": "not_applicable",
                            "reason": "主题不同。",
                            "canonical_question": None,
                            "canonical_answer": None,
                        }
                    ]
                }
            ),
        )


def test_legacy_markdown_manifest_is_fail_closed(tmp_path) -> None:
    from app.core.config import Settings

    (tmp_path / "legacy.md").write_text("# old", encoding="utf-8")
    (tmp_path / "knowledge_sources.json").write_text(
        json.dumps({"active": [], "retained_legacy": ["legacy.md"]}),
        encoding="utf-8",
    )
    settings = Settings(KNOWLEDGE_BASE_DIR=str(tmp_path))
    assert _knowledge_files(settings) == []


def test_curated_legacy_faq_candidates_are_valid_and_not_active() -> None:
    from app.services import knowledge_base

    root = Path(knowledge_base.__file__).resolve().parent / "knowledge_base"
    manifest = json.loads((root / "knowledge_sources.json").read_text(encoding="utf-8"))
    candidate_name = "curated_legacy_faq_candidates.json"
    payload = json.loads((root / candidate_name).read_text(encoding="utf-8"))
    candidates = payload["candidates"]

    assert candidate_name in manifest["curated_candidates"]
    assert candidate_name not in manifest["active"]
    assert payload["status"] == "pending_user_review"
    assert payload["selection_summary"]["legacy_chunks_reviewed"] == 188
    assert payload["selection_summary"]["canonical_candidates"] == len(candidates) == 20
    assert len({item["candidate_uid"] for item in candidates}) == len(candidates)
    assert {
        item["disposition"] for item in candidates
    } == {"new", "merge", "replace"}
    for item in candidates:
        assert item["question"].strip().endswith(("？", "?"))
        assert item["answer"].strip()
        assert item["information_value"].strip()
        assert item["source_refs"]
        assert item["metadata"]["category"]
        assert item["metadata"]["risk_level"] in {"L0", "L1", "L2", "L3"}
