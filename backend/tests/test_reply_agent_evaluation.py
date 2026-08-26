from __future__ import annotations

import json
import sys
from collections import Counter
from types import SimpleNamespace

import pytest

from app.evaluation.rag_cases import retrieval_clauses_for
from app.evaluation.rag_metric_calibration import find_prohibition_clauses
from app.evaluation.reply_agent_cases import (
    REQUIRED_RETRIEVAL_FAMILIES,
    load_reply_agent_eval_suite,
    summarize_reply_agent_eval_suite,
)
from app.schemas.reply_agent_evaluation import ReplyAgentEvaluationRunDetail
from app.services.reply_agent_evaluation_reports import ReplyAgentEvaluationReportStore
from eval.build_reply_agent_suite import build_suite
from eval.reanalyze_reply_agent_report import async_main as reanalyze_main
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

    assert suite.suite_id == "reply-agent-core-v4"
    assert suite.version == 4
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
    cold_water = next(case for case in suite.cases if case.case_id == "RA-003")
    assert "早C、午B、晚A" in cold_water.rag_case.history[0].content
    noon = next(case for case in suite.cases if case.case_id == "RA-005")
    noon_fact = noon.rag_case.authoritative_facts[0]
    assert noon_fact.statement == (
        "午B超过下午2点当天跳过，不要补喝；"
        "晚A照常按原计划进行。"
    )
    assert noon_fact.provenance == "knowledge_faqs:faq_2b69430ec351e003:v2"
    assert noon_fact.source_ids == ["faq_2b69430ec351e003"]
    assert "倒班" not in noon_fact.statement
    report_noon = next(case for case in suite.cases if case.case_id == "RA-067")
    assert (
        report_noon.rag_case.authoritative_facts[0].statement
        == noon.rag_case.authoritative_facts[0].statement
    )
    before_meal = next(case for case in suite.cases if case.case_id == "RA-021")
    assert before_meal.rag_case.history[0].content.startswith("午B随餐")
    assert before_meal.rag_case.rewrite.must_include == [
        "午B",
        "先喝完再吃正餐",
        "随餐边吃边喝",
    ]
    assert before_meal.rag_case.authoritative_facts[0].statement == (
        "早C和午B优先随餐饮用；条件不允许时，紧邻正餐前后喝也可以，"
        "不需要卡固定的分钟数。"
    )


def test_every_recall_gated_case_scores_on_retrievable_clauses_only() -> None:
    """The 32 recall-gated cases are what the calibrated 0.75 gate judges. A
    prohibition among their clauses has no retrievable truth, so the score it
    produces is not reproducible — 15 of them carried one before this contract."""
    suite = load_reply_agent_eval_suite()

    gated = 0
    offenders: dict[str, list[str]] = {}
    for case in suite.cases:
        rag_case = case.rag_case
        if not rag_case or not rag_case.retrieval.required_fact_ids:
            continue
        gated += 1
        fact_by_id = {fact.fact_id: fact for fact in rag_case.authoritative_facts}
        for fact_id in rag_case.retrieval.required_fact_ids:
            clauses = retrieval_clauses_for(fact_by_id[fact_id])
            assert clauses, f"{case.case_id}: recall has no clause to score"
            found = find_prohibition_clauses(clauses)
            if found:
                offenders[f"{case.case_id}:{fact_id}"] = found

    assert gated == 32
    assert offenders == {}


def test_recall_gated_prohibitions_survive_for_answer_scoring() -> None:
    suite = load_reply_agent_eval_suite()
    by_id = {case.case_id: case for case in suite.cases}

    noon_fact = by_id["RA-005"].rag_case.authoritative_facts[0]
    assert "不要补喝" in noon_fact.statement
    assert noon_fact.retrieval_clauses == [
        "午B超过下午2点当天跳过",
        "当天的晚A照常按原计划进行",
    ]


def test_generated_fixture_matches_builder_source() -> None:
    generated = build_suite().model_dump(mode="json")
    persisted = load_reply_agent_eval_suite().model_dump(mode="json")
    assert generated == persisted


