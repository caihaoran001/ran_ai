"""把已有评测证据（JSONL+summary）合并导入为页面可读的 report.json。

用于补导历史批次：读取一个或多个 run_archivist_dedup_eval 产出的证据文件，
用当前判分器重新判定后写入 diagnostics/archivist-evaluations/<run_id>/report.json。

用法（backend/ 下）：
  uv run python scripts/import_archivist_eval_reports.py \
    --run-id 2026-09-16-172856 --label "修复前（39 组次 35 过）" \
    --jsonl ../docs/reviews/artifacts/archivist-dedup-eval-20260916-172856.jsonl \
    --summary ../docs/reviews/artifacts/archivist-dedup-eval-summary-20260916-172856.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

SCRIPTS_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPTS_DIR.parent
sys.path.insert(0, str(BACKEND_DIR / "src"))
sys.path.insert(0, str(SCRIPTS_DIR))

import run_archivist_dedup_eval as harness  # noqa: E402

SH = ZoneInfo("Asia/Shanghai")


def _started_at_from_run_id(run_id: str) -> str:
    try:
        local = datetime.strptime(run_id[:17], "%Y-%m-%d-%H%M%S").replace(tzinfo=SH)
        return local.isoformat()
    except ValueError:
        return datetime.now(SH).isoformat()


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--label", default=None)
    parser.add_argument("--started-at", default=None)
    parser.add_argument("--jsonl", nargs="+", required=True)
    parser.add_argument("--summary", nargs="+", default=[])
    parser.add_argument("--report-dir", default=None)
    return parser.parse_args(argv)


def main(argv=None) -> None:
    args = parse_args(argv)
    rows: list[dict] = []
    for raw_path in args.jsonl:
        for line in Path(raw_path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row.get("group_code"):
                rows.append(row)
    if not rows:
        raise SystemExit("no group runs found in the given jsonl files")

    summaries = [
        json.loads(Path(raw_path).read_text(encoding="utf-8"))
        for raw_path in args.summary
    ]
    total_tokens = sum(int(item.get("total_tokens") or 0) for item in summaries)
    total_cost = sum(float(item.get("total_cost_yuan") or 0.0) for item in summaries)
    ai_settings = (
        (summaries[0].get("ai_settings") if summaries else None)
        or rows[0].get("ai_settings")
        or {}
    )
    model = str(ai_settings.get("ai_model") or "unknown")
    if summaries and summaries[0].get("mode"):
        mode = str(summaries[0]["mode"])
    else:
        mode = "fake" if model.startswith("fake") else "real"
    repeats = max(int(row.get("repeat_index") or 0) for row in rows) + 1

    grading = [harness.grade_group(row) for row in rows]
    report = harness.build_display_report(
        run_id=args.run_id,
        label=args.label,
        started_at=args.started_at or _started_at_from_run_id(args.run_id),
        mode=mode,
        model=model,
        repeats=repeats,
        group_results=rows,
        grading=grading,
        total_tokens=total_tokens,
        total_cost_yuan=total_cost,
    )
    report_dir = (
        Path(args.report_dir) if args.report_dir else harness.default_report_dir()
    )
    path = harness.write_display_report(report, report_dir)
    summary = report["summary"]
    print(
        f"imported {args.run_id}: passed={summary['passed']} failed={summary['failed']} "
        f"flagged={summary['flagged']} batches={summary['batches']} "
        f"tokens={summary['total_tokens']} cost={summary['total_cost_yuan']} -> {path}"
    )


if __name__ == "__main__":
    main()
