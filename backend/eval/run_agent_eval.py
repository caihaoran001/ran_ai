from __future__ import annotations

import argparse
import asyncio
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.db.session import engine
from app.evaluation.agent_assessment import assess_agent_case
from app.evaluation.agent_cases import (
    AgentEvalCase,
    AgentToolStub,
    load_agent_eval_suite,
)
from app.schemas.agent_evaluation import (
    AgentEvaluationCaseResult,
    AgentEvaluationCounts,
    AgentEvaluationRunDetail,
    AgentEvaluationSummary,
    AgentEvaluationUsageSummary,
    AgentFailureReason,
    AgentJudgePolicy,
    AgentJudgeResult,
    AgentToolCallEvidence,
)
from app.services.agent.graph import RanrenAgentGraph
from app.services.agent.state import AgentRunContext

if __package__:
    from .agent_costs import AgentEvaluationCostLedger, install_agent_usage_collector
    from .agent_judge import AgentQualityJudge
    from .agent_reporting import (
        aggregate_agent_summary,
        empty_metrics,
        utc_now,
        write_agent_report,
    )
    from .read_only import install_read_only_guards
    from .reporting import atomic_write_json
else:
    from agent_costs import AgentEvaluationCostLedger, install_agent_usage_collector
    from agent_judge import AgentQualityJudge
    from agent_reporting import (
        aggregate_agent_summary,
        empty_metrics,
        utc_now,
        write_agent_report,
    )
    from read_only import install_read_only_guards
    from reporting import atomic_write_json


class SandboxTool:
    def __init__(self, stub: AgentToolStub | None):
        self._results = (
            list(stub.results)
            if stub is not None
            else [{"ok": False, "error": "AGENT_EVAL_TOOL_NOT_CONFIGURED"}]
        )
        self.records: list[dict[str, Any]] = []

    async def ainvoke(self, args: dict[str, Any]) -> Any:
        index = min(len(self.records), len(self._results) - 1)
        result = self._results[index]
        self.records.append({"args": dict(args or {}), "result": result})
        return result


class IsolatedAgentGraph(RanrenAgentGraph):
    def __init__(self, *, case: AgentEvalCase, settings):
        self._eval_case = case
        super().__init__(
            context=AgentRunContext(
                service_case_id=0,
                trigger_id=None,
                agent_run_id=None,
                run_kind="interactive",
                channel="agent-evaluation",
                group_id="agent-evaluation",
            ),
            settings=settings,
            include_reminder_tools=True,
            checkpointer=None,
        )
        stubs = {item.name: item for item in case.tool_stubs}
        self.sandbox_tools = {
            name: SandboxTool(stubs.get(name)) for name in self.tools_by_name
        }
        self.tools_by_name = dict(self.sandbox_tools)

    def _current_time_context(self) -> str:
        return self._eval_case.current_time_context

    async def _build_memory_context(self) -> str:
        return self._eval_case.memory_context

    async def _build_knowledge_context(
        self, query: str
    ) -> tuple[str, int | None, list[dict[str, Any]]]:
        return (
            self._eval_case.knowledge_context,
            self._eval_case.knowledge_hit_count,
            [],
        )

    async def _resolve_bot_nickname(self) -> str:
        return "燃人AI"

    def tool_evidence(
        self, traces: list[dict[str, Any]]
    ) -> list[AgentToolCallEvidence]:
        offsets: dict[str, int] = defaultdict(int)
        output: list[AgentToolCallEvidence] = []
        for trace in traces:
            name = str(trace.get("name") or "unknown")
            tool = self.sandbox_tools.get(name)
            result = None
            if tool is not None:
                offset = offsets[name]
                if offset < len(tool.records):
                    result = tool.records[offset]["result"]
                    offsets[name] += 1
            output.append(
                AgentToolCallEvidence(
                    name=name,
                    args=dict(trace.get("args") or {}),
                    result=result,
                    ok=bool(trace.get("ok")),
                    error=(str(trace.get("error")) if trace.get("error") else None),
                    elapsed_ms=max(0, int(trace.get("elapsed_ms") or 0)),
                )
            )
        return output


