from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from importlib.util import find_spec
from types import SimpleNamespace

import pytest

from app.api.routes.agent_evaluations import get_report_store
from app.core.config import get_settings
from app.evaluation.agent_assessment import assess_agent_case
from app.evaluation.agent_cases import load_agent_eval_suite
from app.schemas.agent_evaluation import (
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
    assert allowed_by_code["additional_tools"].passed is True
    assert "明确允许" in allowed_by_code["additional_tools"].message
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
    rejected_by_code = {item.code: item for item in rejected_checks}
    assert rejected_by_code["additional_tools"].passed is False
    assert "get_service_progress" in rejected_by_code["additional_tools"].message
    assert {item.code for item in rejected_failures} == {"additional_tools"}


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
    by_code = {item.code: item for item in checks}
    assert by_code["required_tool_get_recent_checkins"].passed is False
    assert by_code["tool_args_get_recent_checkins"].passed is True
    assert {item.code for item in failures} == {
        "required_tool_get_recent_checkins"
    }


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
async def test_evaluation_cost_ledger_separates_agent_judge_and_marks_gaps() -> None:
    settings = get_settings().model_copy(
        update={
            "ai_prompt_cost_per_1m_tokens": 2.0,
            "ai_completion_cost_per_1m_tokens": 8.0,
            "ai_prompt_cache_hit_cost_per_1m_tokens": 0.5,
            "ai_premium_models": "",
            "ai_peak_price_multiplier": 1.0,
        }
    )
    ledger = AgentEvaluationCostLedger(settings)
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