def test_unified_agent_stubs_match_runtime_tool_contracts() -> None:
    suite = load_reply_agent_eval_suite()
    by_id = {case.case_id: case.agent_case for case in suite.cases if case.agent_case}

    def stub(case_id: str, tool_name: str) -> dict:
        item = next(
            item
            for item in by_id[case_id].tool_stubs
            if item.name == tool_name
        )
        return item.results[0]

    yesterday = stub("RA-033", "get_recent_checkins")
    assert set(yesterday) == {
        "ok",
        "days",
        "offset",
        "date_from",
        "date_to",
        "count",
        "complete_checkin_days",
        "has_more",
        "next_offset",
        "items",
        "note",
    }
    assert set(yesterday["items"][0]) == {
        "date",
        "is_complete",
        "counts_toward_complete_total",
        "content",
        "complete_items",
        "general",
        "personalized",
    }

    weight = stub("RA-034", "get_weight_trend")
    assert set(weight) == {"ok", "days", "points", "delta_kg", "direction"}
    assert set(weight["points"][0]) == {"date", "weight"}

    delivery = stub("RA-036", "read_delivery_document")
    assert set(delivery) == {
        "ok",
        "document_id",
        "document_type",
        "document_type_label",
        "title",
        "status",
        "created_at",
        "updated_at",
        "readable",
        "text_length",
        "text",
        "truncated",
    }

    group_file = stub("RA-038", "read_group_file")
    assert set(group_file) == {
        "ok",
        "message_id",
        "filename",
        "parse_status",
        "text",
        "truncated",
    }

    preference_read = stub("RA-039", "get_checkin_followup_preferences")
    preference_write = stub("RA-040", "update_checkin_followup_preferences")
    assert set(preference_read) == {"ok", "preferences"}
    assert set(preference_write) == {
        "ok",
        "updated_topics",
        "enabled",
        "preferences",
    }
    assert all(
        set(item) == {"topic", "label", "enabled"}
        for item in preference_read["preferences"] + preference_write["preferences"]
    )

    reminder_list = stub("RA-041", "list_group_reminders")
    reminder_cancel = stub("RA-042", "cancel_group_reminder")
    assert set(reminder_list) == {"ok", "pending", "count"}
    assert set(reminder_cancel) == {"ok", "cancelled", "count"}
    assert all(
        set(item) == {"task_id", "remind_at", "content", "recurrence"}
        for item in reminder_list["pending"] + reminder_cancel["cancelled"]
    )

    checklist = stub("RA-048", "set_checkin_checklist")
    assert set(checklist) == {
        "ok",
        "updated",
        "source_type",
        "version_no",
        "questions",
    }
    assert len(checklist["questions"]) == 5

    web_search = stub("RA-059", "web_search_lifestyle")
    assert set(web_search) == {"ok", "query", "answer", "results"}
    assert all(
        set(item) == {"title", "url", "content", "score"}
        for item in web_search["results"]
    )

    analysis = stub("RA-061", "get_recent_checkins")
    assert set(analysis) == set(yesterday)
    assert len(analysis["items"]) == 7
    assert sum("exercise" in item["general"] for item in analysis["items"]) == 4

    lifecycle = by_id["RA-055"]
    assert lifecycle.expected.required_facts == [
        "本条没有明确 @ 机器人，所以不会执行服务周期修改"
    ]

    dehydration = next(case for case in suite.cases if case.case_id == "RA-080")
    assert dehydration.handoff.expected_risk_codes == [
        "physical_discomfort",
        "other_risk",
    ]


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


def test_unified_cli_defaults_to_fixed_v4_and_ten_yuan_limit(monkeypatch) -> None:
    monkeypatch.delenv("REPLY_AGENT_EVALUATION_DATASET_PATH", raising=False)
    monkeypatch.delenv("REPLY_AGENT_EVAL_MAX_COST_YUAN", raising=False)
    monkeypatch.setattr(sys, "argv", ["run_reply_agent_eval.py"])

    args = parse_args()

    assert args.dataset.name == "reply_agent_eval_cases.v4.json"
    assert args.max_cost_yuan == 10
    assert args.case_ids == ""


