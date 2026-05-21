from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openpyxl import load_workbook


DEFAULT_SOURCE = Path("../参考文件/HumanBurningAI_营养师客服班主任_QA库框架_v1.xlsx")
DEFAULT_SHEET = "02_分类字典"
DEFAULT_OUTPUT = Path("src/app/services/knowledge_base/keyword_whitelist.json")


def main() -> None:
    args = parse_args()
    rows = read_rows(args.source, args.sheet)
    keywords = sorted(
        {
            keyword
            for row in rows
            for keyword in split_keywords(row.get("识别关键词"))
        },
        key=lambda item: (-len(item), item),
    )
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_file": str(args.source),
        "sheet": args.sheet,
        "keyword_column": "识别关键词",
        "count": len(keywords),
        "keywords": keywords,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Imported {len(keywords)} keyword whitelist terms into {args.output}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import retrieval keyword whitelist from sheet 02 category dictionary.")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--sheet", default=DEFAULT_SHEET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def read_rows(source: Path, sheet_name: str) -> list[dict[str, str]]:
    if not source.exists():
        raise SystemExit(f"Source file not found: {source}")
    workbook = load_workbook(source, read_only=True, data_only=True)
    if sheet_name not in workbook.sheetnames:
        raise SystemExit(f"Sheet not found: {sheet_name}. Available: {', '.join(workbook.sheetnames)}")
    sheet = workbook[sheet_name]
    headers = [clean_text(value) for value in next(sheet.iter_rows(min_row=1, max_row=1, values_only=True))]
    if "识别关键词" not in headers:
        raise SystemExit("Required column missing: 识别关键词")
    rows: list[dict[str, str]] = []
    for values in sheet.iter_rows(min_row=2, values_only=True):
        row = {
            header: clean_text(values[index] if index < len(values) else "")
            for index, header in enumerate(headers)
            if header
        }
        if row.get("识别关键词"):
            rows.append(row)
    return rows


def split_keywords(value: str | None) -> list[str]:
    text = clean_text(value)
    if not text:
        return []
    return [
        item.strip()
        for item in re.split(r"[、,，;/；\s]+", text)
        if len(item.strip()) >= 2
    ]


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).replace("\r\n", "\n").replace("\r", "\n")).strip()


if __name__ == "__main__":
    main()
