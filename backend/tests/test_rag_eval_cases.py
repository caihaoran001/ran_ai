import re
from collections import Counter

import pytest

from app.evaluation.rag_cases import (
    REQUIRED_RISK_TAGS,
    load_rag_eval_suite,
    retrieval_clauses_for,
    validate_retrieval_clause_contract,
)
from app.evaluation.rag_metric_calibration import find_prohibition_clauses


def test_fixed_rag_eval_suite_has_exact_contract_and_risk_coverage():
    suite = load_rag_eval_suite()

    assert suite.suite_id == "rag-fixed-50-v3"
    assert suite.version == 3
    assert len(suite.cases) == 50
    assert Counter(case.origin.kind for case in suite.cases) == {
        "faq_rewrite": 25,
        "real_chat": 25,
    }
    assert Counter(case.rewrite.decision for case in suite.cases) == {
        "retrieve": 16,
        "skip": 34,
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


def test_ellipsis_cases_include_the_context_needed_to_resolve_the_reference():
    suite = load_rag_eval_suite()
    cases = {case.case_id: case for case in suite.cases}

    noon = cases["rag-faq-noon-boundary"]
    questionnaire = cases["rag-faq-questionnaire-purpose"]

    assert any("午B" in item.content for item in noon.history)
    assert any("入营问卷" in item.content for item in questionnaire.history)


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


def test_recall_gated_facts_state_retrievable_clauses_only():
    """Recall asks what the retrieved chunks support. No chunk asserts that a
    wrong framing is absent, so a prohibition there has no reproducible verdict:
    the v1 calibration judged identical pairs both ways across repeats."""
    suite = load_rag_eval_suite()

    offenders = {}
    for case in suite.cases:
        fact_by_id = {fact.fact_id: fact for fact in case.authoritative_facts}
        for fact_id in case.retrieval.required_fact_ids:
            clauses = retrieval_clauses_for(fact_by_id[fact_id])
            assert clauses, f"{case.case_id}: recall has no clause to score"
            found = find_prohibition_clauses(clauses)
            if found:
                offenders[f"{case.case_id}:{fact_id}"] = found

    assert offenders == {}


def test_prohibitions_stay_in_the_statement_for_answer_scoring():
    """Dropping them from recall must not drop them from the case: answer
    correctness still requires the reply to carry the prohibition."""
    suite = load_rag_eval_suite()
    cases = {case.case_id: case for case in suite.cases}

    palpitations = cases["rag-faq-palpitations"].authoritative_facts[0]
    assert "不应解释为代谢启动" in palpitations.statement
    assert not find_prohibition_clauses(retrieval_clauses_for(palpitations))

    night_shift = cases["rag-faq-night-shift"].authoritative_facts[0]
    assert "不要机械照搬白天钟点" in night_shift.statement
    # One bundled sentence became four scoreable clauses, so the gate stops
    # being all-or-nothing on this case.
    assert retrieval_clauses_for(night_shift) == [
        "倒班时应按自己的清醒周期安排产品",
        "早C在起床后的第一餐",
        "午B在清醒周期中段且与计划睡眠至少间隔8小时",
        "晚A在最后一餐或准备结束清醒周期时",
    ]


def test_retrieval_clauses_fall_back_to_splitting_the_statement():
    """A fact whose whole statement answers its question needs no override."""
    suite = load_rag_eval_suite()
    case = next(item for item in suite.cases if item.case_id == "rag-faq-cap-conversion")
    fact = case.authoritative_facts[0]

    assert not fact.retrieval_clauses
    assert retrieval_clauses_for(fact) == [
        "一瓶盖是半份，约等于1.5个小刻度",
        "一大刻度等于3个小刻度，也就是一份，约两瓶盖",
        "所以按一大刻度执行时，一瓶盖不够，量取时以瓶体刻度或附赠刻度杯为准",
    ]


def test_off_topic_clauses_leave_the_recall_reference():
    """The judge is asked what the retrieval had to surface to answer *this*
    question. A rule that merely shares a FAQ paragraph with the answer is not
    that, and requiring it turns a good retrieval into a failed case."""
    suite = load_rag_eval_suite()
    cases = {case.case_id: case for case in suite.cases}

    # 问「两点多才想起来还补吗」：晚A的安排不是回答这个问题所需的知识
    noon = cases["rag-faq-noon-boundary"].authoritative_facts[0]
    assert "晚A照常按原计划进行" in noon.statement
    assert retrieval_clauses_for(noon) == [
        "午B的完成时限是下午2点",
        "午B超过时限当天跳过",
    ]

    # 问「这三种都只能用温水吗」：经期口径同段但与本题无关
    cold = cases["rag-faq-cold-water"].authoritative_facts[0]
    assert "经期建议热水冲调" in cold.statement
    assert not any("经期" in clause for clause in retrieval_clauses_for(cold))


def test_suite_rejects_a_prohibition_in_a_recall_gated_fact():
    case = next(
        item
        for item in load_rag_eval_suite().cases
        if item.case_id == "rag-faq-noon-boundary"
    )
    case.authoritative_facts[0].retrieval_clauses = [
        "午B超过下午2点当天跳过",
        "不要补喝",
    ]

    with pytest.raises(ValueError, match="prohibitions"):
        validate_retrieval_clause_contract(case)


def test_suite_rejects_a_recall_gated_fact_with_nothing_left_to_score():
    case = next(
        item
        for item in load_rag_eval_suite().cases
        if item.case_id == "rag-faq-noon-boundary"
    )
    case.authoritative_facts[0].statement = "午B超过下午2点当天不要补喝。"
    case.authoritative_facts[0].retrieval_clauses = []

    with pytest.raises(ValueError, match="prohibitions"):
        validate_retrieval_clause_contract(case)
