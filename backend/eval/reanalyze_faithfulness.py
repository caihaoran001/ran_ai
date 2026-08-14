from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import get_settings
from app.evaluation.faithfulness_calibration import (
    CALIBRATION_REPEAT_COUNT,
    build_faithfulness_calibration_pairs,
    default_faithfulness_calibration_path,
    load_faithfulness_calibration_suite,
)
from app.schemas.rag_evaluation import FaithfulnessCalibrationReport

if __package__:
    from .calibrate_faithfulness import _report
    from .reporting import atomic_write_json, utc_now
else:
    from calibrate_faithfulness import _report
    from reporting import atomic_write_json, utc_now


def _verify_source_report(
    report: FaithfulnessCalibrationReport,
    *,
    suite,
) -> None:
    if report.status != "completed":
        raise ValueError("只能重新分析已完成的忠实度校准报告")
    if (
        report.calibration_id != suite.calibration_id
        or report.dataset_version != suite.version
    ):
        raise ValueError("原报告的校准题版本与当前固定题集不一致")
    expected_items = len(suite.cases) * CALIBRATION_REPEAT_COUNT
    if len(report.items) != expected_items:
        raise ValueError("原报告缺少两轮完整判分，不能重新选线")
    expected_pairs = {
        (pair.case_id, pair.source_case_id, pair.phase, repeat_index)
        for pair in build_faithfulness_calibration_pairs(suite)
        for repeat_index in range(1, CALIBRATION_REPEAT_COUNT + 1)
    }
    actual_pairs = {
        (item.case_id, item.source_case_id, item.phase, item.repeat_index)
        for item in report.items
    }
    if actual_pairs != expected_pairs:
        raise ValueError("原报告的题目或轮次与当前固定题集不一致")


def main(args: argparse.Namespace) -> int:
    source_path = args.source_report.resolve()
    source = FaithfulnessCalibrationReport.model_validate_json(
        source_path.read_text(encoding="utf-8")
    )
    suite = load_faithfulness_calibration_suite(args.dataset)
    _verify_source_report(source, suite=suite)
    settings = get_settings()
    if source.model != settings.ai_model:
        raise ValueError("当前判官模型与原报告不一致，不能复用旧分数")
    if source.ragas_version != "0.4.3":
        raise ValueError("当前 RAGAS 版本与原报告不一致，不能复用旧分数")

    run_id = args.run_id or datetime.now(timezone.utc).strftime(
        "faithfulness-calibration-reanalysis-%Y%m%dT%H%M%SZ"
    )
    run_dir = args.reports_root.resolve() / run_id
    if run_dir.exists():
        raise RuntimeError(f"run directory already exists: {run_dir}")
    started_at = utc_now()
    report = _report(
        suite=suite,
        run_id=run_id,
        started_at=started_at,
        status="completed",
        items=source.items,
        score_source="existing_scores_reanalysis",
        source_run_id=source.run_id,
    )
    payload = report.model_dump(mode="json")
    atomic_write_json(run_dir / "report.json", payload)
    atomic_write_json(run_dir / "status.json", payload)
    atomic_write_json(args.latest_report.resolve(), payload)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reanalyse completed faithfulness scores without calling an LLM"
    )
    parser.add_argument("--run-id")
    parser.add_argument("--source-report", type=Path, required=True)
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
    raise SystemExit(main(parse_args()))
