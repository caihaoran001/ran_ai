from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import ValidationError

from app.schemas.agent_evaluation import (
    AgentEvaluationRunDetail,
    AgentEvaluationSummary,
)


MAX_REPORT_BYTES = 12 * 1024 * 1024
MAX_RUNS = 100


class AgentEvaluationReportError(RuntimeError):
    pass


class AgentEvaluationReportNotFound(AgentEvaluationReportError):
    pass


class AgentEvaluationReportInvalid(AgentEvaluationReportError):
    pass


def default_agent_evaluation_report_root() -> Path:
    configured = os.getenv("AGENT_EVALUATION_REPORTS_PATH", "").strip()
    if configured:
        return Path(configured)
    diagnostics_history = os.getenv("DIAGNOSTICS_HISTORY_PATH", "").strip()
    diagnostics_dir = (
        Path(diagnostics_history).parent if diagnostics_history else Path("diagnostics")
    )
    return diagnostics_dir / "agent-evaluations"


class AgentEvaluationReportStore:
    def __init__(self, root: Path | None = None):
        self.root = (root or default_agent_evaluation_report_root()).resolve()

    def list_runs(self) -> list[AgentEvaluationSummary]:
        if not self.root.is_dir():
            return []
        summaries: list[AgentEvaluationSummary] = []
        for run_dir in sorted(self.root.iterdir(), reverse=True):
            if len(summaries) >= MAX_RUNS:
                break
            if not run_dir.is_dir() or run_dir.is_symlink():
                continue
            try:
                summaries.append(self._read_summary(run_dir))
            except AgentEvaluationReportError:
                continue
        summaries.sort(key=lambda item: item.started_at, reverse=True)
        return summaries

    def get_run(self, run_id: str) -> AgentEvaluationRunDetail:
        run_dir = self._safe_run_dir(run_id)
        report_path = run_dir / "report.json"
        if not report_path.is_file():
            if not run_dir.is_dir():
                raise AgentEvaluationReportNotFound("Agent evaluation run not found")
            summary = self._read_summary(run_dir)
            return AgentEvaluationRunDetail(summary=summary, cases=[])
        return self._read_model(report_path, AgentEvaluationRunDetail)

    def _read_summary(self, run_dir: Path) -> AgentEvaluationSummary:
        report_path = run_dir / "report.json"
        if report_path.is_file():
            return self._read_model(report_path, AgentEvaluationRunDetail).summary
        status_path = run_dir / "status.json"
        if status_path.is_file():
            return self._read_model(status_path, AgentEvaluationSummary)
        raise AgentEvaluationReportInvalid(
            "run contains neither report.json nor status.json"
        )

    def _safe_run_dir(self, run_id: str) -> Path:
        allowed = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-"
        if not run_id or any(char not in allowed for char in run_id):
            raise AgentEvaluationReportNotFound("Agent evaluation run not found")
        candidate = (self.root / run_id).resolve()
        if candidate.parent != self.root or candidate.is_symlink():
            raise AgentEvaluationReportNotFound("Agent evaluation run not found")
        return candidate

    @staticmethod
    def _read_model(path: Path, model_type):
        if path.is_symlink() or not path.is_file():
            raise AgentEvaluationReportInvalid("report file is unavailable")
        size = path.stat().st_size
        if size <= 0 or size > MAX_REPORT_BYTES:
            raise AgentEvaluationReportInvalid("report file size is invalid")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return model_type.model_validate(payload)
        except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as exc:
            raise AgentEvaluationReportInvalid("report file is invalid") from exc
