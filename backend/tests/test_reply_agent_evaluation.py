from __future__ import annotations

import json
import sys
from collections import Counter
from types import SimpleNamespace

import pytest

from app.evaluation.reply_agent_cases import (
    REQUIRED_RETRIEVAL_FAMILIES,
    load_reply_agent_eval_suite,
    summarize_reply_agent_eval_suite,
)
from app.schemas.reply_agent_evaluation import ReplyAgentEvaluationRunDetail
from app.services.reply_agent_evaluation_reports import ReplyAgentEvaluationReportStore
from eval.build_reply_agent_suite import build_suite
from eval.run_full_eval import _mock_cases as mock_rag_cases
from eval.run_reply_agent_eval import (
    _base_summary,
    adapt_rag_result,
    aggregate_summary,
    async_main,
)
from eval.rewrite_harm import assess_rewrite_harm
from eval.run_reply_agent_eval import parse_args


def test_fixed_reply_agent_suite_has_exact_contract() -> None:
    suite = load_reply_agent_eval_suite()
    summary = summarize_reply_agent_eval_suite(suite)

    assert suite.suite_id == "reply-agent-core-v1"
    assert suite.version == 1
    assert len(suite.cases) == 80
    assert summary["retrieval_counts"] == {"retrieve": 32, "skip": 48}
    assert summary["execution_counts"] == {"agent": 30, "handoff": 3, "rag": 47}
    assert summary["metric_group_counts"]["M0"] == 80
    assert summary["metric_group_counts"]["MR"] == 32
    assert set(summary["retrieval_family_counts"]) == REQUIRED_RETRIEVAL_FAMILIES
    assert min(summary["retrieval_family_counts"].values()) >= 4
    assert [case.case_id for case in suite.cases] == [
        f"RA-{index:03d}" for index in range(1, 81)
    ]
    source_ids = {source for case in suite.cases for source in case.source_case_ids}
    assert {
        "rag-faq-low-carb",
        "rag-chat-rice-at-lunch",
        "rag-faq-meal-order",
        "rag-chat-meal-order-why",
        "rag-faq-sudden-weight-gain",
        "rag-chat-coffee-weight",
        "rag-faq-constipation",
        "rag-chat-two-day-constipation",
    } <= source_ids


def test_generated_fixture_matches_builder_source() -> None:
    generated = build_suite().model_dump(mode="json")
    persisted = load_reply_agent_eval_suite().model_dump(mode="json")
    assert generated == persisted


def test_metric_routing_covers_retrieve_skip_tool_no_reply_and_handoff() -> None:
    suite = load_reply_agent_eval_suite()

    assert all("M0" in case.metric_groups for case in suite.cases)
    assert all(
        ("MR" in case.metric_groups) == (case.retrieval_decision == "retrieve")
        for case in suite.cases
    )
    assert any(
        case.agent_case
        and case.agent_case.expected.response_mode == "no_reply"
        and "MB" in case.metric_groups
        for case in suite.cases
    )
    assert any(
        case.agent_case
        and case.agent_case.expected.required_tools
        and "MT" in case.metric_groups
        for case in suite.cases
    )
    assert all("MB" in case.metric_groups for case in suite.cases if case.handoff)


def test_unified_cli_defaults_to_fixed_v1_and_ten_yuan_limit(monkeypatch) -> None:
    monkeypatch.delenv("REPLY_AGENT_EVALUATION_DATASET_PATH", raising=False)
    monkeypatch.delenv("REPLY_AGENT_EVAL_MAX_COST_YUAN", raising=False)
    monkeypatch.setattr(sys, "argv", ["run_reply_agent_eval.py"])

    args = parse_args()

    assert args.dataset.name == "reply_agent_eval_cases.v1.json"
    assert args.max_cost_yuan == 10
    assert args.case_ids == ""


@pytest.mark.asyncio
async def test_unified_mock_runs_80_once_with_zero_calls(tmp_path) -> None:
    reports_root = tmp_path / "reply-agent-evaluations"
    exit_code = await async_main(
        SimpleNamespace(
            mock=True,
            run_id="reply-agent-mock-test",
            reports_root=reports_root,
            dataset=None,
        )
    )

    assert exit_code == 0
    report_path = reports_root / "reply-agent-mock-test" / "report.json"
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    report = ReplyAgentEvaluationRunDetail.model_validate(payload)

    assert report.summary.status == "completed"
    assert report.summary.counts.total == 80
    assert report.summary.counts.completed == 80
    assert report.summary.counts.errors == 0
    assert report.summary.usage.call_count == 0
    assert report.summary.usage.total_tokens == 0
    assert report.summary.usage.estimated_cost_yuan == 0
    assert report.summary.metrics.rewrite_harm_rate.model_dump() == {
        "value": 0.0,
        "numerator": 0,
        "denominator": 32,
    }
    assert report.summary.metrics.answer_relevancy_average.denominator == 32
    assert report.summary.metrics.answer_correctness_observed.denominator == 47
    assert report.summary.metrics.handoff_accuracy.model_dump() == {
        "value": 1.0,
        "numerator": 3,
        "denominator": 3,
    }
    assert report.summary.metrics.judge_task_completion_observed.denominator == 30
    assert report.summary.metrics.judge_groundedness_observed.denominator == 30
    assert report.summary.metrics.judge_clarification_observed.denominator == 30
    assert report.summary.metrics.judge_expression_observed.denominator == 30
    assert len(report.cases) == 80
    assert len({case.case_id for case in report.cases}) == 80
    assert Counter(case.execution_kind for case in report.cases) == {
        "rag": 47,
        "agent": 30,
        "handoff": 3,
    }
    assert (
        sum(case.actual.retrieval_decision == "retrieve" for case in report.cases) == 32
    )
    assert all(
        case.usage.get("total", {}).get("call_count") == 0 for case in report.cases
    )
    assert all(case.evidence for case in report.cases)

    no_reply = next(
        case for case in report.cases if case.actual.response_mode == "no_reply"
    )
    assert "MB" in no_reply.expected.metric_groups
    handoff = next(case for case in report.cases if case.execution_kind == "handoff")
    assert handoff.actual.handoff_triggered is True
    assert handoff.actual.response is None
    assert any(metric.key == "reply_agent_bypassed" for metric in handoff.metrics)

    store = ReplyAgentEvaluationReportStore(reports_root)
    assert [item.run_id for item in store.list_runs()] == ["reply-agent-mock-test"]
    assert store.get_run("reply-agent-mock-test").summary.counts.total == 80


