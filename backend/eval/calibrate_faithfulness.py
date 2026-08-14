from __future__ import annotations

import argparse
import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import get_settings
from app.evaluation.faithfulness_calibration import (
    CALIBRATION_REPEAT_COUNT,
    SELECTION_CASE_COUNT,
    VALIDATION_CASE_COUNT,
    assess_faithfulness_calibration,
    assess_two_stage_faithfulness_calibration,
    build_faithfulness_calibration_pairs,
    default_faithfulness_calibration_path,
    load_faithfulness_calibration_suite,
)
from app.schemas.rag_evaluation import (
    FaithfulnessCalibrationItem,
    FaithfulnessCalibrationMetrics,
    FaithfulnessCalibrationReport,
    FaithfulnessCalibrationStageRun,
    RagMetricThresholdSelection,
    RagBinaryJudgement,
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
    items: list[FaithfulnessCalibrationItem],
    error: str | None = None,
    score_source: str = "fresh_llm_scores",
    source_run_id: str | None = None,
) -> FaithfulnessCalibrationReport:
    settings = get_settings()
    two_stage = None
    overall = None
    expected_item_count = len(suite.cases) * CALIBRATION_REPEAT_COUNT
    if len(items) == expected_item_count:
        selection_runs = _scores_by_stage(items, phase="selection")
        validation_runs = _scores_by_stage(items, phase="validation")
        two_stage = assess_two_stage_faithfulness_calibration(
            selection_runs=selection_runs,
            validation_runs=validation_runs,
        )
        effective_threshold = two_stage.selected_threshold
        if effective_threshold is not None:
            overall = assess_faithfulness_calibration(
                [item.positive_score for item in items],
                [item.negative_score for item in items],
                threshold=effective_threshold,
            )
    else:
        effective_threshold = None
    now = utc_now()
    return FaithfulnessCalibrationReport(
        schema_version=3,
        calibration_id=suite.calibration_id,
        dataset_version=suite.version,
        run_id=run_id,
        status=status,
        started_at=started_at,
        updated_at=now,
        finished_at=now if status != "running" else None,
        git_sha=(os.getenv("RAG_EVAL_GIT_SHA") or "unknown")[:64],
        model=settings.ai_model,
        ragas_version="0.4.3",
        threshold=effective_threshold,
        base_pair_count=len(suite.cases),
        completed_pair_count=_completed_case_count(items),
        passed=two_stage.passed if two_stage else None,
        metrics=(
            _metrics_model(overall)
            if overall
            else None
        ),
        failed_criteria=list(two_stage.failed_criteria) if two_stage else [],
        items=items,
        repeat_count=CALIBRATION_REPEAT_COUNT,
        selection_case_count=SELECTION_CASE_COUNT,
        validation_case_count=VALIDATION_CASE_COUNT,
        selected_threshold=(two_stage.selected_threshold if two_stage else None),
        threshold_selection=(
            _threshold_selection_model(two_stage.threshold_selection)
            if two_stage and two_stage.threshold_selection
            else None
        ),
        selection_results=(
            [_stage_run_model(run) for run in two_stage.selection_runs]
            if two_stage
            else []
        ),
        validation_results=(
            [_stage_run_model(run) for run in two_stage.validation_runs]
            if two_stage
            else []
        ),
        score_source=score_source,
        source_run_id=source_run_id,
        error=error,
    )


def _metrics_model(assessment) -> FaithfulnessCalibrationMetrics:
    return FaithfulnessCalibrationMetrics(
        positive_mean=assessment.positive_mean,
        negative_mean=assessment.negative_mean,
        mean_gap=assessment.mean_gap,
        auc=assessment.auc,
        paired_accuracy=assessment.paired_accuracy,
        positive_pass_rate=assessment.positive_pass_rate,
        negative_rejection_rate=assessment.negative_rejection_rate,
    )


def _stage_run_model(run) -> FaithfulnessCalibrationStageRun:
    return FaithfulnessCalibrationStageRun(
        repeat_index=run.repeat_index,
        passed=run.passed,
        metrics=_metrics_model(run.assessment),
    )