def _history_lines(case: AgentEvalCase) -> list[str]:
    labels = {"user": "用户", "staff": "工作人员", "assistant": "AI助教"}
    return [f"{labels[item.role]}：{item.content}" for item in case.history]


def _mock_tool_calls(case: AgentEvalCase) -> list[AgentToolCallEvidence]:
    stubs = {item.name: item for item in case.tool_stubs}
    calls = []
    for expected in case.expected.required_tools:
        result = stubs[expected.name].results[0]
        args = dict(expected.args_subset)
        for name, rule in expected.string_args.items():
            args[name] = " ".join(
                [
                    *rule.required_literals,
                    *(group[0] for group in rule.required_any_groups),
                ]
            )
        calls.append(
            AgentToolCallEvidence(
                name=expected.name,
                args=args,
                result=result,
                ok=not isinstance(result, dict) or result.get("ok") is not False,
                elapsed_ms=12,
            )
        )
    if case.case_id == "agent-checklist-no-mention" and case.expected.forbidden_tools:
        calls.append(
            AgentToolCallEvidence(
                name=case.expected.forbidden_tools[0],
                args={"questions": ["喝水", "运动", "睡眠", "情绪"]},
                result={"ok": False, "error": "需要明确@机器人"},
                ok=False,
                error="TOOL_RETURNED_FAILURE",
                elapsed_ms=8,
            )
        )
    return calls


def _mock_response(case: AgentEvalCase) -> str:
    if case.expected.response_mode == "no_reply":
        return "<no_reply>"
    if case.expected.required_literals:
        return "，".join(case.expected.required_literals) + "。"
    if case.expected.response_mode == "clarify":
        return "还缺一个关键信息，你说的是哪一天或哪一项？"
    return "已经按你刚才说的处理好了。"


async def _result_from_evidence(
    *,
    case: AgentEvalCase,
    round_index: int,
    response: str,
    tool_calls: list[AgentToolCallEvidence],
    judge: AgentQualityJudge,
    cost_ledger: AgentEvaluationCostLedger,
    mock_failure: bool,
    elapsed_ms: int,
) -> AgentEvaluationCaseResult:
    actual, checks, failures = assess_agent_case(
        expected=case.expected,
        response=response,
        tool_calls=tool_calls,
    )
    judged = await judge.judge(
        history=[item.model_dump() for item in case.history],
        user_query=case.user_query,
        expected=case.expected,
        actual=actual,
        mock_failure=mock_failure,
    )
    deterministic_passed = all(check.passed for check in checks)
    return AgentEvaluationCaseResult(
        case_id=case.case_id,
        title=case.title,
        origin=case.origin,
        scenario=case.scenario,
        tags=case.tags,
        round_index=round_index,
        status="passed" if deterministic_passed else "failed",
        history=case.history,
        user_query=case.user_query,
        expected=case.expected,
        actual=actual,
        deterministic_checks=checks,
        judge=judged,
        failure_reasons=failures,
        usage=cost_ledger.case_usage(case.case_id, round_index),
        elapsed_ms=elapsed_ms,
    )


async def _mock_results(
    cases: list[AgentEvalCase],
    repeat_count: int,
    judge: AgentQualityJudge,
    cost_ledger: AgentEvaluationCostLedger,
) -> list[AgentEvaluationCaseResult]:
    results = []
    for round_index in range(1, repeat_count + 1):
        for case in cases:
            failed = case.case_id == "agent-checklist-no-mention"
            cost_ledger.begin_case(case.case_id, round_index)
            try:
                result = await _result_from_evidence(
                    case=case,
                    round_index=round_index,
                    response=_mock_response(case),
                    tool_calls=_mock_tool_calls(case),
                    judge=judge,
                    cost_ledger=cost_ledger,
                    mock_failure=failed,
                    elapsed_ms=24,
                )
                results.append(result)
            finally:
                cost_ledger.end_case(case.case_id, round_index)
    return results


