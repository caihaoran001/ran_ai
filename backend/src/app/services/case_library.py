from __future__ import annotations

from functools import lru_cache
from pathlib import Path


CASE_LIBRARY_DIR = Path(__file__).resolve().parent / "case_library"


@lru_cache(maxsize=1)
def _skill_text() -> str:
    path = CASE_LIBRARY_DIR / "skill.md"
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""
