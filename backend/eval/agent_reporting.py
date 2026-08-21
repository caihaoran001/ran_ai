from __future__ import annotations

from datetime import datetime, timezone

from app.schemas.agent_evaluation import (
    AgentEvaluationCaseResult,
    AgentEvaluationCounts,
    AgentEvaluationMetrics,
    AgentEvaluationSummary,
    AgentRateMetric,
)

if __package__:
    from .reporting import atomic_write_json
else:
    from reporting import atomic_write_json


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _rate(passed: int, total: int) -> AgentRateMetric:
    return AgentRateMetric(
        value=(passed / total) if total else None,
        numerator=passed,
        denominator=total,
    )


def empty_metrics() -> AgentEvaluationMetrics:
    return AgentEvaluationMetrics(
        task_pass_rate=AgentRateMetric(),
        tool_selection_accuracy=AgentRateMetric(),
        argument_accuracy=AgentRateMetric(),
        unauthorized_action_rate=AgentRateMetric(),
        literal_constraint_pass_rate=AgentRateMetric(),
        judge_pass_rate=AgentRateMetric(),
        judge_task_completion_rate=AgentRateMetric(),
        judge_groundedness_rate=AgentRateMetric(),
        judge_clarification_rate=AgentRateMetric(),
        judge_expression_rate=AgentRateMetric(),
    )


def aggregate_agent_summary(
    *,
    base: AgentEvaluationSummary,
    cases: list[AgentEvaluationCaseResult],
    total: int,
    status: str = "completed",
    error: str | None = None,
) -> AgentEvaluationSummary:
    completed = [case for case in cases if case.status != "error"]
    tool_selection = [
        all(
            check.passed
            for check in case.deterministic_checks
            if check.code.startswith("required_tool_")
            or check.code in {"forbidden_tools", "additional_tools"}
        )
        for case in completed
    ]
    arguments = [
        check.passed
        for case in completed
        for check in case.deterministic_checks
        if check.code.startswith("tool_args_")
    ]
    unauthorized = [
        not check.passed
        for case in completed
        for check in case.deterministic_checks
        if check.code == "forbidden_tools"
    ]
    literal_constraints = [
        all(
            check.passed
            for check in case.deterministic_checks
            if check.code in {"required_literals", "forbidden_literals"}
        )
        for case in completed
        if case.expected.required_literals or case.expected.forbidden_literals
    ]
    judged = [case.judge.passed is True for case in cases if case.judge.passed is not None]
    dimension_results: dict[str, list[bool]] = {
        "task_completion": [],
        "groundedness": [],
        "clarification": [],
        "expression": [],
    }
    for case in cases:
        for dimension in case.judge.dimensions:
            if dimension.passed is not None:
                dimension_results[dimension.key].append(dimension.passed is True)
    now = utc_now()
    counts = AgentEvaluationCounts(
        total=total,
        completed=len(cases),
        passed=sum(case.status == "passed" for case in cases),
        failed=sum(case.status == "failed" for case in cases),
        errors=sum(case.status == "error" for case in cases),
    )
    return base.model_copy(
        update={
            "status": status,
            "updated_at": now,
            "finished_at": now if status != "running" else None,
            "counts": counts,
            "metrics": AgentEvaluationMetrics(
                task_pass_rate=_rate(counts.passed, len(completed)),
                tool_selection_accuracy=_rate(sum(tool_selection), len(tool_selection)),
                argument_accuracy=_rate(sum(arguments), len(arguments)),
                unauthorized_action_rate=_rate(sum(unauthorized), len(unauthorized)),
                literal_constraint_pass_rate=_rate(
                    sum(literal_constraints), len(literal_constraints)
                ),
                judge_pass_rate=_rate(sum(judged), len(judged)),
                judge_task_completion_rate=_rate(
                    sum(dimension_results["task_completion"]),
                    len(dimension_results["task_completion"]),
                ),
                judge_groundedness_rate=_rate(
                    sum(dimension_results["groundedness"]),
                    len(dimension_results["groundedness"]),
                ),
                judge_clarification_rate=_rate(
                    sum(dimension_results["clarification"]),
                    len(dimension_results["clarification"]),
                ),
                judge_expression_rate=_rate(
                    sum(dimension_results["expression"]),
                    len(dimension_results["expression"]),
                ),
            ),
            "error": error,
        }
    )


def write_agent_report(path, report) -> None:
    atomic_write_json(path / "report.json", report.model_dump(mode="json"))
