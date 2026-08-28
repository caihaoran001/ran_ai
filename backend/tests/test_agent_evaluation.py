from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from importlib.util import find_spec
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.api.routes.agent_evaluations import get_report_store
from app.core.config import get_settings
from app.evaluation.agent_assessment import (
    READ_ONLY_TOOLS,
    STATE_CHANGING_TOOLS,
    assess_agent_case,
)
from app.evaluation.agent_cases import load_agent_eval_suite
from app.schemas.agent_evaluation import (
    AgentCaseActual,
    AgentCaseExpected,
    AgentEvaluationCaseResult,
    AgentEvaluationCounts,
    AgentEvaluationMetrics,
    AgentEvaluationRunDetail,
    AgentEvaluationSummary,
    AgentExpectedStringArgument,
    AgentExpectedToolCall,
    AgentFrameworkMetric,
    AgentJudgePolicy,
    AgentJudgeResult,
    AgentRateMetric,
    AgentToolCallEvidence,
)
from app.services.agent_evaluation_reports import AgentEvaluationReportStore
from eval.agent_costs import AgentEvaluationCostLedger, install_agent_usage_collector
from eval.run_agent_eval import IsolatedAgentGraph, SandboxTool, _mock_tool_calls


def _metrics() -> AgentEvaluationMetrics:
    perfect = AgentRateMetric(value=1.0, numerator=1, denominator=1)
    zero = AgentRateMetric(value=0.0, numerator=0, denominator=1)
    return AgentEvaluationMetrics(
        task_pass_rate=perfect,
        tool_selection_accuracy=perfect,
        argument_accuracy=perfect,
        unauthorized_action_rate=zero,
        literal_constraint_pass_rate=perfect,
        judge_pass_rate=perfect,
        judge_task_completion_rate=perfect,
        judge_groundedness_rate=perfect,
        judge_clarification_rate=perfect,
        judge_expression_rate=perfect,
    )


def _summary(run_id: str, *, status: str = "completed", minute: int = 0):
    timestamp = datetime(2026, 8, 21, 8, minute, tzinfo=timezone.utc)
    return AgentEvaluationSummary(
        run_id=run_id,
        run_mode="mock",
        status=status,
        started_at=timestamp,
        updated_at=timestamp,
        finished_at=timestamp if status == "completed" else None,
        git_sha="1234567",
        dataset_id="agent-task-core-v1",
        dataset_version=1,
        repeat_count=2,
        model="mock-model",
        evaluation_framework="deepeval",
        evaluation_framework_version="4.1.5",
        judge_policy=AgentJudgePolicy(
            status="mock",
            enforced=False,
            model="mock-judge",
            reason="只验证报告链路",
        ),
        counts=AgentEvaluationCounts(
            total=1,
            completed=1 if status == "completed" else 0,
            passed=1 if status == "completed" else 0,
            failed=0,
            errors=0,
        ),
        metrics=_metrics(),
        estimated_cost_yuan=0,
    )


def _report(run_id: str) -> AgentEvaluationRunDetail:
    expected = AgentCaseExpected(
        response_mode="reply",
        required_tools=[
            AgentExpectedToolCall(
                name="get_service_progress",
                args_subset={},
            )
        ],
        required_literals=["第12天"],
    )
    actual, checks, failures = assess_agent_case(
        expected=expected,
        response="现在是服务第12天。",
        tool_calls=[
            AgentToolCallEvidence(
                name="get_service_progress",
                args={},
                result={"service_day": 12},
                ok=True,
                elapsed_ms=9,
            )
        ],
    )
    case = AgentEvaluationCaseResult(
        case_id="agent-service-progress",
        title="查询服务进度",
        origin="production_derived",
        scenario="data_lookup",
        tags=["progress"],
        round_index=1,
        status="passed",
        history=[],
        user_query="现在是第几天？",
        expected=expected,
        actual=actual,
        deterministic_checks=checks,
        judge=AgentJudgeResult(
            status="mock",
            passed=True,
            framework_metrics=[
                AgentFrameworkMetric(
                    key="tool_correctness",
                    label="工具调用精确匹配",
                    kind="deterministic",
                    score=1,
                    threshold=1,
                    passed=True,
                    reason="工具名称完全一致",
                )
            ],
        ),
        failure_reasons=failures,
        elapsed_ms=20,
    )
    return AgentEvaluationRunDetail(summary=_summary(run_id), cases=[case])


