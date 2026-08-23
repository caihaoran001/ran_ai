from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.evaluation.reply_agent_cases import load_reply_agent_eval_suite
from app.schemas.agent_evaluation import (
    AgentEvaluationCaseResult,
    AgentFrameworkMetric,
)
from app.schemas.reply_agent_evaluation import (
    ReplyAgentEvaluationCaseResult,
    ReplyAgentEvaluationRunDetail,
    ReplyAgentFailureReason,
)

if __package__:
    from .agent_costs import AgentEvaluationCostLedger
    from .agent_judge import (
        TASK_COMPLETION_THRESHOLD,
        AgentQualityJudge,
    )
    from .reporting import atomic_write_json
    from .rewrite_harm import (
        REWRITE_RECALL_DROP_THRESHOLD,
        assess_rewrite_harm,
    )
    from .run_reply_agent_eval import (
        _base_summary,
        _metric,
        aggregate_summary,
        write_report,
    )
else:
    from agent_costs import AgentEvaluationCostLedger
    from agent_judge import TASK_COMPLETION_THRESHOLD, AgentQualityJudge
    from reporting import atomic_write_json
    from rewrite_harm import REWRITE_RECALL_DROP_THRESHOLD, assess_rewrite_harm
    from run_reply_agent_eval import (
        _base_summary,
        _metric,
        aggregate_summary,
        write_report,
    )


def _load_report(path: Path) -> ReplyAgentEvaluationRunDetail:
    return ReplyAgentEvaluationRunDetail.model_validate(
        json.loads(path.read_text(encoding="utf-8"))
    )


def _replace_metric(
    case: ReplyAgentEvaluationCaseResult,
    *,
    key: str,
    replacement,
) -> None:
    case.metrics = [item for item in case.metrics if item.key != key]
    if replacement is not None:
        case.metrics.append(replacement)


def _mark_failed(
    case: ReplyAgentEvaluationCaseResult,
    *,
    stage: str,
    code: str,
    message: str,
) -> None:
    if case.status != "error":
        case.status = "failed"
    case.failure_reasons = [item for item in case.failure_reasons if item.code != code]
    case.failure_reasons.append(
        ReplyAgentFailureReason(
            stage=stage,
            code=code,
            message=message,
            severity="warning",
        )
    )


def _reanalyze_rewrite_harm(case: ReplyAgentEvaluationCaseResult) -> None:
    rag = case.evidence.get("rag")
    if not isinstance(rag, dict):
        raise ValueError(f"{case.case_id} is missing persisted RAG evidence")
    actual = rag.get("actual")
    scores = rag.get("scores")
    if not isinstance(actual, dict) or not isinstance(scores, dict):
        raise ValueError(f"{case.case_id} has invalid persisted RAG evidence")
    rewritten_query = actual.get("knowledge_query")
    raw_hits = actual.get("raw_query_hits") or []
    rewritten_hits = actual.get("rewritten_query_hits") or []
    assessment = assess_rewrite_harm(
        original_query=case.user_query,
        rewritten_query=rewritten_query if isinstance(rewritten_query, str) else None,
        raw_hit_count=len(raw_hits),
        rewritten_hit_count=len(rewritten_hits),
        raw_recall=scores.get("raw_context_recall"),
        rewritten_recall=scores.get("context_recall"),
    )
    replacement = None
    if assessment.applicable:
        replacement = _metric(
            key="rewrite_harm",
            label="查询改写损伤",
            group="MR",
            kind="deterministic",
            score=assessment.score,
            threshold=REWRITE_RECALL_DROP_THRESHOLD,
            passed=(
                None
                if assessment.signal == "missing_recall_scores"
                else not assessment.harmed
            ),
            reason=assessment.reason,
        )
    _replace_metric(case, key="rewrite_harm", replacement=replacement)
    if assessment.harmed:
        _mark_failed(
            case,
            stage="rewrite_harm",
            code="query_rewrite_harm",
            message=assessment.reason,
        )


async def _reanalyze_task_completion(
    case: ReplyAgentEvaluationCaseResult,
    *,
    expected,
    judge: AgentQualityJudge | None,
    ledger: AgentEvaluationCostLedger,
    mock: bool,
) -> None:
    agent = case.evidence.get("agent")
    if not isinstance(agent, dict):
        raise ValueError(f"{case.case_id} is missing persisted Agent evidence")
    source = AgentEvaluationCaseResult.model_validate(agent)
    source_key = source.case_id
    ledger.begin_case(source_key, source.round_index)
    try:
        if mock:
            metric = AgentFrameworkMetric(
                key="task_completion",
                label="任务完成",
                kind="llm",
                score=1.0,
                threshold=TASK_COMPLETION_THRESHOLD,
                passed=True,
                reason="Mock 重分析仅验证证据重放与报告链路",
            )
        else:
            assert judge is not None
            metric = await judge.score_task_completion(
                history=[item.model_dump(mode="json") for item in source.history],
                user_query=source.user_query,
                expected=expected,
                actual=source.actual,
                threshold=TASK_COMPLETION_THRESHOLD,
                strict_mode=False,
            )
        case.usage = ledger.case_usage(source_key, source.round_index).model_dump(
            mode="json"
        )
    finally:
        ledger.end_case(source_key, source.round_index)
    replacement = _metric(
        key="judge_task_completion",
        label="任务完成",
        group="MB" if "MB" in case.expected.metric_groups else "M0",
        kind="llm",
        score=metric.score,
        threshold=TASK_COMPLETION_THRESHOLD,
        passed=metric.passed,
        reason=metric.reason,
        enforced=True,
    )
    _replace_metric(case, key="judge_task_completion", replacement=replacement)
    if metric.passed is False:
        _mark_failed(
            case,
            stage="judge",
            code="task_completion",
            message=metric.reason or "用户可见业务结果未达到校准后阈值",
        )


