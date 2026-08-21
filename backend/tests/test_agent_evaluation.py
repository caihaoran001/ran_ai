from __future__ import annotations

import json
from datetime import datetime, timezone

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
    AgentExpectedToolCall,
    AgentJudgePolicy,
    AgentJudgeResult,
    AgentRateMetric,
    AgentToolCallEvidence,
)
from app.services.agent_evaluation_reports import AgentEvaluationReportStore
from eval.run_agent_eval import IsolatedAgentGraph, SandboxTool


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
        judge=AgentJudgeResult(status="mock", passed=True),
        failure_reasons=failures,
        elapsed_ms=20,
    )
    return AgentEvaluationRunDetail(summary=_summary(run_id), cases=[case])


def _write_json(path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_fixed_agent_suite_has_balanced_cases_and_repeats() -> None:
    suite = load_agent_eval_suite()

    assert len(suite.cases) == 30
    assert suite.repeat_count == 2
    assert sum(case.origin == "production_derived" for case in suite.cases) == 15
    assert sum(case.origin == "boundary" for case in suite.cases) == 15
    assert any(case.expected.forbidden_tools for case in suite.cases)
    assert any(case.expected.response_mode == "no_reply" for case in suite.cases)


@pytest.mark.asyncio
async def test_isolated_graph_keeps_current_schemas_but_replaces_every_execution() -> None:
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
        _summary("agent-eval-running", status="running", minute=1).model_dump(mode="json"),
    )

    listing = await admin_client.get("/agent-evaluations")
    detail = await admin_client.get("/agent-evaluations/agent-eval-completed")

    assert listing.status_code == 200
    assert [item["run_id"] for item in listing.json()["items"]] == [
        "agent-eval-running",
        "agent-eval-completed",
    ]
    assert detail.status_code == 200
    case = detail.json()["cases"][0]
    assert case["actual"]["tool_calls"][0]["result"] == {"service_day": 12}
    assert case["deterministic_checks"][0]["code"] == "response_mode"


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
