from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import ValidationError

from app.schemas.archivist_evaluation import (
    ArchivistEvaluationRunDetail,
    ArchivistEvaluationSummary,
)


MAX_REPORT_BYTES = 24 * 1024 * 1024
MAX_RUNS = 100


class ArchivistEvaluationReportError(RuntimeError):
    pass


class ArchivistEvaluationReportNotFound(ArchivistEvaluationReportError):
    pass


class ArchivistEvaluationReportInvalid(ArchivistEvaluationReportError):
    pass


def default_archivist_evaluation_report_root() -> Path:
    configured = os.getenv("ARCHIVIST_EVALUATION_REPORTS_PATH", "").strip()
    if configured:
        return Path(configured)
    diagnostics_history = os.getenv("DIAGNOSTICS_HISTORY_PATH", "").strip()
    diagnostics_dir = (
        Path(diagnostics_history).parent if diagnostics_history else Path("diagnostics")
    )
    return diagnostics_dir / "archivist-evaluations"


class ArchivistEvaluationReportStore:
    def __init__(self, root: Path | None = None):
        self.root = (root or default_archivist_evaluation_report_root()).resolve()

    def list_runs(self) -> list[ArchivistEvaluationSummary]:
        if not self.root.is_dir():
            return []
        summaries: list[ArchivistEvaluationSummary] = []
        for run_dir in sorted(self.root.iterdir(), reverse=True):
            if len(summaries) >= MAX_RUNS:
                break
            if not run_dir.is_dir() or run_dir.is_symlink():
                continue
            try:
                summaries.append(self._read_summary(run_dir))
            except ArchivistEvaluationReportError:
                continue
        summaries.sort(key=lambda item: item.started_at, reverse=True)
        return summaries

    def get_run(self, run_id: str) -> ArchivistEvaluationRunDetail:
        run_dir = self._safe_run_dir(run_id)
        report_path = run_dir / "report.json"
        if not report_path.is_file():
            if not run_dir.is_dir():
                raise ArchivistEvaluationReportNotFound(
                    "Archivist evaluation run not found"
                )
            raise ArchivistEvaluationReportInvalid("run contains no report.json")
        return self._read_model(report_path, ArchivistEvaluationRunDetail)

    def _read_summary(self, run_dir: Path) -> ArchivistEvaluationSummary:
        report_path = run_dir / "report.json"
        if not report_path.is_file():
            raise ArchivistEvaluationReportInvalid("run contains no report.json")
        return self._read_model(report_path, ArchivistEvaluationRunDetail).summary

    def _safe_run_dir(self, run_id: str) -> Path:
        allowed = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-"
        if not run_id or any(char not in allowed for char in run_id):
            raise ArchivistEvaluationReportNotFound(
                "Archivist evaluation run not found"
            )
        candidate = (self.root / run_id).resolve()
        if candidate.parent != self.root or candidate.is_symlink():
            raise ArchivistEvaluationReportNotFound(
                "Archivist evaluation run not found"
            )
        return candidate

    @staticmethod
    def _read_model(path: Path, model_type):
        if path.is_symlink() or not path.is_file():
            raise ArchivistEvaluationReportInvalid("report file is unavailable")
        size = path.stat().st_size
        if size <= 0 or size > MAX_REPORT_BYTES:
            raise ArchivistEvaluationReportInvalid("report file size is invalid")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return model_type.model_validate(payload)
        except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as exc:
            raise ArchivistEvaluationReportInvalid("report file is invalid") from exc