async def _real_result(
    *,
    case: AgentEvalCase,
    round_index: int,
    settings,
    judge: AgentQualityJudge,
    cost_ledger: AgentEvaluationCostLedger,
) -> AgentEvaluationCaseResult:
    started = time.monotonic()
    cost_ledger.begin_case(case.case_id, round_index)
    try:
        graph = IsolatedAgentGraph(case=case, settings=settings)
        reply = await graph.run(
            user_query=case.user_query,
            recent_messages=_history_lines(case),
            query_speaker=case.query_speaker,
        )
        tool_calls = graph.tool_evidence(reply.tool_trace)
        actual, checks, failures = assess_agent_case(
            expected=case.expected,
            response=reply.reply,
            tool_calls=tool_calls,
        )
        try:
            judged = await judge.judge(
                history=[item.model_dump() for item in case.history],
                user_query=case.user_query,
                expected=case.expected,
                actual=actual,
            )
        except Exception as exc:
            judged = AgentJudgeResult(
                status="error",
                error=f"{type(exc).__name__}: {exc}"[:2000],
            )
            failures.append(
                AgentFailureReason(
                    stage="judge",
                    code="judge_error",
                    message=judged.error or "判断官运行失败",
                )
            )
        deterministic_passed = all(check.passed for check in checks)
        status = (
            "error"
            if judged.status == "error"
            else ("passed" if deterministic_passed else "failed")
        )
        return AgentEvaluationCaseResult(
            case_id=case.case_id,
            title=case.title,
            origin=case.origin,
            scenario=case.scenario,
            tags=case.tags,
            round_index=round_index,
            status=status,
            history=case.history,
            user_query=case.user_query,
            expected=case.expected,
            actual=actual,
            deterministic_checks=checks,
            judge=judged,
            failure_reasons=failures,
            usage=cost_ledger.case_usage(case.case_id, round_index),
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )
    except Exception as exc:
        return AgentEvaluationCaseResult(
            case_id=case.case_id,
            title=case.title,
            origin=case.origin,
            scenario=case.scenario,
            tags=case.tags,
            round_index=round_index,
            status="error",
            history=case.history,
            user_query=case.user_query,
            expected=case.expected,
            actual={"response_mode": "unavailable", "response": None, "tool_calls": []},
            deterministic_checks=[],
            judge={
                "status": "skipped",
                "passed": None,
                "dimensions": [],
                "error": None,
            },
            failure_reasons=[
                AgentFailureReason(
                    stage="runtime",
                    code="agent_run_error",
                    message=f"{type(exc).__name__}: {exc}"[:2000],
                    severity="critical",
                )
            ],
            usage=cost_ledger.case_usage(case.case_id, round_index),
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )
    finally:
        cost_ledger.end_case(case.case_id, round_index)


def _base_summary(
    *,
    run_id: str,
    suite,
    mock: bool,
    settings,
    judge: AgentQualityJudge,
) -> AgentEvaluationSummary:
    now = utc_now()
    judge_model = (
        "mock-judge"
        if mock
        else (os.getenv("AGENT_EVAL_JUDGE_MODEL") or settings.ai_model)
    )
    return AgentEvaluationSummary(
        run_id=run_id,
        run_mode="mock" if mock else "real",
        status="running",
        started_at=now,
        updated_at=now,
        git_sha=(
            os.getenv("AGENT_EVAL_GIT_SHA")
            or os.getenv("RAG_EVAL_GIT_SHA")
            or "unknown"
        )[:64],
        dataset_id=suite.suite_id,
        dataset_version=suite.version,
        repeat_count=suite.repeat_count,
        model="mock-model" if mock else settings.ai_model,
        evaluation_framework="deepeval",
        evaluation_framework_version=judge.framework_version,
        judge_policy=AgentJudgePolicy(
            status="mock" if mock else "observational",
            enforced=False,
            model=judge_model,
            reason=(
                "模拟判断只验收数据链路，不能代表真实质量"
                if mock
                else "DeepEval 语义指标尚未用固定中文正反样本完成校准；结论展示但暂不作为发布硬门槛"
            ),
        ),
        counts=AgentEvaluationCounts(
            total=len(suite.cases) * suite.repeat_count,
            completed=0,
            passed=0,
            failed=0,
            errors=0,
        ),
        metrics=empty_metrics(),
        usage=AgentEvaluationUsageSummary(tracking_available=True),
        estimated_cost_yuan=0,
    )


