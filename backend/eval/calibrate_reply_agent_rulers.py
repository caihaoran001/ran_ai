from __future__ import annotations

import argparse
import asyncio
import os
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.evaluation.rag_metric_calibration import (
    assess_rag_metric_calibration,
    select_stable_calibration_threshold,
)
from app.evaluation.reply_agent_ruler_calibration import (
    MIN_CALIBRATION_CLASSIFICATION_RATE,
    TASK_COMPLETION_REPEAT_COUNT,
    ReplyAgentRulerCalibrationSuite,
    TaskCompletionCalibrationCase,
    default_reply_agent_ruler_calibration_path,
    load_reply_agent_ruler_calibration_suite,
)
from app.schemas.agent_evaluation import AgentCaseActual, AgentEvaluationModelCall

if __package__:
    from .agent_costs import AgentEvaluationCostLedger, usage_from_calls
    from .agent_judge import (
        TASK_COMPLETION_POLICY_VERSION,
        TASK_COMPLETION_PROBE_THRESHOLD,
        AgentQualityJudge,
    )
    from .reporting import atomic_write_json
    from .rewrite_harm import (
        REWRITE_HARM_POLICY_VERSION,
        REWRITE_RECALL_DROP_THRESHOLD,
        assess_rewrite_harm,
    )
else:
    from agent_costs import AgentEvaluationCostLedger, usage_from_calls
    from agent_judge import (
        TASK_COMPLETION_POLICY_VERSION,
        TASK_COMPLETION_PROBE_THRESHOLD,
        AgentQualityJudge,
    )
    from reporting import atomic_write_json
    from rewrite_harm import (
        REWRITE_HARM_POLICY_VERSION,
        REWRITE_RECALL_DROP_THRESHOLD,
        assess_rewrite_harm,
    )


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rewrite_harm_calibration(suite: ReplyAgentRulerCalibrationSuite) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for case in suite.rewrite_harm_cases:
        actual = assess_rewrite_harm(
            original_query=case.original_query,
            rewritten_query=case.rewritten_query,
            raw_hit_count=case.raw_hit_count,
            rewritten_hit_count=case.rewritten_hit_count,
            raw_recall=case.raw_recall,
            rewritten_recall=case.rewritten_recall,
        )
        passed = (
            actual.applicable == case.expected_applicable
            and actual.harmed == case.expected_harmed
            and actual.signal == case.expected_signal
        )
        items.append(
            {
                "case_id": case.case_id,
                "passed": passed,
                "expected": {
                    "applicable": case.expected_applicable,
                    "harmed": case.expected_harmed,
                    "signal": case.expected_signal,
                },
                "actual": asdict(actual),
            }
        )
    return {
        "policy_version": REWRITE_HARM_POLICY_VERSION,
        "recall_drop_threshold": REWRITE_RECALL_DROP_THRESHOLD,
        "passed": all(item["passed"] for item in items),
        "completed": len(items),
        "items": items,
    }


def _actual(case: TaskCompletionCalibrationCase, response: str) -> AgentCaseActual:
    no_reply = response == "<no_reply>"
    return AgentCaseActual(
        response_mode="no_reply" if no_reply else "reply",
        response=None if no_reply else response,
        tool_calls=case.tool_calls,
    )


