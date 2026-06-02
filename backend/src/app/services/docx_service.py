from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

from app.services.document_templates import render_document_content


def write_markdown_docx(content: str, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc = Document()
    section = doc.sections[0]
    section.top_margin = Cm(2.2)
    section.bottom_margin = Cm(2.2)
    section.left_margin = Cm(2.0)
    section.right_margin = Cm(2.0)

    styles = doc.styles
    for style_name in ("Normal", "Heading 1", "Heading 2", "Heading 3"):
        style = styles[style_name]
        style.font.name = "Microsoft YaHei"
        style._element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")
        style.font.size = Pt(10)
        paragraph_format = style.paragraph_format
        paragraph_format.space_before = Pt(0)
        paragraph_format.space_after = Pt(6)
        paragraph_format.line_spacing = Pt(18)

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            doc.add_paragraph("")
            continue

        is_heading = line.startswith("#") or re.match(r"^[一二三四五六七八九十]+[、.．]", line)
        is_bullet = line.startswith(("- ", "• ", "* "))
        text = re.sub(r"^#+\s*", "", line)
        text = re.sub(r"^[-•*]\s*", "", text)
        text = text.replace("**", "")

        paragraph = doc.add_paragraph(style="Normal")
        if is_bullet:
            paragraph.paragraph_format.left_indent = Cm(0.45)
        run = paragraph.add_run(text)
        run.font.name = "Microsoft YaHei"
        run._element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")
        run.font.size = Pt(10)
        run.bold = bool(is_heading)

    doc.save(output_path)


def write_structured_docx(title: str, payload: dict[str, Any], output_path: Path) -> None:
    write_markdown_docx(render_structured_markdown(title, payload), output_path)


def write_template_docx(content_json: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc = Document()
    title = str(content_json.get("title") or "交付文件")
    compact_execution = "28天执行标准与打卡要求" in title
    section = doc.sections[0]
    if compact_execution:
        section.top_margin = Cm(1.45)
        section.bottom_margin = Cm(1.45)
        section.left_margin = Cm(1.45)
        section.right_margin = Cm(1.45)
    else:
        section.top_margin = Cm(2.0)
        section.bottom_margin = Cm(2.0)
        section.left_margin = Cm(1.8)
        section.right_margin = Cm(1.8)
    _setup_styles(doc, compact=compact_execution)

    if content_json.get("show_header_brand", True):
        _add_paragraph(doc, "Powered by HumanBurning.AI", bold=True, size=9 if compact_execution else 10, alignment=WD_ALIGN_PARAGRAPH.CENTER, compact=compact_execution)
    _add_paragraph(doc, title, bold=True, size=12 if compact_execution else 14, alignment=WD_ALIGN_PARAGRAPH.CENTER, compact=compact_execution)
    recipient = str(content_json.get("recipient") or "").strip()
    if recipient and content_json.get("show_recipient", True):
        _add_paragraph(doc, f"To：{recipient}", bold=True, size=9 if compact_execution else 10, compact=compact_execution)

    for block in content_json.get("blocks") or []:
        block_title = str(block.get("title") or "").strip()
        if block.get("display") == "inline":
            _add_inline_paragraph(doc, block_title, str(block.get("content") or "").strip(), compact=compact_execution)
            continue
        if block_title:
            _add_paragraph(doc, block_title, bold=True, size=10 if compact_execution else 11, compact=compact_execution)
        kind = block.get("kind")
        if kind == "table":
            _add_table(doc, block.get("rows") or [], compact=compact_execution)
        elif kind == "list":
            for item in block.get("items") or []:
                text = str(item or "").strip()
                if text:
                    _add_paragraph(doc, f"• {text}", size=9 if compact_execution else 10, compact=compact_execution)
        else:
            for line in str(block.get("content") or "").splitlines():
                if line.strip():
                    _add_paragraph(doc, line, size=9 if compact_execution else 10, compact=compact_execution)

    if content_json.get("show_footer_brand", True):
        _add_paragraph(doc, "Powered by HumanBurning.AI", bold=True, size=9 if compact_execution else 10, alignment=WD_ALIGN_PARAGRAPH.CENTER, compact=compact_execution)
    doc.save(output_path)


def render_structured_markdown(title: str, payload: dict[str, Any]) -> str:
    lines: list[str] = ["Powered by HumanBurning.AI", f"# {title}"]
    subtitle = _string(payload.get("subtitle"))
    if subtitle:
        lines.append(subtitle)
    recipient = _string(payload.get("recipient"))
    if recipient:
        lines.append(f"To：{recipient}")
    summary = _string(payload.get("summary"))
    if summary:
        lines.extend(["", "## 一句话结论", summary])

    overview = payload.get("overview")
    if isinstance(overview, dict) and overview:
        lines.extend(["", "## 用户信息概览"])
        for key, value in overview.items():
            lines.append(f"- {key}: {_string(value) or '未提供'}")

    sections = payload.get("sections")
    if isinstance(sections, list):
        for section in sections:
            if not isinstance(section, dict):
                continue
            heading = _string(section.get("heading"))
            body = section.get("body")
            bullets = section.get("bullets")
            if heading:
                lines.extend(["", f"## {heading}"])
            if isinstance(body, list):
                lines.extend(_string(item) for item in body if _string(item))
            elif _string(body):
                lines.append(_string(body))
            if isinstance(bullets, list):
                lines.extend(f"- {_string(item)}" for item in bullets if _string(item))

    actions = payload.get("actions")
    if isinstance(actions, list) and actions:
        lines.extend(["", "## 本周直接执行"])
        lines.extend(f"{index}. {_string(action)}" for index, action in enumerate(actions, start=1) if _string(action))

    reminders = payload.get("reminders")
    if isinstance(reminders, list) and reminders:
        lines.extend(["", "## 提醒"])
        lines.extend(f"- {_string(item)}" for item in reminders if _string(item))

    lines.extend(["", "Powered by HumanBurning.AI"])
    return "\n".join(line for line in lines if line is not None).strip()


def render_template_markdown(content_json: dict[str, Any]) -> str:
    return render_document_content(content_json)


def safe_filename(value: str) -> str:
    return re.sub(r'[\\/:*?"<>|\\n\\r]+', "_", value).strip() or "未命名"


def _string(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return ""
    return str(value).strip()


def _setup_styles(doc: Document, *, compact: bool = False) -> None:
    styles = doc.styles
    for style_name in ("Normal", "Heading 1", "Heading 2", "Heading 3"):
        style = styles[style_name]
        style.font.name = "Microsoft YaHei"
        style._element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")
        style.font.size = Pt(9 if compact else 10)
        paragraph_format = style.paragraph_format
        paragraph_format.space_before = Pt(0)
        paragraph_format.space_after = Pt(2 if compact else 6)
        paragraph_format.line_spacing = Pt(13.5 if compact else 17)


def _add_paragraph(
    doc: Document,
    text: str,
    *,
    bold: bool = False,
    size: int = 10,
    alignment: WD_ALIGN_PARAGRAPH | None = None,
    compact: bool = False,
) -> None:
    paragraph = doc.add_paragraph(style="Normal")
    if alignment is not None:
        paragraph.alignment = alignment
    paragraph.paragraph_format.space_after = Pt(2 if compact else 6)
    paragraph.paragraph_format.line_spacing = Pt(13.5 if compact else 17)
    run = paragraph.add_run(text)
    run.font.name = "Microsoft YaHei"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")
    run.font.size = Pt(size)
    run.bold = bold


def _add_inline_paragraph(doc: Document, title: str, content: str, *, compact: bool = False) -> None:
    paragraph = doc.add_paragraph(style="Normal")
    paragraph.paragraph_format.space_after = Pt(2 if compact else 6)
    paragraph.paragraph_format.line_spacing = Pt(13.5 if compact else 17)
    if title:
        title_run = paragraph.add_run(f"{title}：")
        title_run.font.name = "Microsoft YaHei"
        title_run._element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")
        title_run.font.size = Pt(9 if compact else 10)
        title_run.bold = True
    content_run = paragraph.add_run(content)
    content_run.font.name = "Microsoft YaHei"
    content_run._element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")
    content_run.font.size = Pt(9 if compact else 10)


def _add_table(doc: Document, rows: list[list[dict[str, Any]]], *, compact: bool = False) -> None:
    if not rows:
        return
    table = doc.add_table(rows=len(rows), cols=max(len(row) for row in rows))
    table.style = "Table Grid"
    for row_index, row in enumerate(rows):
        for cell_index, cell in enumerate(row):
            value = cell.get("value") if isinstance(cell, dict) else cell
            target = table.cell(row_index, cell_index)
            target.text = str(value or "")
            for paragraph in target.paragraphs:
                paragraph.paragraph_format.space_after = Pt(0)
                paragraph.paragraph_format.line_spacing = Pt(11.5 if compact else 13)
                for run in paragraph.runs:
                    run.font.name = "Microsoft YaHei"
                    run._element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")
                    run.font.size = Pt(8 if compact else 9)
                    run.bold = bool(isinstance(cell, dict) and not cell.get("editable", True))
    if not compact:
        doc.add_paragraph("")
