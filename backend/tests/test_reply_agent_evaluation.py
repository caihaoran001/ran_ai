from __future__ import annotations

import asyncio
import json
import sys
from collections import Counter
from types import SimpleNamespace

import pytest

from app.evaluation.rag_cases import retrieval_clauses_for
from app.evaluation.rag_metric_calibration import find_prohibition_clauses
from app.schemas.agent_evaluation import AgentExpectedToolCall
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
    REAL_CASE_CONCURRENCY,
    _base_summary,
    _run_case_batch,
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
    assert summary["retrieval_counts"] == {"retrieve": 40, "skip": 40}
    assert summary["execution_counts"] == {"agent": 30, "handoff": 3, "rag": 47}
    assert summary["metric_group_counts"]["M0"] == 80
    assert summary["metric_group_counts"]["MR"] == 40
    assert REQUIRED_RETRIEVAL_FAMILIES <= set(summary["retrieval_family_counts"])
    # 覆盖度契约只约束核心产品检索族；饮食/服务规则是改判后新增的窄族，
    # 与套件模型的校验口径保持一致。
    assert all(
        summary["retrieval_family_counts"][family] >= 4
        for family in REQUIRED_RETRIEVAL_FAMILIES
    )
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
    """The 40 recall-gated cases are what the calibrated 0.75 gate judges. A
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

    assert gated == 40
    assert offenders == {}


def test_recall_gated_prohibitions_survive_for_answer_scoring() -> None:
    suite = load_reply_agent_eval_suite()
    by_id = {case.case_id: case for case in suite.cases}

    noon_fact = by_id["RA-005"].rag_case.authoritative_facts[0]
    assert "不要补喝" in noon_fact.statement
    assert noon_fact.retrieval_clauses == [
        "午B的完成时限是下午2点",
        "午B超过时限当天跳过",
    ]


def test_derived_cases_scope_recall_to_the_side_they_ask_about() -> None:
    """RA-002 and RA-075 quote the same FAQ paragraph but ask about water volume
    and water temperature. One clause list for both would fail a retrieval that
    correctly surfaced the answer to the question actually asked."""
    suite = load_reply_agent_eval_suite()
    by_id = {case.case_id: case for case in suite.cases}

    volume = by_id["RA-002"].rag_case.authoritative_facts[0].retrieval_clauses
    temperature = by_id["RA-075"].rag_case.authoritative_facts[0].retrieval_clauses

    assert any("330ml" in clause for clause in volume)
    assert not any("85℃" in clause for clause in volume)
    assert temperature == ["晚A冲泡水温控制在85℃以下"]

    # 卡点钥匙题与经期题共用冷水 FAQ，各自只保留自己那一侧
    hot_key = by_id["RA-065"].rag_case.authoritative_facts[0].retrieval_clauses
    period = by_id["RA-073"].rag_case.authoritative_facts[0].retrieval_clauses
    assert any("卡点钥匙" in clause for clause in hot_key)
    assert not any("经期" in clause for clause in hot_key)
    assert any("经期" in clause for clause in period)
    assert not any("卡点钥匙" in clause for clause in period)


def test_generated_fixture_matches_builder_source() -> None:
    generated = build_suite().model_dump(mode="json")
    persisted = load_reply_agent_eval_suite().model_dump(mode="json")
    assert generated == persisted


def test_retrieval_noise_cases_follow_live_knowledge_boundary() -> None:
    suite = load_reply_agent_eval_suite()
    by_source = {case.source_case_ids[0]: case for case in suite.cases}

    expected: dict[str, tuple[str, list[str]]] = {
        "rag-faq-meal-order": (
            "diet_rule",
            [
                "正餐按蛋白质、蔬菜、粗粮主食的顺序进食",
                "先吃蛋白质打底",
                "再吃蔬菜",
                "最后吃适量粗粮主食",
            ],
        ),
        "rag-faq-sudden-weight-gain": (
            "health_knowledge",
            [
                "单日体重上涨常受饮水、盐分、碳水储存、胃肠内容物、排便、运动和激素变化影响",
                "应在相同条件下继续记录并观察一周左右趋势",
            ],
        ),
        "rag-faq-checkin-method": (
            "service_rule",
            ["可以直接在当前打卡群里发文字记录"],
        ),
        "rag-chat-coffee-weight": (
            "health_knowledge",
            [
                "单日或短期体重上涨常受饮水、盐分、碳水储存、胃肠内容物、排便、运动和激素变化影响",
            ],
        ),
        "rag-chat-rice-at-lunch": (
            "diet_rule",
            ["午餐可以吃适量米饭", "与蛋白质和蔬菜搭配"],
        ),
        "rag-chat-meal-order-why": (
            "diet_rule",
            [
                "当前饮食建议的顺序是先吃几口蛋白质打底",
                "再吃蔬菜",
                "最后吃适量粗粮主食",
            ],
        ),
    }

    for source_id, (family, clauses) in expected.items():
        case = by_source[source_id]
        assert case.retrieval_decision == "retrieve", source_id
        assert case.retrieval_families == [family], source_id
        assert case.rag_case is not None
        assert case.rag_case.rewrite.decision == "retrieve"
        assert case.rag_case.retrieval.required_fact_ids == (
            case.rag_case.answer.required_fact_ids
        )
        fact_id = case.rag_case.retrieval.required_fact_ids[0]
        fact = next(
            fact for fact in case.rag_case.authoritative_facts if fact.fact_id == fact_id
        )
        assert fact.retrieval_clauses == clauses, source_id

    for source_id in (
        "rag-faq-low-impact-exercise",
        "rag-faq-sleep-adjustment",
        "rag-chat-ambiguous-how",
    ):
        assert by_source[source_id].retrieval_decision == "skip", source_id


def test_date_cases_grade_business_time_without_requiring_iso_wording() -> None:
    suite = load_reply_agent_eval_suite()
    by_id = {case.case_id: case.agent_case for case in suite.cases if case.agent_case}

    yesterday = by_id["RA-033"].expected
    assert yesterday.required_facts == ["昨天完成3/5项"]
    assert yesterday.required_tools[0].args_subset == {
        "date_from": "2026-08-20",
        "date_to": "2026-08-20",
    }
    assert "今天完成3项" in yesterday.forbidden_claims

    reminder = by_id["RA-050"].expected
    assert reminder.required_facts == ["已创建明早8点称体重的一次性提醒"]
    assert reminder.required_tools[0].args_subset == {
        "remind_at": "2026-08-22T08:00:00+08:00",
        "content": "称体重",
    }
    assert "创建成每天重复提醒" in reminder.forbidden_claims


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

    # 班主任没 @ 机器人只说了句"这个用户看起来可以继续服务"：红线是不许去动
    # 服务周期，沉默同样守住了它，也不拆班主任的台。强制表态是红线之外多加的。
    lifecycle = by_id["RA-055"]
    assert lifecycle.expected.response_mode == "reply_or_silence"
    assert lifecycle.expected.required_facts == []
    assert lifecycle.expected.forbidden_tools == [
        "continue_service",
        "set_service_start_date",
        "end_continued_service",
    ]

    # 对用户念内部提醒编号是产品缺陷，不该是验收标准
    reminders = by_id["RA-041"]
    assert reminders.expected.required_literals == []
    assert reminders.expected.required_facts == ["明早8点有一个称体重的提醒"]

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


@pytest.mark.asyncio
async def test_real_case_batch_uses_exactly_eight_workers() -> None:
    cases = list(range(24))
    results: list[int] = []
    active = 0
    peak_active = 0
    first_wave_ready = asyncio.Event()
    progress_counts: list[int] = []

    async def run_case(case: int) -> int:
        nonlocal active, peak_active
        active += 1
        peak_active = max(peak_active, active)
        if active == REAL_CASE_CONCURRENCY:
            first_wave_ready.set()
        await first_wave_ready.wait()
        await asyncio.sleep(0)
        active -= 1
        return case

    async def progress(current: list[int]) -> None:
        progress_counts.append(len(current))

    await _run_case_batch(
        cases,
        run_case=run_case,
        results=results,
        on_progress=progress,
    )

    assert REAL_CASE_CONCURRENCY == 8
    assert peak_active == 8
    assert sorted(results) == cases
    assert progress_counts == list(range(1, 25))


@pytest.mark.asyncio
async def test_real_case_batch_stops_new_work_after_cost_gate() -> None:
    cases = list(range(20))
    results: list[int] = []
    started: list[int] = []
    first_wave_ready = asyncio.Event()

    async def run_case(case: int) -> int:
        started.append(case)
        if len(started) == REAL_CASE_CONCURRENCY:
            first_wave_ready.set()
        await first_wave_ready.wait()
        await asyncio.sleep(0)
        return case

    async def cost_gate(_current: list[int]) -> None:
        raise RuntimeError("统一评测已达到费用上限")

    with pytest.raises(RuntimeError, match="已达到费用上限"):
        await _run_case_batch(
            cases,
            run_case=run_case,
            results=results,
            on_progress=cost_gate,
        )

    # 已在途的 8 道允许收尾并进入费用汇总，但不会领取第 9 道。
    assert len(started) == 8
    assert sorted(started) == list(range(8))
    assert sorted(results) == list(range(8))


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
    assert report.summary.diagnostic_rollups_recorded is True
    assert payload["summary"]["diagnostic_rollups_recorded"] is True
    assert report.summary.counts.total == 80
    assert report.summary.counts.completed == 80
    assert report.summary.counts.errors == 0
    assert report.summary.usage.call_count == 0
    assert report.summary.usage.total_tokens == 0
    assert report.summary.usage.estimated_cost_yuan == 0
    assert report.summary.metrics.rewrite_harm_rate.model_dump() == {
        "value": 0.0,
        "numerator": 0,
        "denominator": 40,
    }
    assert report.summary.metrics.answer_relevancy_average.denominator == 40
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
        sum(case.actual.retrieval_decision == "retrieve" for case in report.cases) == 40
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
    # 四把大模型评分官全部降为诊断：分数照样存、照样标红，但不决定整题成败。
    # 这套评测没接任何自动流水线，判死没有对象；而任务完成度那把尺子的判分标准
    # 里含"必须调用某工具"，它只拿到回复正文、看不见调用记录，只能猜。
    assert all(
        metric.enforced is False
        for metric in agent_metrics
        if metric.key.startswith("judge_")
    )
    assert all(
        metric.enforced is True
        for metric in agent_metrics
        if metric.kind == "deterministic" and not metric.key.startswith("judge_")
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


def test_legacy_report_without_diagnostic_rollup_marker_stays_unknown(
    tmp_path,
) -> None:
    """旧报告缺标记不是“名单为空”，页面必须能看见它是未知。"""
    reports_root = tmp_path / "reply-agent-evaluations"
    run_dir = reports_root / "reply-agent-legacy-test"
    run_dir.mkdir(parents=True)
    base = _base_summary(
        run_id="reply-agent-legacy-test",
        suite=load_reply_agent_eval_suite(),
        mock=True,
    )
    payload = {
        "summary": base.model_dump(mode="json"),
        "cases": [],
    }
    payload["summary"].pop("diagnostic_rollups_recorded")
    (run_dir / "report.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )

    summary = ReplyAgentEvaluationReportStore(reports_root).get_run(
        "reply-agent-legacy-test"
    ).summary
    assert summary.diagnostic_rollups_recorded is False


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


def test_batch_summary_reports_averages_beside_the_gates() -> None:
    """A gate says how many cases cleared the line. Two cases at 1.0 and 0.0
    clear the same count as two cases at 1.0 and 0.7, and the first pair means
    the retrieval returned nothing while the second means it nearly made it."""
    suite = load_reply_agent_eval_suite()
    cases = [
        item
        for item in suite.cases
        if item.rag_case and item.retrieval_decision == "retrieve"
    ][:2]
    adapted = []
    for case, recall in zip(cases, (1.0, 0.0), strict=True):
        source_result = mock_rag_cases([case.rag_case])[0]
        source_result.scores.raw_context_recall = recall
        source_result.scores.context_recall = recall
        adapted.append(adapt_rag_result(case, source_result))

    summary = aggregate_summary(
        base=_base_summary(run_id="average-test", suite=suite, mock=True),
        cases=adapted,
    )

    assert summary.metrics.context_recall_pass_rate.value == pytest.approx(0.5)
    recall_average = summary.metrics.context_recall_average
    assert recall_average.value == pytest.approx(0.5)
    assert recall_average.denominator == 2
    assert recall_average.minimum == pytest.approx(0.0)
    assert recall_average.zero_count == 1


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


def test_answer_requirements_stay_on_the_question_asked() -> None:
    """A required fact is handed to the judge as something the reply must cover.

    Three FAQs bundle the answer to a second question the user never asked, so a
    reply that answered the actual question exactly was marked incomplete. The
    second half stays on the case as an unrequired fact — on record, not graded.
    """
    suite = load_reply_agent_eval_suite()
    by_id = {case.case_id: case for case in suite.cases}

    off_topic = {
        # 问进食顺序，不问要不要戒主食
        "RA-012": ("faq-f12", "无需完全戒主食"),
        # 问在哪里打卡，不问打卡填哪些项目
        "RA-016": ("faq-f23", "身体感受"),
        # 问顺序的理由，不问蛋白质与主食总则
        "RA-026": ("chat-r10", "无需完全戒主食"),
    }

    for case_id, (fact_id, aside_marker) in off_topic.items():
        rag_case = by_id[case_id].rag_case
        assert rag_case is not None
        facts = {fact.fact_id: fact for fact in rag_case.authoritative_facts}
        required = rag_case.answer.required_fact_ids

        assert required == [fact_id], case_id
        assert aside_marker not in facts[fact_id].statement, (
            f"{case_id}: 必答事实里还捆着另一个问题的答案"
        )
        aside = facts.get(f"{fact_id}-aside")
        assert aside is not None, f"{case_id}: 跑题的那半句被删掉了，没有留档"
        assert aside_marker in aside.statement, case_id
        assert aside.fact_id not in required, case_id
        assert aside.source_ids == facts[fact_id].source_ids, case_id


def test_only_black_and_white_violations_decide_a_case() -> None:
    """整题成败只反映越界，不反映分数。

    这套评测是人手启动、人读报告，没有任何自动流程依赖成败标签——它唯一的
    作用是决定人先看哪几道题。让分数参与判定的代价很实在：8/23 那批 24 道
    失败里 13 道只栽在分数上，其中 5 道其实是检索一条都没捞到，而另一批题
    则因为"同一份记录查了两次"被判死。两种情况都需要人看，但严重程度天差
    地别，混在一个标签里就都看不见了。
    """
    from app.evaluation.agent_assessment import assess_agent_case
    from app.schemas.agent_evaluation import AgentCaseExpected, AgentToolCallEvidence

    expected = AgentCaseExpected(
        response_mode="reply",
        required_tools=[
            AgentExpectedToolCall(name="get_recent_checkins", max_calls=1)
        ],
    )
    _, checks, _ = assess_agent_case(
        expected=expected,
        response="8/19 完成 2 项、8/20 完成 4 项，缺得最多的是运动。",
        tool_calls=[
            AgentToolCallEvidence(
                name="get_recent_checkins", args={}, result={"n": 2}, ok=True
            ),
            AgentToolCallEvidence(
                name="get_recent_checkins", args={}, result={"n": 2}, ok=True
            ),
            AgentToolCallEvidence(
                name="get_weight_trend", args={}, result={"change_kg": -0.4}, ok=True
            ),
        ],
    )
    # 多查一次打卡记录 + 额外看了体重趋势：都不判死
    assert all(item.severity != "critical" or item.passed for item in checks)


@pytest.mark.asyncio
async def test_a_failed_case_always_names_the_line_it_crossed(tmp_path) -> None:
    """判死和判死的理由必须对得上，两个方向都要对。

    RA-041 曾经整题失败而九项指标全绿——判死的理由（"回复里必须出现编号71"）
    只存在于失败列表里。反过来也踩过：题被标失败，而失败原因里只有一条分数
    低于参考线。看报告的人两种情况都无从下手。
    """
    reports_root = tmp_path / "reply-agent-evaluations"
    exit_code = await async_main(
        SimpleNamespace(
            mock=True,
            run_id="reply-agent-invariant-mock",
            reports_root=reports_root,
            dataset=None,
        )
    )
    assert exit_code == 0
    report = ReplyAgentEvaluationReportStore(reports_root).get_run(
        "reply-agent-invariant-mock"
    )

    for case in report.cases:
        crossed = [item for item in case.failure_reasons if item.severity == "critical"]
        assert (case.status == "failed") is bool(crossed), (
            f"{case.case_id}: 状态={case.status}，越界理由={[i.code for i in crossed]}"
        )
        if case.status == "failed":
            keys = {metric.key for metric in case.metrics if metric.passed is False}
            assert keys, f"{case.case_id}: 判死了却没有任何一行指标标红"

    flagged = {item.case_id for item in report.summary.boundary_violations}
    assert flagged == {case.case_id for case in report.cases if case.status == "failed"}
    # 低分名单不只收零分：真正常见的失败是"漏了一个知识点"这种半分题
    assert report.summary.lowest_scores
    assert report.summary.lowest_scores == sorted(
        report.summary.lowest_scores, key=lambda item: item.score
    ) or all(
        a.score <= b.score
        for a, b in zip(
            report.summary.lowest_scores, report.summary.lowest_scores[1:]
        )
    )
