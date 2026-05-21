from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openpyxl import load_workbook


DEFAULT_SOURCE = Path("../参考文件/HumanBurningAI_营养师客服班主任_QA库框架_v1.xlsx")
DEFAULT_SHEET = "04_现有QA归类"
DEFAULT_OUTPUT_MD = Path("src/app/services/knowledge_base/ranren_ai_qa_from_excel.md")
DEFAULT_OUTPUT_META = Path("src/app/services/knowledge_base/ranren_ai_qa_from_excel.metadata.json")

SEARCHABLE_COLUMNS = {
    "客户原话",
    "当前人工回复",
    "建议标准化问题",
    "建议回复结构",
}

METADATA_ONLY_COLUMNS = {
    "源序号",
    "一级分类",
    "二级分类",
    "用户状态/次级标签",
    "命中关键词",
    "情绪强度",
    "风险等级",
    "是否转人工",
}


def main() -> None:
    args = parse_args()
    rows = read_rows(args.source, args.sheet)
    blocks = build_blocks(rows, chunk_size=args.chunk_size, max_replies=args.max_replies)
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_meta.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.write_text(render_markdown(blocks), encoding="utf-8")
    args.output_meta.write_text(
        json.dumps(render_metadata(blocks, args), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        f"Imported {len(rows)} QA rows into {len(blocks)} knowledge blocks:\n"
        f"- {args.output_md}\n"
        f"- {args.output_meta}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import sheet 04 QA rows into clean searchable Markdown plus metadata JSON.",
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--sheet", default=DEFAULT_SHEET)
    parser.add_argument("--output-md", type=Path, default=DEFAULT_OUTPUT_MD)
    parser.add_argument("--output-meta", type=Path, default=DEFAULT_OUTPUT_META)
    parser.add_argument("--chunk-size", type=int, default=20)
    parser.add_argument("--max-replies", type=int, default=8)
    args = parser.parse_args()
    if args.chunk_size < 1:
        raise SystemExit("--chunk-size must be >= 1")
    if args.max_replies < 1:
        raise SystemExit("--max-replies must be >= 1")
    return args


def read_rows(source: Path, sheet_name: str) -> list[dict[str, str]]:
    if not source.exists():
        raise SystemExit(f"Source file not found: {source}")
    workbook = load_workbook(source, read_only=True, data_only=True)
    if sheet_name not in workbook.sheetnames:
        raise SystemExit(f"Sheet not found: {sheet_name}. Available: {', '.join(workbook.sheetnames)}")
    sheet = workbook[sheet_name]
    raw_headers = next(sheet.iter_rows(min_row=1, max_row=1, values_only=True))
    headers = [clean_text(value) for value in raw_headers]
    missing = (SEARCHABLE_COLUMNS | METADATA_ONLY_COLUMNS).difference(headers)
    if missing:
        raise SystemExit(f"Required columns missing: {', '.join(sorted(missing))}")

    rows: list[dict[str, str]] = []
    for row_number, values in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
        row = {
            header: clean_text(values[index] if index < len(values) else "")
            for index, header in enumerate(headers)
            if header
        }
        if not any(row.get(column) for column in SEARCHABLE_COLUMNS):
            continue
        row["_excel_row"] = str(row_number)
        rows.append(row)
    return rows


def build_blocks(
    rows: list[dict[str, str]],
    *,
    chunk_size: int,
    max_replies: int,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        question = one_line(row.get("建议标准化问题")) or one_line(row.get("客户原话")) or "未命名问题"
        grouped[question].append(row)

    sorted_groups = sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0]))
    blocks: list[dict[str, Any]] = []
    for group_index, (question, group_rows) in enumerate(sorted_groups, start=1):
        total_chunks = math.ceil(len(group_rows) / chunk_size)
        for chunk_index in range(total_chunks):
            chunk_rows = group_rows[chunk_index * chunk_size : (chunk_index + 1) * chunk_size]
            block_id = f"QA-{group_index:04d}"
            if total_chunks > 1:
                block_id = f"{block_id}-{chunk_index + 1:02d}"
            blocks.append(
                {
                    "id": block_id,
                    "standard_question": sanitize_searchable_text(question),
                    "chunk_index": chunk_index + 1,
                    "chunk_count": total_chunks,
                    "source_rows": [int(row["_excel_row"]) for row in chunk_rows],
                    "user_phrasings": unique_values(
                        (sanitize_searchable_text(one_line(row.get("客户原话"))) for row in chunk_rows),
                    ),
                    "reference_replies": unique_values(
                        (sanitize_searchable_text(clean_multiline(row.get("当前人工回复"))) for row in chunk_rows),
                        limit=max_replies,
                    ),
                    "reply_structure": sanitize_searchable_text(
                        most_common_text(row.get("建议回复结构") for row in chunk_rows)
                    ),
                    "metadata": metadata_for_rows(chunk_rows),
                }
            )
    return blocks


