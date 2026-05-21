from __future__ import annotations

from functools import lru_cache
from pathlib import Path


@lru_cache
def get_manual_prompt() -> str:
    repo_root_prompt = Path(__file__).resolve().parents[4] / "手动版提示词.md"
    for path in (
        repo_root_prompt,
        Path(__file__).with_name("manual_prompt.md"),
    ):
        if path.exists():
            return path.read_text(encoding="utf-8")
    return (
        "请基于用户问卷数据生成《个人能量重启处方单》和"
        "《28天执行标准与打卡要求》，不做医疗诊断。"
    )