def test_unified_v4_locks_confirmed_business_rulings() -> None:
    suite = load_reply_agent_eval_suite()
    by_source = {case.source_case_ids[0]: case for case in suite.cases}

    evening = by_source["rag-faq-evening-water"].rag_case
    assert "默认配330ml水冲泡" in evening.authoritative_facts[0].statement
    assert "300ml" not in evening.rewrite.must_include

    cold = by_source["rag-faq-cold-water"].rag_case
    assert "水温不高于85℃" in cold.authoritative_facts[0].statement
    assert "60–85℃热水" in cold.authoritative_facts[0].statement

    night = by_source["rag-faq-night-shift"].rag_case
    assert "晚A在计划睡眠前1至2小时" in night.authoritative_facts[0].statement

    before_meal = by_source["rag-chat-before-meal"].rag_case
    fact_text = before_meal.authoritative_facts[0].statement
    assert "紧邻正餐前后喝也可以" in fact_text
    assert "不属于随餐" not in fact_text

    travel = by_source["rag-chat-travel-interruption"].rag_case
    assert "28天服务期不会因为旅游中断自动顺延" in (
        travel.authoritative_facts[0].statement
    )

    constipation = by_source["rag-chat-two-day-constipation"].rag_case
    assert constipation.answer.response_mode == "clarify"
    assert constipation.answer.required_fact_ids == ["chat-r09-clarify"]
    assert constipation.answer.forbidden_fact_ids == ["chat-r09"]
    assert constipation.retrieval.required_fact_ids == ["chat-r09"]

    for source_id in ("rag-faq-low-impact-exercise", "rag-faq-sleep-adjustment"):
        case = by_source[source_id].rag_case
        assert case.authoritative_facts == []
        assert case.retrieval.required_fact_ids == []
        assert case.answer.required_fact_ids == []


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
    assert report.summary.score_source == "fresh_run"
    assert report.summary.ruler_policy is not None
    assert report.summary.ruler_policy.task_completion_policy_version == (
        "task-completion-v4"
    )
    assert report.summary.ruler_policy.task_completion_threshold == 0.9
    agent_metrics = [
        metric
        for case in report.cases
        if case.execution_kind == "agent"
        for metric in case.metrics
    ]
    assert all(
        metric.enforced is (metric.key == "judge_task_completion")
        for metric in agent_metrics
        if metric.key.startswith("judge_")
    )

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


@pytest.mark.asyncio
async def test_saved_output_reanalysis_replays_80_cases_without_generation(
    tmp_path,
) -> None:
    reports_root = tmp_path / "reply-agent-evaluations"
    await async_main(
        SimpleNamespace(
            mock=True,
            run_id="reply-agent-reanalysis-source",
            reports_root=reports_root,
            dataset=None,
            case_ids="",
        )
    )
    exit_code = await reanalyze_main(
        SimpleNamespace(
            source_report=(
                reports_root
                / "reply-agent-reanalysis-source"
                / "report.json"
            ),
            run_id="reply-agent-reanalysis-mock",
            mock=True,
            max_cost_yuan=10,
            reports_root=reports_root,
            dataset=None,
        )
    )

    assert exit_code == 0
    report = ReplyAgentEvaluationRunDetail.model_validate_json(
        (
            reports_root / "reply-agent-reanalysis-mock" / "report.json"
        ).read_text(encoding="utf-8")
    )
    assert report.summary.status == "completed"
    assert report.summary.score_source == "saved_output_reanalysis"
    assert report.summary.source_run_id == "reply-agent-reanalysis-source"
    assert report.summary.counts.total == 80
    assert report.summary.counts.completed == 80
    assert report.summary.usage.call_count == 0
    assert report.summary.ruler_policy is not None
    assert report.summary.ruler_policy.task_completion_threshold == 0.9
    agent_cases = [case for case in report.cases if case.execution_kind == "agent"]
    assert len(agent_cases) == 30
    assert all(
        next(
            metric
            for metric in case.metrics
            if metric.key == "judge_task_completion"
        ).enforced
        for case in agent_cases
    )
    assert all(case.evidence["source_run_id"] for case in report.cases)


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