def render_markdown(blocks: list[dict[str, Any]]) -> str:
    lines = [
        "# 燃人AI 表四现有 QA 知识库",
        "",
    ]
    for block in blocks:
        title = block["standard_question"]
        if block["chunk_count"] > 1:
            title = f"{title}（样本 {block['chunk_index']}/{block['chunk_count']}）"
        lines.extend(
            [
                f"## {block['id']} {title}",
                "",
                "**用户常见问法**",
            ]
        )
        lines.extend(render_bullets(block["user_phrasings"]))
        lines.extend(["", "**参考回复**"])
        lines.extend(render_bullets(block["reference_replies"]))
        if block["reply_structure"]:
            lines.extend(["", "**回复结构**", "", block["reply_structure"]])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_metadata(blocks: list[dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_file": str(args.source),
        "sheet": args.sheet,
        "chunk_size": args.chunk_size,
        "max_replies_per_block": args.max_replies,
        "searchable_markdown": str(args.output_md),
        "note": "Risk, transfer-to-human, category and keyword fields are metadata only. They are intentionally excluded from searchable Markdown.",
        "blocks": [
            {
                "id": block["id"],
                "standard_question": block["standard_question"],
                "chunk_index": block["chunk_index"],
                "chunk_count": block["chunk_count"],
                "source_rows": block["source_rows"],
                "row_count": len(block["source_rows"]),
                "metadata_only": block["metadata"],
            }
            for block in blocks
        ],
    }


def render_bullets(values: list[str]) -> list[str]:
    if not values:
        return ["- 暂无可用样本"]
    return [f"- {escape_bullet(value)}" for value in values]


def metadata_for_rows(rows: list[dict[str, str]]) -> dict[str, list[str]]:
    return {
        "source_ids": unique_values(row.get("源序号") for row in rows),
        "primary_categories": unique_values(row.get("一级分类") for row in rows),
        "secondary_categories": unique_values(row.get("二级分类") for row in rows),
        "user_tags": unique_values(row.get("用户状态/次级标签") for row in rows),
        "matched_keywords": unique_values(split_metadata_tokens(row.get("命中关键词")) for row in rows),
        "emotion_levels": unique_values(row.get("情绪强度") for row in rows),
        "risk_levels": unique_values(row.get("风险等级") for row in rows),
        "requires_human": unique_values(row.get("是否转人工") for row in rows),
    }


def unique_values(values: Any, *, limit: int | None = None) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in flatten(values):
        text = clean_text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
        if limit is not None and len(result) >= limit:
            break
    return result


def flatten(values: Any):
    for value in values:
        if isinstance(value, (list, tuple, set)):
            yield from flatten(value)
        else:
            yield value


def split_metadata_tokens(value: str | None) -> list[str]:
    text = clean_text(value)
    if not text:
        return []
    return [part.strip() for part in re.split(r"[、,，;/；\s]+", text) if part.strip()]


def most_common_text(values: Any) -> str:
    texts = [clean_multiline(value) for value in values if clean_multiline(value)]
    if not texts:
        return ""
    return Counter(texts).most_common(1)[0][0]


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def one_line(value: Any) -> str:
    return re.sub(r"\s+", " ", clean_text(value)).strip()


def clean_multiline(value: Any) -> str:
    text = clean_text(value)
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def sanitize_searchable_text(value: str) -> str:
    text = clean_multiline(value)
    replacements = {
        "转人工处理": "由运营跟进处理",
        "转人工": "运营跟进",
        "记录风险": "记录情况",
        "风险等级": "",
        "是否转人工": "",
        "命中关键词": "",
        "一级分类": "",
        "二级分类": "",
        "情绪强度": "",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return re.sub(r"[ \t]+", " ", text).strip()


def escape_bullet(value: str) -> str:
    return value.replace("\n", "\n  ")


if __name__ == "__main__":
    main()
