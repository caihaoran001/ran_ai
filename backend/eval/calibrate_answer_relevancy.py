from __future__ import annotations

import argparse
import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import get_settings
from app.evaluation.answer_relevancy_calibration import (
    CALIBRATION_THRESHOLD,
    assess_answer_relevancy_calibration,
    build_answer_relevancy_calibration_pairs,
    default_answer_relevancy_calibration_path,
    load_answer_relevancy_calibration_suite,
)
from app.schemas.rag_evaluation import (
    AnswerRelevancyCalibrationItem,
    AnswerRelevancyCalibrationMetrics,
    AnswerRelevancyCalibrationReport,
)

if __package__:
    from .ragas_eval import RagasScorer
    from .reporting import atomic_write_json, utc_now
else:
    from ragas_eval import RagasScorer
    from reporting import atomic_write_json, utc_now


def _report(
    *,
    suite,
    run_id: str,
    started_at,
    status: str,
    items: list[AnswerRelevancyCalibrationItem],
    error: str | None = None,
) -> AnswerRelevancyCalibrationReport:
    settings = get_settings()
    assessment = None
    if len(items) == len(suite.cases):
        assessment = assess_answer_relevancy_calibration(
            [item.positive_score for item in items],
            [item.negative_score for item in items],
        )
    now = utc_now()
    return AnswerRelevancyCalibrationReport(
        calibration_id=suite.calibration_id,
        dataset_version=suite.version,
        run_id=run_id,
        status=status,
        started_at=started_at,
        updated_at=now,
        finished_at=now if status != "running" else None,
        git_sha=(os.getenv("RAG_EVAL_GIT_SHA") or "unknown")[:64],
        model=settings.ai_model,
        embedding_model=settings.embedding_model,
        ragas_version="0.4.3",
        threshold=CALIBRATION_THRESHOLD,
        base_pair_count=len(suite.cases),
        completed_pair_count=len(items),
        passed=assessment.passed if assessment else None,
        metrics=(
            AnswerRelevancyCalibrationMetrics(
                positive_mean=assessment.positive_mean,
                negative_mean=assessment.negative_mean,
                mean_gap=assessment.mean_gap,
                auc=assessment.auc,
                paired_accuracy=assessment.paired_accuracy,
                positive_pass_rate=assessment.positive_pass_rate,
                negative_rejection_rate=assessment.negative_rejection_rate,
            )
            if assessment
            else None
        ),
        failed_criteria=list(assessment.failed_criteria) if assessment else [],
        items=items,
        error=error,
    )


async def async_main(args: argparse.Namespace) -> int:
    suite = load_answer_relevancy_calibration_suite(args.dataset)
    pairs = build_answer_relevancy_calibration_pairs(suite)
    run_id = args.run_id or datetime.now(timezone.utc).strftime(
        "relevancy-calibration-%Y%m%dT%H%M%SZ"
    )
    run_dir = args.reports_root.resolve() / run_id
    if run_dir.exists():
        raise RuntimeError(f"run directory already exists: {run_dir}")

    settings = get_settings()
    if not settings.ai_api_key or not settings.embedding_api_key:
        raise RuntimeError("真实校准要求 AI_API_KEY 和 EMBEDDING_API_KEY")
    scorer = RagasScorer(settings)
    started_at = utc_now()
    items: list[AnswerRelevancyCalibrationItem] = []
    atomic_write_json(
        run_dir / "status.json",
        _report(
            suite=suite,
            run_id=run_id,
            started_at=started_at,
            status="running",
            items=items,
        ).model_dump(mode="json"),
    )
    try:
        for pair in pairs:
            positive_score = await scorer.score_answer_relevancy(
                user_input=pair.user_input,
                response=pair.positive_response,
            )
            negative_score = await scorer.score_answer_relevancy(
                user_input=pair.user_input,
                response=pair.negative_response,
            )
            items.append(
                AnswerRelevancyCalibrationItem(
                    case_id=pair.case_id,
                    negative_source_case_id=pair.negative_source_case_id,
                    user_input=pair.user_input,
                    positive_response=pair.positive_response,
                    negative_response=pair.negative_response,
                    positive_score=positive_score,
                    negative_score=negative_score,
                )
            )
            atomic_write_json(
                run_dir / "status.json",
                _report(
                    suite=suite,
                    run_id=run_id,
                    started_at=started_at,
                    status="running",
                    items=items,
                ).model_dump(mode="json"),
            )

        report = _report(
            suite=suite,
            run_id=run_id,
            started_at=started_at,
            status="completed",
            items=items,
        )
        payload = report.model_dump(mode="json")
        atomic_write_json(run_dir / "report.json", payload)
        atomic_write_json(run_dir / "status.json", payload)
        atomic_write_json(args.latest_report.resolve(), payload)
        return 0
    except Exception as exc:
        failed = _report(
            suite=suite,
            run_id=run_id,
            started_at=started_at,
            status="failed",
            items=items,
            error=f"{type(exc).__name__}: {exc}"[:2000],
        )
        atomic_write_json(run_dir / "status.json", failed.model_dump(mode="json"))
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Calibrate Chinese RAGAS Answer Relevancy"
    )
    parser.add_argument("--run-id")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path(
            os.getenv(
                "RAG_ANSWER_RELEVANCY_CALIBRATION_DATASET_PATH",
                str(default_answer_relevancy_calibration_path()),
            )
        ),
    )
    parser.add_argument(
        "--reports-root",
        type=Path,
        default=Path(
            os.getenv(
                "RAG_ANSWER_RELEVANCY_CALIBRATION_REPORTS_PATH",
                "/app/diagnostics/rag-evaluations/calibrations",
            )
        ),
    )
    parser.add_argument(
        "--latest-report",
        type=Path,
        default=Path(
            os.getenv(
                "RAG_ANSWER_RELEVANCY_CALIBRATION_LATEST_PATH",
                "/app/diagnostics/rag-evaluations/answer-relevancy-calibration.json",
            )
        ),
    )
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(async_main(parse_args())))