def _write_json(path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_fixed_agent_suite_has_balanced_cases_and_repeats() -> None:
    suite = load_agent_eval_suite()

    assert suite.suite_id == "agent-task-core-v3"
    assert suite.version == 3
    assert len(suite.cases) == 30
    assert suite.repeat_count == 2
    assert sum(case.origin == "production_derived" for case in suite.cases) == 15
    assert sum(case.origin == "boundary" for case in suite.cases) == 15
    assert any(case.expected.forbidden_tools for case in suite.cases)
    assert any(case.expected.response_mode == "no_reply" for case in suite.cases)


def test_task_completion_description_only_contains_user_visible_outcome() -> None:
    from eval.agent_judge import _business_outcome_description

    expected = AgentCaseExpected(
        response_mode="reply",
        required_tools=[
            AgentExpectedToolCall(name="read_delivery_document", args_subset={})
        ],
        forbidden_tools=["set_checkin_checklist"],
        required_literals=["96"],
        required_facts=["第14天报告重点是睡眠稳定、午后困倦减少"],
    )

    description = _business_outcome_description(expected)

    assert "第14天报告重点是睡眠稳定、午后困倦减少" in description
    assert "96" in description
    assert "read_delivery_document" not in description
    assert "set_checkin_checklist" not in description
    assert "由独立的确定性指标核对" in description
    # 说清"拿不到调用记录"，而不只是"不要判"。禁令句压不住它看得见的东西：
    # 这把尺子曾因为在证据里看见工具名，自行长出"回复得体现调用了它"的要求。
    assert "拿不到调用记录" in description


def test_numeric_literal_accepts_basic_chinese_digit_form() -> None:
    expected = AgentCaseExpected(response_mode="reply", required_literals=["3"])

    _, checks, failures = assess_agent_case(
        expected=expected,
        response="昨天完成了三项。",
        tool_calls=[],
    )

    literal_check = next(item for item in checks if item.code == "required_literals")
    assert literal_check.passed is True
    assert all(item.code != "required_literals" for item in failures)


def test_task_completion_uses_user_visible_geval_not_trace_outcome() -> None:
    from eval.agent_judge import AgentQualityJudge, TASK_COMPLETION_THRESHOLD

    captured: dict = {}

    class FakeGEval:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    judge = object.__new__(AgentQualityJudge)
    judge._model = object()
    judge._api = {
        "GEval": FakeGEval,
        "SingleTurnParams": SimpleNamespace(
            INPUT="input",
            ACTUAL_OUTPUT="actual_output",
            EXPECTED_OUTPUT="expected_output",
            CONTEXT="context",
        ),
    }
    expected = AgentCaseExpected(
        response_mode="reply",
        required_tools=[
            AgentExpectedToolCall(name="read_delivery_document", args_subset={})
        ],
        required_facts=["第14天报告重点是睡眠稳定"],
    )

    metric = judge._task_completion_metric(
        expected,
        threshold=0.8,
        strict_mode=False,
    )

    assert isinstance(metric, FakeGEval)
    assert captured["name"] == "用户可见任务完成度"
    assert captured["evaluation_params"] == [
        "input",
        "actual_output",
        "expected_output",
        "context",
    ]
    steps = "\n".join(captured["evaluation_steps"])
    assert "工具返回只是核对回复真伪的证据" in steps
    assert "绝不代表该信息已向用户传达" in steps
    assert "read_delivery_document" not in steps
    assert "第14天报告重点是睡眠稳定" in steps
    assert "GEval 要求原始分数使用 0 至 10 分制" in steps
    assert "符合回复方式给 10 分" in steps
    assert captured["threshold"] == 0.8
    assert captured["strict_mode"] is False
    assert TASK_COMPLETION_THRESHOLD == 0.9


@pytest.mark.asyncio
async def test_isolated_graph_replaces_every_tool_execution() -> None:
    suite = load_agent_eval_suite()
    settings = get_settings().model_copy(
        update={"ai_api_key": "agent-eval-test-key", "web_search_enabled": False}
    )
    graph = IsolatedAgentGraph(case=suite.cases[0], settings=settings)
    schema_names = {tool.name for tool in graph.tools}

    fixture_tools = {
        name
        for case in suite.cases
        for name in (
            [item.name for item in case.expected.required_tools]
            + case.expected.forbidden_tools
            + case.expected.allowed_additional_tools
            + [item.name for item in case.tool_stubs]
        )
    }
    assert fixture_tools <= schema_names
    assert set(graph.tools_by_name) == schema_names
    assert all(isinstance(tool, SandboxTool) for tool in graph.tools_by_name.values())

    sandbox = graph.tools_by_name["get_service_progress"]
    result = await sandbox.ainvoke({})
    assert result == {
        "ok": True,
        "status": "in_service",
        "service_day": 12,
        "total_days": 28,
    }
    assert sandbox.records == [{"args": {}, "result": result}]

    knowledge = await graph._build_knowledge_context(
        "改写问题",
        fallback_query="用户原问题",
    )
    assert knowledge == (
        suite.cases[0].knowledge_context,
        suite.cases[0].knowledge_hit_count,
        [],
        "改写问题",
        False,
        suite.cases[0].knowledge_hit_count,
    )


def test_deterministic_assessment_rejects_wrong_args_and_forbidden_write() -> None:
    expected = AgentCaseExpected(
        response_mode="reply",
        required_tools=[
            AgentExpectedToolCall(
                name="get_recent_checkins",
                args_subset={"start_date": "2026-08-20", "end_date": "2026-08-20"},
            )
        ],
        forbidden_tools=["set_checkin_checklist"],
    )
    _, checks, failures = assess_agent_case(
        expected=expected,
        response="昨天打卡了。",
        tool_calls=[
            AgentToolCallEvidence(
                name="get_recent_checkins",
                args={"start_date": "2026-08-19", "end_date": "2026-08-19"},
                result=[],
                ok=True,
            ),
            AgentToolCallEvidence(
                name="set_checkin_checklist",
                args={"questions": ["喝水"]},
                result={"ok": False},
                ok=False,
            ),
        ],
    )

    by_code = {item.code: item for item in checks}
    assert by_code["tool_args_get_recent_checkins"].passed is False
    assert by_code["forbidden_tools"].passed is False
    assert {item.code for item in failures} >= {
        "tool_args_get_recent_checkins",
        "forbidden_tools",
    }


def test_deterministic_assessment_allows_only_named_auxiliary_tools() -> None:
    expected = AgentCaseExpected(
        response_mode="reply",
        required_tools=[
            AgentExpectedToolCall(name="get_weight_trend", args_subset={"days": 7})
        ],
        allowed_additional_tools=["skill", "read_skill_file"],
    )
    _, allowed_checks, allowed_failures = assess_agent_case(
        expected=expected,
        response="近七天体重下降0.8公斤。",
        tool_calls=[
            AgentToolCallEvidence(
                name="skill",
                args={"name": "checkin_analysis"},
                result="体重趋势分析规则",
                ok=True,
            ),
            AgentToolCallEvidence(
                name="get_weight_trend",
                args={"days": 7},
                result={"change_kg": -0.8},
                ok=True,
            ),
        ],
    )
    allowed_by_code = {item.code: item for item in allowed_checks}
    assert allowed_by_code["additional_read_tools"].passed is True
    assert "明确允许" in allowed_by_code["additional_read_tools"].message
    assert allowed_failures == []

    _, rejected_checks, rejected_failures = assess_agent_case(
        expected=expected,
        response="近七天体重下降0.8公斤。",
        tool_calls=[
            AgentToolCallEvidence(
                name="get_weight_trend",
                args={"days": 7},
                result={"change_kg": -0.8},
                ok=True,
            ),
            AgentToolCallEvidence(
                name="get_service_progress",
                args={},
                result={"service_day": 12},
                ok=True,
            ),
        ],
    )
    # 计划外多查了一个只读工具：记账，不判越界。事先猜的工具清单不可能穷尽所有
    # 合理路径，而只读查询既不改数据也不外发，代价只有一次调用的钱。
    rejected_by_code = {item.code: item for item in rejected_checks}
    read_extra = rejected_by_code["additional_read_tools"]
    assert read_extra.passed is False
    assert read_extra.severity == "info"
    assert "get_service_progress" in read_extra.message
    assert rejected_by_code["additional_write_tools"].passed is True
    assert {item.code for item in rejected_failures} == {"additional_read_tools"}
    assert all(item.severity != "critical" for item in rejected_failures)


def test_unplanned_write_tool_is_a_boundary_violation() -> None:
    """只读放宽不能顺带放过写操作。

    RA-047 就是这个形状：回复文字说"需要 @ 我才能改"，手上却真的调了修改清单
    的工具。这类调用改到真实数据，一次都不能算记账。
    """
    expected = AgentCaseExpected(response_mode="reply")
    _, checks, failures = assess_agent_case(
        expected=expected,
        response="更新打卡追问需要班主任在群里 @ 我。",
        tool_calls=[
            AgentToolCallEvidence(
                name="set_checkin_checklist",
                args={"items": ["饮水", "运动"]},
                result={"ok": True},
                ok=True,
            )
        ],
    )
    by_code = {item.code: item for item in checks}
    assert by_code["additional_write_tools"].passed is False
    assert by_code["additional_write_tools"].severity == "critical"
    assert "set_checkin_checklist" in by_code["additional_write_tools"].message
    assert any(item.severity == "critical" for item in failures)


def test_unregistered_tool_counts_as_state_changing() -> None:
    """新工具上线时忘了归类，宁可误报越界也不要默默放过一个可能在改数据的调用。"""
    _, checks, _ = assess_agent_case(
        expected=AgentCaseExpected(response_mode="reply"),
        response="好。",
        tool_calls=[
            AgentToolCallEvidence(
                name="brand_new_tool", args={}, result={"ok": True}, ok=True
            )
        ],
    )
    by_code = {item.code: item for item in checks}
    assert by_code["additional_write_tools"].passed is False
    assert "brand_new_tool" in by_code["additional_write_tools"].message


def test_argument_accuracy_is_independent_from_call_count() -> None:
    expected = AgentCaseExpected(
        response_mode="reply",
        required_tools=[
            AgentExpectedToolCall(
                name="get_recent_checkins",
                args_subset={"days": 7},
                max_calls=1,
            )
        ],
    )
    _, checks, failures = assess_agent_case(
        expected=expected,
        response="最近运动打卡最容易漏。",
        tool_calls=[
            AgentToolCallEvidence(
                name="get_recent_checkins",
                args={"days": 7},
                result={"most_missing": "运动"},
                ok=True,
            ),
            AgentToolCallEvidence(
                name="get_recent_checkins",
                args={"date_from": "2026-08-19", "date_to": "2026-08-20"},
                result={"most_missing": "运动"},
                ok=True,
            ),
        ],
    )
    # 次数只卡下限：调够了就算办到了事。多调是浪费，单独记一条成本提示，
    # 不再判失败——否则"同一份记录查了两次但答案完全正确"会变成失败。
    by_code = {item.code: item for item in checks}
    assert by_code["required_tool_get_recent_checkins"].passed is True
    assert by_code["tool_args_get_recent_checkins"].passed is True
    over = by_code["tool_calls_over_plan_get_recent_checkins"]
    assert over.passed is False
    assert over.severity == "info"
    assert "仅多花调用成本" in over.message
    assert all(item.severity != "critical" for item in failures)


def test_string_argument_rules_accept_paraphrase_and_reject_invented_fact() -> None:
    expected = AgentCaseExpected(
        response_mode="reply",
        required_tools=[
            AgentExpectedToolCall(
                name="search_success_case",
                string_args={
                    "query": AgentExpectedStringArgument(
                        required_literals=["体重", "瘦不下来"],
                        required_any_groups=[
                            ["一周", "7天"],
                            ["没有变化", "没动", "不变"],
                        ],
                        forbidden_literals=["低碳", "饮食"],
                    )
                },
            )
        ],
    )

    def argument_check(query: str):
        _, checks, _ = assess_agent_case(
            expected=expected,
            response="先别给自己下结论。",
            tool_calls=[
                AgentToolCallEvidence(
                    name="search_success_case",
                    args={"query": query},
                    result="相似案例",
                    ok=True,
                )
            ],
        )
        return {item.code: item for item in checks}["tool_args_search_success_case"]

    assert argument_check("连续7天体重没动，担心自己瘦不下来").passed is True
    assert argument_check("低碳饮食一周体重没动，担心瘦不下来").passed is False


def test_v3_sandbox_results_match_runtime_tool_contracts() -> None:
    suite = load_agent_eval_suite()
    by_id = {case.case_id: case for case in suite.cases}

    restore = by_id["agent-restore-checklist"].tool_stubs[0].results[0]
    assert restore["restored"] is True
    assert len(restore["questions"]) == 5

    continuation = by_id["agent-continue-service"].tool_stubs[0].results[0]
    assert continuation["continued"] is True
    assert continuation["service_day"] == 31
    assert continuation["service_day_text"] == "第 31 天"

    success_case = by_id["agent-success-case"]
    mock_calls = _mock_tool_calls(success_case)
    _, checks, failures = assess_agent_case(
        expected=success_case.expected,
        response="相似案例说明短期体重不动不等于瘦不下来。",
        tool_calls=mock_calls,
    )
    assert {item.code: item.passed for item in checks}[
        "tool_args_search_success_case"
    ] is True
    assert failures == []


@pytest.mark.asyncio
async def test_agent_cost_ledger_attributes_eight_concurrent_cases() -> None:
    ledger = AgentEvaluationCostLedger(get_settings())
    ready = 0
    all_ready = asyncio.Event()

    async def record(case_index: int) -> tuple[str, str | None]:
        nonlocal ready
        case_id = f"agent-concurrent-{case_index}"
        metric_key = f"metric_{case_index}"
        ledger.begin_case(case_id, 1)
        ready += 1
        if ready == 8:
            all_ready.set()
        await all_ready.wait()
        ledger.record_judge_model_usage(
            model="mock-judge",
            response=SimpleNamespace(
                model="mock-judge",
                usage=SimpleNamespace(
                    prompt_tokens=10,
                    completion_tokens=2,
                    total_tokens=12,
                ),
            ),
            elapsed_ms=1,
            success=True,
            error_type=None,
            metric_key=metric_key,
        )
        usage = ledger.case_usage(case_id, 1)
        ledger.end_case(case_id, 1)
        return case_id, usage.calls[0].metric_key

    recorded = await asyncio.gather(*(record(index) for index in range(8)))

    assert recorded == [
        (f"agent-concurrent-{index}", f"metric_{index}") for index in range(8)
    ]
    assert all(
        ledger.case_usage(f"agent-concurrent-{index}", 1).total.call_count == 1
        for index in range(8)
    )


@pytest.mark.asyncio
async def test_evaluation_cost_ledger_separates_agent_judge_and_marks_gaps(
    tmp_path,
) -> None:
    settings = get_settings().model_copy(
        update={
            "ai_prompt_cost_per_1m_tokens": 2.0,
            "ai_completion_cost_per_1m_tokens": 8.0,
            "ai_prompt_cache_hit_cost_per_1m_tokens": 0.5,
            "ai_premium_models": "",
            "ai_peak_price_multiplier": 1.0,
        }
    )
    call_log_path = tmp_path / "calls.jsonl"
    ledger = AgentEvaluationCostLedger(settings, call_log_path=call_log_path)
    ledger.begin_case("agent-service-progress", 1)
    await ledger.record_agent_model_usage(
        model="requested-agent-model",
        scenario="reply_agent",
        elapsed_ms=120,
        response=SimpleNamespace(
            usage_metadata={
                "input_tokens": 1000,
                "output_tokens": 100,
                "total_tokens": 1100,
                "input_token_details": {"cache_read": 200},
            },
            response_metadata={"model_name": "actual-agent-model"},
        ),
        request_messages=[{"role": "user", "content": "现在第几天？"}],
        success=True,
    )
    ledger.record_judge_model_usage(
        model="requested-judge-model",
        response=SimpleNamespace(
            model="actual-judge-model",
            usage=SimpleNamespace(
                prompt_tokens=2000,
                completion_tokens=200,
                total_tokens=2200,
                prompt_tokens_details=SimpleNamespace(cached_tokens=0),
            ),
        ),
        elapsed_ms=80,
        success=True,
        error_type=None,
        metric_key="groundedness",
        request_payload={"messages": [{"role": "user", "content": "judge"}]},
    )

    complete = ledger.case_usage("agent-service-progress", 1)
    assert complete.agent.call_count == 1
    assert complete.agent.estimated_cost_yuan == 0.0025
    assert complete.judge.call_count == 1
    assert complete.judge.estimated_cost_yuan == 0.0056
    assert complete.total.estimated_cost_yuan == 0.0081
    assert complete.total.prompt_cache_hit_tokens == 200
    assert complete.calls[1].metric_key == "groundedness"
    assert complete.calls[0].model == "actual-agent-model"

    ledger.record_judge_model_usage(
        model="requested-judge-model",
        response=None,
        elapsed_ms=31,
        success=False,
        error_type="APITimeoutError",
        metric_key="expression",
    )
    incomplete = ledger.case_usage("agent-service-progress", 1)
    ledger.end_case("agent-service-progress", 1)

    assert incomplete.total.call_count == 3
    assert incomplete.total.usage_reported_calls == 2
    assert incomplete.total.costed_calls == 2
    assert incomplete.total.complete is False
    assert incomplete.total.known_estimated_cost_yuan == 0.0081
    assert incomplete.total.estimated_cost_yuan is None
    assert incomplete.calls[-1].error_type == "APITimeoutError"
    call_evidence = [
        json.loads(line)
        for line in call_log_path.read_text(encoding="utf-8").splitlines()
    ]
    assert len(call_evidence) == 3
    assert call_evidence[0]["request"][0]["content"] == "现在第几天？"
    assert call_evidence[1]["stage_key"] == "groundedness"
    assert call_evidence[1]["response"]["model"] == "actual-judge-model"
    assert call_evidence[2]["error_message"] == "APITimeoutError"


def test_agent_usage_collector_is_scoped_and_restorable() -> None:
    import app.services.agent.graph as graph_module

    ledger = AgentEvaluationCostLedger(get_settings())
    original = graph_module.record_model_usage
    restore = install_agent_usage_collector(ledger)
    try:
        assert graph_module.record_model_usage == ledger.record_agent_model_usage
    finally:
        restore()
    assert graph_module.record_model_usage is original


@pytest.mark.asyncio
async def test_deepeval_adapter_records_framework_version_and_tool_metric() -> None:
    if find_spec("deepeval") is None:
        pytest.skip("DeepEval is installed only in the dedicated evaluation image")
    from eval.agent_judge import AgentQualityJudge

    expected = AgentCaseExpected(
        response_mode="reply",
        required_tools=[
            AgentExpectedToolCall(name="get_service_progress", args_subset={})
        ],
        forbidden_tools=["set_checkin_checklist"],
        required_facts=["说明当前服务天数"],
    )
    actual, _, _ = assess_agent_case(
        expected=expected,
        response="现在是服务第12天。",
        tool_calls=[
            AgentToolCallEvidence(
                name="get_service_progress",
                args={},
                result={"service_day": 12},
                ok=True,
            ),
            AgentToolCallEvidence(
                name="set_checkin_checklist",
                args={"questions": ["喝水"]},
                result={"ok": False},
                ok=False,
            ),
        ],
    )
    judge = AgentQualityJudge(get_settings(), mock=True)
    try:
        result = await judge.judge(
            history=[],
            user_query="现在第几天？",
            expected=expected,
            actual=actual,
        )
    finally:
        await judge.close()

    framework = {item.key: item for item in result.framework_metrics}
    assert judge.framework_version == "4.1.5"
    assert framework["tool_correctness"].passed is False
    assert framework["tool_correctness"].kind == "deterministic"
    assert framework["task_completion"].kind == "llm"
    assert os.environ["DEEPEVAL_TELEMETRY_OPT_OUT"] == "1"
    assert os.environ["DEEPEVAL_FILE_SYSTEM"] == "READ_ONLY"


@pytest.mark.asyncio
async def test_deepeval_metric_retries_one_invalid_json_response() -> None:
    if find_spec("deepeval") is None:
        pytest.skip("DeepEval is installed only in the dedicated evaluation image")
    from eval.agent_judge import AgentQualityJudge

    class FlakyMetric:
        _ran_ai_key = "task_completion"
        _ran_ai_label = "任务完成"
        _ran_ai_kind = "llm"
        score = 1.0
        threshold = 0.9
        success = True
        reason = "第二次返回合法 JSON"
        attempts = 0

        async def a_measure(self, *_args, **_kwargs) -> None:
            self.attempts += 1
            if self.attempts == 1:
                raise ValueError("Evaluation LLM outputted an invalid JSON")

    judge = AgentQualityJudge(get_settings(), mock=True)
    metric = FlakyMetric()
    try:
        result = await judge._measure(metric, object())
    finally:
        await judge.close()

    assert metric.attempts == 2
    assert result.passed is True
    assert result.reason == "第二次返回合法 JSON"


@pytest.mark.asyncio
async def test_deepeval_openai_adapter_records_judge_usage(monkeypatch) -> None:
    if find_spec("deepeval") is None:
        pytest.skip("DeepEval is installed only in the dedicated evaluation image")
    import eval.agent_judge as judge_module

    response = SimpleNamespace(
        model="actual-judge-model",
        usage=SimpleNamespace(
            prompt_tokens=500,
            completion_tokens=50,
            total_tokens=550,
            prompt_tokens_details=SimpleNamespace(cached_tokens=100),
        ),
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))],
    )

    class SyncClient:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=lambda **_kwargs: response)
            )

        def close(self) -> None:
            return None

    class AsyncCompletions:
        async def create(self, **_kwargs):
            return response

    class AsyncClient:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=AsyncCompletions())

        async def close(self) -> None:
            return None

    monkeypatch.setattr(judge_module, "OpenAI", SyncClient)
    monkeypatch.setattr(judge_module, "AsyncOpenAI", AsyncClient)
    settings = get_settings().model_copy(
        update={
            "ai_api_key": "agent-eval-test-key",
            "ai_prompt_cost_per_1m_tokens": 2.0,
            "ai_completion_cost_per_1m_tokens": 8.0,
            "ai_prompt_cache_hit_cost_per_1m_tokens": 0.5,
            "ai_premium_models": "",
            "ai_peak_price_multiplier": 1.0,
        }
    )
    ledger = AgentEvaluationCostLedger(settings)
    ledger.begin_case("agent-service-progress", 1)
    judge = judge_module.AgentQualityJudge(
        settings,
        mock=False,
        cost_ledger=ledger,
    )
    try:
        judge._model._ran_ai_metric_key = "task_completion"
        assert await judge._model.a_generate("test prompt") == '{"ok": true}'
    finally:
        await judge.close()
        ledger.end_case("agent-service-progress", 1)

    usage = ledger.case_usage("agent-service-progress", 1)
    assert usage.judge.call_count == 1
    assert usage.judge.total_tokens == 550
    assert usage.judge.prompt_cache_hit_tokens == 100
    assert usage.judge.estimated_cost_yuan == 0.00125
    assert usage.calls[0].metric_key == "task_completion"
    assert usage.calls[0].model == "actual-judge-model"


