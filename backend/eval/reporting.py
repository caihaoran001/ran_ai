from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from app.schemas.rag_evaluation import (
    RagEvaluationCaseResult,
    RagEvaluationCounts,
    RagEvaluationMetrics,
    RagEvaluationRunDetail,
    RagEvaluationSummary,
    RagMetricValue,
)

if __package__:
    from .rag_costs import summarize_case_usage
    from .rewrite_harm import assess_rewrite_harm
else:
    from rag_costs import summarize_case_usage
    from rewrite_harm import assess_rewrite_harm


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def metric(values: list[float], *, numerator: int | None = None) -> RagMetricValue:
    return RagMetricValue(
        value=(sum(values) / len(values)) if values else None,
        numerator=numerator,
        denominator=len(values),
    )


def aggregate_summary(
    *,
    base: RagEvaluationSummary,
    cases: list[RagEvaluationCaseResult],
    status: str = "completed",
    error: str | None = None,
) -> RagEvaluationSummary:
    decision_values = [
        1.0
        if case.expected.retrieval_decision != case.actual.retrieval_decision
        else 0.0
        for case in cases
        if case.actual.retrieval_decision is not None
    ]
    rewrite_assessments = [
        assess_rewrite_harm(
            original_query=case.user_query,
            rewritten_query=case.actual.knowledge_query,
            raw_hit_count=len(case.actual.raw_query_hits),
            rewritten_hit_count=len(case.actual.rewritten_query_hits),
            raw_recall=case.scores.raw_context_recall,
            rewritten_recall=case.scores.context_recall,
        )
        for case in cases
        if case.expected.retrieval_decision == "retrieve"
    ]
    rewrite_assessments = [
        item
        for item in rewrite_assessments
        if item.applicable and item.signal != "missing_recall_scores"
    ]
    metric_lists = {
        "context_recall": [
            case.scores.context_recall
            for case in cases
            if case.scores.context_recall is not None
        ],
        "context_precision": [
            case.scores.context_precision
            for case in cases
            if case.scores.context_precision is not None
        ],
        "faithfulness": [
            case.scores.faithfulness
            for case in cases
            if case.scores.faithfulness is not None
        ],
        "answer_relevancy": [
            case.scores.answer_relevancy
            for case in cases
            if case.scores.answer_relevancy is not None
        ],
    }
    counts = RagEvaluationCounts(
        total=len(cases),
        completed=len(cases),
        passed=sum(case.status == "passed" for case in cases),
        failed=sum(case.status == "failed" for case in cases),
        skipped=sum(case.status == "skipped" for case in cases),
        errors=sum(case.status == "error" for case in cases),
    )
    usage = summarize_case_usage(
        cases,
        tracking_available=base.usage.tracking_available,
    )
    now = utc_now()
    return base.model_copy(
        update={
            "status": status,
            "updated_at": now,
            "finished_at": now,
            "counts": counts,
            "metrics": RagEvaluationMetrics(
                retrieval_decision_error_rate=metric(
                    decision_values,
                    numerator=int(sum(decision_values)),
                ),
                rewrite_harm_rate=RagMetricValue(
                    value=(
                        sum(item.harmed for item in rewrite_assessments)
                        / len(rewrite_assessments)
                    )
                    if rewrite_assessments
                    else None,
                    numerator=sum(item.harmed for item in rewrite_assessments),
                    denominator=len(rewrite_assessments),
                ),
                context_recall=metric(metric_lists["context_recall"]),
                context_precision=metric(metric_lists["context_precision"]),
                faithfulness=metric(metric_lists["faithfulness"]),
                answer_relevancy=metric(metric_lists["answer_relevancy"]),
            ),
            "critical_fact_errors": sum(
                reason.severity == "critical"
                for case in cases
                for reason in case.failure_reasons
            ),
            "usage": usage,
            "estimated_cost_yuan": usage.total.estimated_cost_yuan,
            "error": error,
        }
    )


def write_report(run_dir: Path, report: RagEvaluationRunDetail) -> None:
    atomic_write_json(run_dir / "report.json", report.model_dump(mode="json"))