@pytest.mark.asyncio
async def test_unified_mock_can_run_a_targeted_case_subset(tmp_path) -> None:
    reports_root = tmp_path / "reply-agent-evaluations"
    exit_code = await async_main(
        SimpleNamespace(
            mock=True,
            run_id="reply-agent-targeted-mock-test",
            reports_root=reports_root,
            dataset=None,
            case_ids="RA-008,ra-036,RA-056",
        )
    )

    assert exit_code == 0
    report = ReplyAgentEvaluationRunDetail.model_validate_json(
        (
            reports_root
            / "reply-agent-targeted-mock-test"
            / "report.json"
        ).read_text(encoding="utf-8")
    )
    assert report.summary.counts.total == 3
    assert report.summary.counts.completed == 3
    assert [case.case_id for case in report.cases] == ["RA-008", "RA-036", "RA-056"]


@pytest.mark.asyncio
async def test_targeted_run_rejects_unknown_or_duplicate_case_ids(tmp_path) -> None:
    common = {
        "mock": True,
        "reports_root": tmp_path / "reply-agent-evaluations",
        "dataset": None,
    }
    with pytest.raises(ValueError, match="unknown case_ids: RA-999"):
        await async_main(
            SimpleNamespace(
                **common,
                run_id="reply-agent-targeted-unknown",
                case_ids="RA-001,RA-999",
            )
        )
    with pytest.raises(ValueError, match="case_ids contains duplicates"):
        await async_main(
            SimpleNamespace(
                **common,
                run_id="reply-agent-targeted-duplicate",
                case_ids="RA-008,RA-008",
            )
        )


def test_rewrite_harm_is_explicit_per_case_and_in_batch_summary() -> None:
    suite = load_reply_agent_eval_suite()
    case = next(
        item
        for item in suite.cases
        if item.rag_case and item.retrieval_decision == "retrieve"
    )
    source_result = mock_rag_cases([case.rag_case])[0]
    source_result.scores.raw_context_recall = 1.0
    source_result.scores.context_recall = 0.8
    source_result.status = "failed"

    adapted = adapt_rag_result(case, source_result)
    metric = next(item for item in adapted.metrics if item.key == "rewrite_harm")

    assert metric.score == pytest.approx(0.2)
    assert metric.threshold == 0.05
    assert metric.passed is False
    assert adapted.actual.knowledge_query == source_result.actual.knowledge_query
    assert adapted.evidence["rag"]["scores"]["raw_context_recall"] == 1.0

    summary = aggregate_summary(
        base=_base_summary(run_id="rewrite-harm-test", suite=suite, mock=True),
        cases=[adapted],
    )
    assert summary.metrics.rewrite_harm_rate.model_dump() == {
        "value": 1.0,
        "numerator": 1,
        "denominator": 1,
    }


def test_rewrite_harm_detects_hit_collapse_when_both_llm_recalls_are_zero() -> None:
    assessment = assess_rewrite_harm(
        original_query="喝完手有点抖，这算代谢起来了吗？",
        rewritten_query="使用产品后手抖是否属于正常代谢反应？",
        raw_hit_count=1,
        rewritten_hit_count=0,
        raw_recall=0.0,
        rewritten_recall=0.0,
    )

    assert assessment.applicable is True
    assert assessment.harmed is True
    assert assessment.signal == "hit_collapse"
    assert "语义召回尺子同时给低分" in assessment.reason


def test_rewrite_harm_excludes_unchanged_queries_from_denominator() -> None:
    assessment = assess_rewrite_harm(
        original_query="晚A怎么冲泡",
        rewritten_query=" 晚A 怎么冲泡 ",
        raw_hit_count=1,
        rewritten_hit_count=1,
        raw_recall=1.0,
        rewritten_recall=1.0,
    )

    assert assessment.applicable is False
    assert assessment.signal == "not_rewritten"