async def async_main(args: argparse.Namespace) -> int:
    if args.max_cost_yuan <= 0:
        raise ValueError("max_cost_yuan must be greater than zero")
    source = _load_report(args.source_report.resolve())
    if source.summary.status != "completed":
        raise ValueError("source report must be completed")
    suite = load_reply_agent_eval_suite(args.dataset)
    suite_by_id = {item.case_id: item for item in suite.cases}
    source_ids = [item.case_id for item in source.cases]
    if source_ids != [item.case_id for item in suite.cases]:
        raise ValueError("source report must contain the complete ordered 80-case suite")

    run_id = args.run_id or datetime.now(timezone.utc).strftime(
        "reply-agent-reanalysis-%Y%m%dT%H%M%SZ"
    )
    run_dir = args.reports_root.resolve() / run_id
    if run_dir.exists():
        raise RuntimeError(f"run directory already exists: {run_dir}")
    base = _base_summary(run_id=run_id, suite=suite, mock=args.mock)
    base.score_source = "saved_output_reanalysis"
    base.source_run_id = source.summary.run_id
    atomic_write_json(run_dir / "status.json", base.model_dump(mode="json"))

    settings = get_settings()
    ledger = AgentEvaluationCostLedger(settings)
    judge = None if args.mock else AgentQualityJudge(
        settings,
        mock=False,
        cost_ledger=ledger,
    )
    results: list[ReplyAgentEvaluationCaseResult] = []
    try:
        for source_case in source.cases:
            case = source_case.model_copy(deep=True)
            original_usage: dict[str, Any] = case.usage
            case.evidence = dict(case.evidence)
            case.evidence["source_run_id"] = source.summary.run_id
            case.evidence["source_usage"] = original_usage
            case.usage = {}
            suite_case = suite_by_id[case.case_id]
            if case.execution_kind == "rag" and suite_case.retrieval_decision == "retrieve":
                _reanalyze_rewrite_harm(case)
            elif case.execution_kind == "agent":
                assert suite_case.agent_case is not None
                await _reanalyze_task_completion(
                    case,
                    expected=suite_case.agent_case.expected,
                    judge=judge,
                    ledger=ledger,
                    mock=args.mock,
                )
            results.append(case)
            running = aggregate_summary(base=base, cases=results, status="running")
            atomic_write_json(run_dir / "status.json", running.model_dump(mode="json"))
            if (
                not args.mock
                and running.usage.known_estimated_cost_yuan >= args.max_cost_yuan
            ):
                raise RuntimeError(
                    "旧报告重分析达到费用上限："
                    f"¥{running.usage.known_estimated_cost_yuan:.4f} / "
                    f"¥{args.max_cost_yuan:.2f}"
                )
        summary = aggregate_summary(base=base, cases=results)
        report = ReplyAgentEvaluationRunDetail(summary=summary, cases=results)
        write_report(run_dir, report)
        atomic_write_json(run_dir / "status.json", summary.model_dump(mode="json"))
        return 0
    except Exception as exc:
        failed = aggregate_summary(
            base=base,
            cases=results,
            status="failed",
            error=f"{type(exc).__name__}: {exc}"[:2000],
        )
        atomic_write_json(run_dir / "status.json", failed.model_dump(mode="json"))
        raise
    finally:
        if judge is not None:
            await judge.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Re-score saved 80-case outputs with calibrated rulers"
    )
    parser.add_argument("--source-report", type=Path, required=True)
    parser.add_argument("--run-id")
    parser.add_argument("--mock", action="store_true", help="No model calls")
    parser.add_argument(
        "--max-cost-yuan",
        type=float,
        default=float(os.getenv("REPLY_AGENT_EVAL_MAX_COST_YUAN", "10")),
    )
    parser.add_argument(
        "--reports-root",
        type=Path,
        default=Path(
            os.getenv(
                "REPLY_AGENT_EVALUATION_REPORTS_PATH",
                "/app/diagnostics/reply-agent-evaluations",
            )
        ),
    )
    parser.add_argument("--dataset", type=Path, default=None)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(async_main(parse_args())))
