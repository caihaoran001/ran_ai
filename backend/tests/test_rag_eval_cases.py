import re
from collections import Counter

from app.evaluation.rag_cases import REQUIRED_RISK_TAGS, load_rag_eval_suite


def test_fixed_rag_eval_suite_has_exact_contract_and_risk_coverage():
    suite = load_rag_eval_suite()

    assert suite.suite_id == "rag-fixed-50-v1"
    assert suite.version == 1
    assert len(suite.cases) == 50
    assert Counter(case.origin.kind for case in suite.cases) == {
        "faq_rewrite": 25,
        "real_chat": 25,
    }
    assert Counter(case.rewrite.decision for case in suite.cases) == {
        "retrieve": 35,
        "skip": 15,
    }
    assert REQUIRED_RISK_TAGS <= {
        tag for case in suite.cases for tag in case.tags
    }
    assert all(len(case.history) <= 10 for case in suite.cases)


def test_fixed_suite_contains_no_direct_identifiers_in_real_chat_cases():
    suite = load_rag_eval_suite()
    chat_text = "\n".join(
        text
        for case in suite.cases
        if case.origin.kind == "real_chat"
        for text in [case.user_query, *(message.content for message in case.history)]
    )

    assert not re.search(r"(?<!\d)1[3-9]\d{9}(?!\d)", chat_text)
    assert "@" not in chat_text
    assert "详细地址" not in chat_text


def test_original_bottle_cap_case_uses_positive_fact_relationships():
    suite = load_rag_eval_suite()
    case = next(item for item in suite.cases if item.case_id == "rag-chat-cap-ambiguity")

    assert case.origin.kind == "real_chat"
    assert case.rewrite.decision == "retrieve"
    assert "一盖指一瓶盖的用量" in case.rewrite.must_preserve_meanings[0]
    assert set(case.answer.required_fact_ids) == {"chat-r01"}
    assert "一瓶盖不够" in case.authoritative_facts[0].statement
    assert "约需两瓶盖" in case.authoritative_facts[0].statement


def test_ambiguous_real_chat_requires_clarification_without_retrieval():
    suite = load_rag_eval_suite()
    case = next(item for item in suite.cases if item.case_id == "rag-chat-ambiguous-how")

    assert case.origin.kind == "real_chat"
    assert case.rewrite.decision == "skip"
    assert case.answer.response_mode == "clarify"
    assert not case.retrieval.required_fact_ids


def test_conversation_correction_skips_knowledge_but_preserves_positive_fact():
    suite = load_rag_eval_suite()
    case = next(item for item in suite.cases if item.case_id == "rag-chat-image-correction")

    assert case.rewrite.decision == "skip"
    assert not case.retrieval.required_fact_ids
    assert case.answer.required_fact_ids == ["chat-s08"]
    assert case.authoritative_facts[0].source_scope == "conversation"