async def _score_response(
    *,
    case: TaskCompletionCalibrationCase,
    response: str,
    polarity: str,
    repeat_index: int,
    mock: bool,
    judge: AgentQualityJudge | None,
    ledger: AgentEvaluationCostLedger,
) -> tuple[dict[str, Any], list[AgentEvaluationModelCall]]:
    ledger_key = f"{case.case_id}-{polarity}"
    ledger.begin_case(ledger_key, repeat_index)
    try:
        if mock:
            score = 1.0 if polarity == "positive" else 0.0
            reason = "Mock 正例" if polarity == "positive" else "Mock 负例"
        else:
            assert judge is not None
            metric = await judge.score_task_completion(
                history=[item.model_dump(mode="json") for item in case.history],
                user_query=case.user_query,
                expected=case.expected,
                actual=_actual(case, response),
                threshold=TASK_COMPLETION_PROBE_THRESHOLD,
                strict_mode=False,
            )
            if metric.score is None:
                raise RuntimeError(f"{case.case_id} did not return a task score")
            score = metric.score
            reason = metric.reason or "DeepEval 未返回理由"
        usage = ledger.case_usage(ledger_key, repeat_index)
    finally:
        ledger.end_case(ledger_key, repeat_index)
    return (
        {
            "case_id": case.case_id,
            "phase": case.phase,
            "error_type": case.error_type,
            "repeat_index": repeat_index,
            "polarity": polarity,
            "user_query": case.user_query,
            "response": response,
            "score": score,
            "reason": reason,
            "usage": usage.model_dump(mode="json"),
        },
        list(usage.calls),
    )


def _scores(
    items: list[dict[str, Any]], *, phase: str, repeat_index: int, polarity: str
) -> list[float]:
    return [
        float(item["score"])
        for item in items
        if item["phase"] == phase
        and item["repeat_index"] == repeat_index
        and item["polarity"] == polarity
    ]


def _assessment_payload(
    positive: list[float], negative: list[float], *, threshold: float
) -> dict[str, Any]:
    assessment = assess_rag_metric_calibration(
        positive,
        negative,
        threshold=threshold,
    )
    payload = asdict(assessment)
    payload["passed_at_90_percent"] = (
        assessment.positive_pass_rate >= MIN_CALIBRATION_CLASSIFICATION_RATE
        and assessment.negative_rejection_rate >= MIN_CALIBRATION_CLASSIFICATION_RATE
    )
    return payload


async def _task_completion_calibration(
    suite: ReplyAgentRulerCalibrationSuite,
    *,
    mock: bool,
    max_cost_yuan: float,
) -> dict[str, Any]:
    settings = get_settings()
    ledger = AgentEvaluationCostLedger(settings)
    judge = None if mock else AgentQualityJudge(settings, mock=False, cost_ledger=ledger)
    items: list[dict[str, Any]] = []
    calls: list[AgentEvaluationModelCall] = []
    try:
        for phase in ("selection", "validation"):
            phase_cases = [
                case for case in suite.task_completion_cases if case.phase == phase
            ]
            for repeat_index in range(1, TASK_COMPLETION_REPEAT_COUNT + 1):
                for case in phase_cases:
                    for polarity, response in (
                        ("positive", case.positive_response),
                        ("negative", case.negative_response),
                    ):
                        item, item_calls = await _score_response(
                            case=case,
                            response=response,
                            polarity=polarity,
                            repeat_index=repeat_index,
                            mock=mock,
                            judge=judge,
                            ledger=ledger,
                        )
                        items.append(item)
                        calls.extend(item_calls)
                        known_cost = usage_from_calls(calls).total.known_estimated_cost_yuan
                        if not mock and known_cost >= max_cost_yuan:
                            raise RuntimeError(
                                "任务完成度校准达到费用上限："
                                f"¥{known_cost:.4f} / ¥{max_cost_yuan:.2f}"
                            )
    finally:
        if judge is not None:
            await judge.close()

    selection_runs = [
        (
            _scores(items, phase="selection", repeat_index=repeat, polarity="positive"),
            _scores(items, phase="selection", repeat_index=repeat, polarity="negative"),
        )
        for repeat in range(1, TASK_COMPLETION_REPEAT_COUNT + 1)
    ]
    threshold = select_stable_calibration_threshold(
        selection_runs,
        minimum_classification_rate=MIN_CALIBRATION_CLASSIFICATION_RATE,
    )
    selected = threshold.selected_threshold if threshold else None
    selection_assessments: list[dict[str, Any]] = []
    validation_assessments: list[dict[str, Any]] = []
    if selected is not None:
        for repeat, (positive, negative) in enumerate(selection_runs, start=1):
            selection_assessments.append(
                {
                    "repeat_index": repeat,
                    **_assessment_payload(positive, negative, threshold=selected),
                }
            )
        for repeat in range(1, TASK_COMPLETION_REPEAT_COUNT + 1):
            positive = _scores(
                items, phase="validation", repeat_index=repeat, polarity="positive"
            )
            negative = _scores(
                items, phase="validation", repeat_index=repeat, polarity="negative"
            )
            validation_assessments.append(
                {
                    "repeat_index": repeat,
                    **_assessment_payload(positive, negative, threshold=selected),
                }
            )
        for item in items:
            item["passed_at_selected_threshold"] = (
                item["score"] >= selected
                if item["polarity"] == "positive"
                else item["score"] < selected
            )

    passed = bool(
        selected is not None
        and all(item["passed_at_90_percent"] for item in selection_assessments)
        and all(item["passed_at_90_percent"] for item in validation_assessments)
    )
    return {
        "policy_version": TASK_COMPLETION_POLICY_VERSION,
        "probe_threshold": TASK_COMPLETION_PROBE_THRESHOLD,
        "repeat_count": TASK_COMPLETION_REPEAT_COUNT,
        "threshold_selection": asdict(threshold) if threshold else None,
        "selected_threshold": selected,
        "selection_assessments": selection_assessments,
        "validation_assessments": validation_assessments,
        "passed": passed,
        "completed_items": len(items),
        "items": items,
        "usage": usage_from_calls(calls).model_dump(mode="json"),
    }


