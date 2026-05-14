from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from docx import Document
from docx.oxml.ns import qn
from docx.shared import Cm, Pt


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


def safe_filename(value: str) -> str:
    return re.sub(r'[\\/:*?"<>|\\n\\r]+', "_", value).strip() or "未命名"


def _string(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return ""
    return str(value).strip()