@pytest.mark.asyncio
async def test_agent_evaluation_api_lists_status_and_returns_evidence(
    admin_client, tmp_path
) -> None:
    app = admin_client._transport.app
    store = AgentEvaluationReportStore(tmp_path)
    app.dependency_overrides[get_report_store] = lambda: store
    _write_json(
        tmp_path / "agent-eval-completed" / "report.json",
        _report("agent-eval-completed").model_dump(mode="json"),
    )
    _write_json(
        tmp_path / "agent-eval-running" / "status.json",
        _summary("agent-eval-running", status="running", minute=1).model_dump(
            mode="json"
        ),
    )

    listing = await admin_client.get("/agent-evaluations")
    detail = await admin_client.get("/agent-evaluations/agent-eval-completed")

    assert listing.status_code == 200
    assert [item["run_id"] for item in listing.json()["items"]] == [
        "agent-eval-running",
        "agent-eval-completed",
    ]
    assert detail.status_code == 200
    assert detail.json()["summary"]["evaluation_framework"] == "deepeval"
    assert detail.json()["summary"]["evaluation_framework_version"] == "4.1.5"
    assert detail.json()["summary"]["usage"]["tracking_available"] is False
    case = detail.json()["cases"][0]
    assert case["actual"]["tool_calls"][0]["result"] == {"service_day": 12}
    assert case["deterministic_checks"][0]["code"] == "response_mode"
    assert case["judge"]["framework_metrics"][0]["key"] == "tool_correctness"
    assert case["usage"]["tracking_available"] is False