async def async_main(args: argparse.Namespace) -> int:
    if args.max_cost_yuan <= 0:
        raise ValueError("max_cost_yuan must be greater than zero")
    suite = load_reply_agent_ruler_calibration_suite(args.dataset)
    run_id = args.run_id or datetime.now(timezone.utc).strftime(
        "reply-agent-ruler-calibration-%Y%m%dT%H%M%SZ"
    )
    run_dir = args.reports_root.resolve() / run_id
    if run_dir.exists():
        raise RuntimeError(f"run directory already exists: {run_dir}")
    base = {
        "schema_version": 1,
        "calibration_id": suite.calibration_id,
        "calibration_version": suite.version,
        "run_id": run_id,
        "run_mode": "mock" if args.mock else "real",
        "status": "running",
        "started_at": utc_now_iso(),
        "updated_at": utc_now_iso(),
        "git_sha": (os.getenv("REPLY_AGENT_EVAL_GIT_SHA") or "unknown")[:64],
        "model": "mock-model" if args.mock else get_settings().ai_model,
        "evaluation_framework": "deepeval",
        "evaluation_framework_version": "4.1.5",
        "rewrite_harm": None,
        "task_completion": None,
        "error": None,
    }
    atomic_write_json(run_dir / "status.json", base)
    try:
        base["rewrite_harm"] = _rewrite_harm_calibration(suite)
        base["task_completion"] = await _task_completion_calibration(
            suite,
            mock=args.mock,
            max_cost_yuan=args.max_cost_yuan,
        )
        base["status"] = "completed"
        base["updated_at"] = utc_now_iso()
        base["finished_at"] = base["updated_at"]
        base["passed"] = bool(
            base["rewrite_harm"]["passed"]
            and base["task_completion"]["passed"]
        )
        atomic_write_json(run_dir / "report.json", base)
        atomic_write_json(run_dir / "status.json", base)
        return 0 if base["passed"] else 2
    except Exception as exc:
        base["status"] = "failed"
        base["updated_at"] = utc_now_iso()
        base["finished_at"] = base["updated_at"]
        base["error"] = f"{type(exc).__name__}: {exc}"[:2000]
        atomic_write_json(run_dir / "status.json", base)
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Calibrate reply Agent rulers")
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--run-id")
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
                "REPLY_AGENT_RULER_CALIBRATION_REPORTS_PATH",
                "/app/diagnostics/reply-agent-evaluations/calibrations",
            )
        ),
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=default_reply_agent_ruler_calibration_path(),
    )
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(async_main(parse_args())))