async def async_main(args: argparse.Namespace) -> int:
    reports_root = args.reports_root.resolve()
    run_id = args.run_id or datetime.now(timezone.utc).strftime(
        "agent-eval-%Y%m%dT%H%M%SZ"
    )
    run_dir = reports_root / run_id
    if run_dir.exists():
        raise RuntimeError(f"run directory already exists: {run_dir}")
    suite = load_agent_eval_suite(args.dataset)
    settings = get_settings().model_copy(update={"web_search_enabled": False})
    if not args.mock:
        if not settings.ai_api_key:
            raise RuntimeError("真实 Agent 评测要求 AI_API_KEY")
        install_read_only_guards(engine)
    cost_ledger = AgentEvaluationCostLedger(settings)
    judge = AgentQualityJudge(
        settings,
        mock=args.mock,
        cost_ledger=cost_ledger,
    )
    restore_usage_collector = (
        (lambda: None)
        if args.mock
        else install_agent_usage_collector(cost_ledger)
    )
    base = _base_summary(
        run_id=run_id,
        suite=suite,
        mock=args.mock,
        settings=settings,
        judge=judge,
    )
    total = len(suite.cases) * suite.repeat_count
    atomic_write_json(run_dir / "status.json", base.model_dump(mode="json"))
    results: list[AgentEvaluationCaseResult] = []
    try:
        if args.mock:
            results = await _mock_results(
                suite.cases,
                suite.repeat_count,
                judge,
                cost_ledger,
            )
        else:
            for round_index in range(1, suite.repeat_count + 1):
                for case in suite.cases:
                    results.append(
                        await _real_result(
                            case=case,
                            round_index=round_index,
                            settings=settings,
                            judge=judge,
                            cost_ledger=cost_ledger,
                        )
                    )
                    running = aggregate_agent_summary(
                        base=base,
                        cases=results,
                        total=total,
                        status="running",
                    )
                    atomic_write_json(
                        run_dir / "status.json", running.model_dump(mode="json")
                    )
        summary = aggregate_agent_summary(base=base, cases=results, total=total)
        report = AgentEvaluationRunDetail(summary=summary, cases=results)
        write_agent_report(run_dir, report)
        atomic_write_json(run_dir / "status.json", summary.model_dump(mode="json"))
        return 0
    except Exception as exc:
        failed = aggregate_agent_summary(
            base=base,
            cases=results,
            total=total,
            status="failed",
            error=f"{type(exc).__name__}: {exc}"[:2000],
        )
        atomic_write_json(run_dir / "status.json", failed.model_dump(mode="json"))
        raise
    finally:
        restore_usage_collector()
        await judge.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the isolated reply Agent task evaluation"
    )
    parser.add_argument(
        "--mock", action="store_true", help="No database or model calls"
    )
    parser.add_argument("--run-id")
    parser.add_argument(
        "--reports-root",
        type=Path,
        default=Path(
            os.getenv(
                "AGENT_EVALUATION_REPORTS_PATH",
                "/app/diagnostics/agent-evaluations",
            )
        ),
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path(
            os.getenv(
                "AGENT_EVALUATION_DATASET_PATH",
                "/app/tests/fixtures/agent_eval_cases.v3.json",
            )
        ),
    )
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(async_main(parse_args())))