@pytest.mark.asyncio
async def test_agent_evaluation_api_rejects_bad_paths_and_invalid_reports(
    admin_client, tmp_path
) -> None:
    app = admin_client._transport.app
    store = AgentEvaluationReportStore(tmp_path)
    app.dependency_overrides[get_report_store] = lambda: store
    _write_json(tmp_path / "broken" / "report.json", {"not": "a report"})

    missing = await admin_client.get("/agent-evaluations/..%2Fsecrets")
    broken = await admin_client.get("/agent-evaluations/broken")
    listing = await admin_client.get("/agent-evaluations")

    assert missing.status_code == 404
    assert broken.status_code == 422
    assert listing.status_code == 200
    assert listing.json() == {"items": []}


def test_every_registered_tool_is_classified_read_only_or_state_changing() -> None:
    """新工具上线时必须归类，否则评测放不放过它就成了偶然。

    评测靠这份分类决定"多调一次"算记账还是算越界。漏归类的工具会被
    `changes_state` 当成写操作报出来——那是安全的默认，但会误报，所以这里
    盯着分类和产品实际注册的工具保持一致。
    """
    import re

    tools_dir = (
        Path(__file__).resolve().parents[1] / "src" / "app" / "services" / "agent" / "tools"
    )
    registered = {
        match
        for path in tools_dir.glob("*.py")
        for match in re.findall(r'name="([a-z_]+)"', path.read_text(encoding="utf-8"))
    }
    assert registered, "没有扫到任何工具定义，说明扫描方式失效了"

    classified = READ_ONLY_TOOLS | STATE_CHANGING_TOOLS
    assert not registered - classified, (
        f"这些工具还没归类为只读或写操作：{sorted(registered - classified)}"
    )
    assert not classified - registered, (
        f"这些名字已经不是产品里注册的工具了：{sorted(classified - registered)}"
    )
    assert not (READ_ONLY_TOOLS & STATE_CHANGING_TOOLS)