def _threshold_selection_model(selection) -> RagMetricThresholdSelection:
    return RagMetricThresholdSelection(
        rule=selection.rule,
        minimum_classification_rate=selection.minimum_classification_rate,
        lower_exclusive=selection.lower_exclusive,
        upper_inclusive=selection.upper_inclusive,
        selected_threshold=selection.selected_threshold,
        safety_margin=selection.safety_margin,
    )


def _scores_by_stage(
    items: list[FaithfulnessCalibrationItem],
    *,
    phase: str,
) -> list[tuple[list[float], list[float]]]:
    return [
        (
            [
                item.positive_score
                for item in items
                if item.phase == phase and item.repeat_index == repeat_index
            ],
            [
                item.negative_score
                for item in items
                if item.phase == phase and item.repeat_index == repeat_index
            ],
        )
        for repeat_index in range(1, CALIBRATION_REPEAT_COUNT + 1)
    ]


def _completed_case_count(items: list[FaithfulnessCalibrationItem]) -> int:
    repeats_by_case: dict[str, set[int]] = {}
    for item in items:
        repeats_by_case.setdefault(item.case_id, set()).add(item.repeat_index)
    return sum(
        len(repeats) == CALIBRATION_REPEAT_COUNT
        for repeats in repeats_by_case.values()
    )


async def async_main(args: argparse.Namespace) -> int:
    suite = load_faithfulness_calibration_suite(args.dataset)
    pairs = build_faithfulness_calibration_pairs(suite)
    run_id = args.run_id or datetime.now(timezone.utc).strftime(
        "faithfulness-calibration-%Y%m%dT%H%M%SZ"
    )
    run_dir = args.reports_root.resolve() / run_id
    if run_dir.exists():
        raise RuntimeError(f"run directory already exists: {run_dir}")

    settings = get_settings()
    if not settings.ai_api_key:
        raise RuntimeError("真实校准要求 AI_API_KEY")
    scorer = RagasScorer(settings, include_answer_relevancy=False)
    started_at = utc_now()
    items: list[FaithfulnessCalibrationItem] = []
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
        for repeat_index in range(1, CALIBRATION_REPEAT_COUNT + 1):
            for pair in pairs:
                (
                    positive_score,
                    positive_details,
                ) = await scorer.score_faithfulness_with_details(
                    user_input=pair.user_input,
                    response=pair.positive_response,
                    retrieved_contexts=pair.retrieved_contexts,
                )
                (
                    negative_score,
                    negative_details,
                ) = await scorer.score_faithfulness_with_details(
                    user_input=pair.user_input,
                    response=pair.negative_response,
                    retrieved_contexts=pair.retrieved_contexts,
                )
                items.append(
                    FaithfulnessCalibrationItem(
                        case_id=pair.case_id,
                        source_case_id=pair.source_case_id,
                        user_input=pair.user_input,
                        retrieved_contexts=pair.retrieved_contexts,
                        positive_response=pair.positive_response,
                        negative_response=pair.negative_response,
                        positive_score=positive_score,
                        negative_score=negative_score,
                        positive_details=[
                            RagBinaryJudgement(
                                statement=item.statement,
                                reason=item.reason,
                                verdict=item.verdict,
                            )
                            for item in positive_details
                        ],
                        negative_details=[
                            RagBinaryJudgement(
                                statement=item.statement,
                                reason=item.reason,
                                verdict=item.verdict,
                            )
                            for item in negative_details
                        ],
                        phase=pair.phase,
                        error_type=pair.error_type,
                        repeat_index=repeat_index,
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
    parser = argparse.ArgumentParser(description="Calibrate Chinese RAGAS Faithfulness")
    parser.add_argument("--run-id")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path(
            os.getenv(
                "RAG_FAITHFULNESS_CALIBRATION_DATASET_PATH",
                str(default_faithfulness_calibration_path()),
            )
        ),
    )
    parser.add_argument(
        "--reports-root",
        type=Path,
        default=Path(
            os.getenv(
                "RAG_FAITHFULNESS_CALIBRATION_REPORTS_PATH",
                "/app/diagnostics/rag-evaluations/calibrations",
            )
        ),
    )
    parser.add_argument(
        "--latest-report",
        type=Path,
        default=Path(
            os.getenv(
                "RAG_FAITHFULNESS_CALIBRATION_LATEST_PATH",
                "/app/diagnostics/rag-evaluations/faithfulness-calibration.json",
            )
        ),
    )
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(async_main(parse_args())))
