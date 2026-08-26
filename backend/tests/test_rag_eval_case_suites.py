import json
from pathlib import Path

import pytest

from app.evaluation.rag_cases import load_rag_eval_suite
from app.evaluation.rag_metric_calibration import (
    select_stable_calibration_threshold,
)

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"


def _write_suite(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "suite.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_v2_suite_loads_with_legacy_gating():
    suite = load_rag_eval_suite(FIXTURES / "rag_eval_cases.v2.json")
    decisions = {case.rewrite.decision for case in suite.cases}
    assert decisions == {"retrieve", "skip"}
    faq = [c for c in suite.cases if c.origin.kind == "faq_rewrite"]
    assert faq and all(c.rewrite.decision == "retrieve" for c in faq)


def test_v3_suite_loads_with_product_scoped_gating():
    suite = load_rag_eval_suite(FIXTURES / "rag_eval_cases.v3.json")
    retrieve = [c for c in suite.cases if c.rewrite.decision == "retrieve"]
    skip = [c for c in suite.cases if c.rewrite.decision == "skip"]
    assert len(retrieve) == 16
    assert len(skip) == 34
    by_id = {c.case_id: c for c in suite.cases}
    for keep in (
        "rag-faq-period-products",
        "rag-faq-palpitations",
        "rag-faq-diarrhea",
        "rag-faq-constipation",
        "rag-chat-two-day-constipation",
    ):
        assert by_id[keep].rewrite.decision == "retrieve", keep
    assert by_id["rag-faq-sleep-adjustment"].rewrite.decision == "skip"
    for drop in ("rag-chat-yellow-peach", "rag-faq-low-carb", "rag-faq-checkin-method"):
        assert by_id[drop].rewrite.decision == "skip", drop


# e0797e3 re-pointed rag-faq-noon-boundary at the atomic FAQ confirmed by
# operations, so v3 no longer carries v2's bundled statement for it. v2 is a
# frozen record of the superseded suite and is deliberately not back-patched.
CASES_CORRECTED_AFTER_V2 = {"rag-faq-noon-boundary"}


def test_v2_and_v3_share_identical_case_content_except_decisions():
    """v3 re-gated the suite; apart from the corrections listed above it did not
    rewrite the material. `retrieval_clauses` is excluded because it is scoring
    policy, not content: it says which half of an unchanged statement recall may
    be judged on, and only v3 gates these cases."""
    v2 = {c.case_id: c for c in load_rag_eval_suite(FIXTURES / "rag_eval_cases.v2.json").cases}
    v3 = {c.case_id: c for c in load_rag_eval_suite(FIXTURES / "rag_eval_cases.v3.json").cases}
    assert set(v2) == set(v3)
    assert CASES_CORRECTED_AFTER_V2 <= set(v3)
    for case_id, old in v2.items():
        new = v3[case_id]
        assert new.user_query == old.user_query
        if case_id in CASES_CORRECTED_AFTER_V2:
            continue
        assert [
            fact.model_dump(exclude={"retrieval_clauses"})
            for fact in new.authoritative_facts
        ] == [
            fact.model_dump(exclude={"retrieval_clauses"})
            for fact in old.authoritative_facts
        ]


def test_unregistered_suite_version_is_rejected(tmp_path):
    payload = json.loads((FIXTURES / "rag_eval_cases.v3.json").read_text())
    payload["suite_id"] = "rag-fixed-50-v99"
    payload["version"] = 99
    with pytest.raises(ValueError, match="not registered"):
        load_rag_eval_suite(_write_suite(tmp_path, payload))


def test_wrong_decision_counts_are_rejected(tmp_path):
    payload = json.loads((FIXTURES / "rag_eval_cases.v3.json").read_text())
    flip = next(
        c for c in payload["cases"] if c["rewrite"]["decision"] == "retrieve"
        and c["origin"]["kind"] == "real_chat"
    )
    flip["rewrite"]["decision"] = "skip"
    with pytest.raises(ValueError, match="exactly 16 retrieve"):
        load_rag_eval_suite(_write_suite(tmp_path, payload))


def test_v2_rejects_skipped_faq_but_v3_accepts(tmp_path):
    payload = json.loads((FIXTURES / "rag_eval_cases.v2.json").read_text())
    chat_skip = next(
        c for c in payload["cases"]
        if c["origin"]["kind"] == "faq_rewrite" and c["case_id"].endswith("low-carb")
    )
    chat_skip["rewrite"]["decision"] = "skip"
    with pytest.raises(ValueError, match="faq_rewrite cases must retrieve"):
        load_rag_eval_suite(_write_suite(tmp_path, payload))

    payload_v3 = json.loads((FIXTURES / "rag_eval_cases.v3.json").read_text())
    assert any(
        c["origin"]["kind"] == "faq_rewrite" and c["rewrite"]["decision"] == "skip"
        for c in payload_v3["cases"]
    )


def test_threshold_selector_prefers_perfect_gap_midpoint():
    selection = select_stable_calibration_threshold(
        [([0.9, 0.95], [0.3, 0.35]), ([0.88, 0.93], [0.28, 0.4])],
        minimum_classification_rate=0.9,
    )
    assert selection is not None
    assert selection.rule == "perfect_gap_midpoint_v1"
    assert selection.selected_threshold == pytest.approx(0.64)