def test_task_completion_evidence_hides_tool_names() -> None:
    """判分材料里不出现工具名。

    这把尺子拿不到调用记录，却因为在证据里看见了工具名，自行长出"回复里得体现
    调用了它"的要求——RA-053 的理由原话是"符合任务要求，但未明确体现调用的
    continue_service 工具调用"，然后判 0 分。核对事实真伪只需要返回内容。
    """
    from eval.agent_judge import AgentQualityJudge

    judge = object.__new__(AgentQualityJudge)
    judge._api = {
        "ToolCall": lambda **kwargs: kwargs,
        "LLMTestCase": lambda **kwargs: kwargs,
    }
    test_case = judge._test_case(
        history=[],
        user_query="@燃人AI 这个用户继续服务",
        expected=AgentCaseExpected(
            response_mode="reply",
            required_tools=[AgentExpectedToolCall(name="continue_service")],
            required_facts=["已恢复连续服务，当前是第31天"],
        ),
        actual=AgentCaseActual(
            response_mode="reply",
            response="好的，已恢复连续服务，当前是第 31 天。",
            tool_calls=[
                AgentToolCallEvidence(
                    name="continue_service",
                    args={},
                    result={"ok": True, "service_day": 31},
                    ok=True,
                )
            ],
        ),
    )

    context = "\n".join(test_case["context"])
    assert "continue_service" not in context
    assert "31" in context, "返回内容本身必须留着，否则没法核对回复真伪"
    assert "不在本次判断范围内" in context
    # 需要按工具归因的证据忠实那把尺子仍能从工具调用列表拿到名字
    assert test_case["tools_called"][0]["name"] == "continue_service"
