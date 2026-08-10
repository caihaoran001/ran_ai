from app.evaluation.rag_cases import REQUIRED_RISK_TAGS, load_rag_eval_suite


def test_versioned_rag_eval_suite_is_valid_and_covers_core_risks():
    suite = load_rag_eval_suite()

    assert suite.suite_id == "rag-chat-core-v1"
    assert suite.version == 1
    assert len(suite.cases) >= 16
    assert {case.rewrite.decision for case in suite.cases} == {"retrieve", "skip"}
    assert REQUIRED_RISK_TAGS <= {
        tag for case in suite.cases for tag in case.tags
    }
    assert all(len(case.history) <= 10 for case in suite.cases)


def test_original_bottle_cap_case_guards_action_and_comparison_direction():
    suite = load_rag_eval_suite()
    case = next(item for item in suite.cases if item.case_id == "rag-cap-ellipsis")

    assert case.rewrite.decision == "retrieve"
    assert "用量" in case.rewrite.must_preserve_meanings
    assert any(
        "冲泡后" in inference and "盖上瓶盖" in inference
        for inference in case.rewrite.forbidden_inferences
    )
    assert "一大刻度不够一瓶盖" in case.answer.forbidden_claims
    assert set(case.answer.required_fact_ids) == {"cap-small", "large-small"}


def test_ambiguous_case_requires_clarification_without_forced_retrieval():
    suite = load_rag_eval_suite()
    case = next(item for item in suite.cases if item.case_id == "rag-ambiguous-this")

    assert case.rewrite.decision == "skip"
    assert case.answer.response_mode == "clarify"
    assert not case.retrieval.required_fact_ids
