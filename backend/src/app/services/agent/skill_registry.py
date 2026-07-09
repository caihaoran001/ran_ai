from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class SkillNotFoundError(ValueError):
    pass


class SkillFileRejectedError(ValueError):
    pass


@dataclass(frozen=True)
class SkillInfo:
    name: str
    description: str


class SkillRegistry:
    def __init__(self, base_dir: Path | None = None, *, max_file_bytes: int = 40_000):
        self.base_dir = base_dir or Path(__file__).resolve().parents[2] / "agent_skills"
        self.max_file_bytes = max_file_bytes
        self._skills_cache: list[SkillInfo] | None = None

    def list_skills(self) -> list[SkillInfo]:
        # skill 是随部署一起落盘的静态文件（改代码要 docker restart），进程内缓存一次目录扫描即可，
        # 避免每次群聊请求都遍历目录读盘解析描述。
        if self._skills_cache is not None:
            return self._skills_cache
        skills: list[SkillInfo] = []
        if self.base_dir.exists():
            for path in sorted(self.base_dir.iterdir()):
                if not path.is_dir():
                    continue
                skill_md = path / "SKILL.md"
                if not skill_md.is_file():
                    continue
                description = self._read_description(skill_md)
                skills.append(SkillInfo(name=path.name, description=description))
        self._skills_cache = skills
        return skills

    def catalog_text(self) -> str:
        skills = self.list_skills()
        if not skills:
            return "无"
        return "\n".join(f"- {item.name}: {item.description}" for item in skills)

    def read_skill(self, name: str) -> str:
        return self.read_skill_file(name, "SKILL.md")

    def read_skill_file(self, skill_name: str, relative_path: str) -> str:
        skill_dir = self._skill_dir(skill_name)
        requested = self._resolve_text_file(skill_dir, relative_path)
        return requested.read_text(encoding="utf-8")

    def _skill_dir(self, name: str) -> Path:
        if not name or "/" in name or "\\" in name or name.startswith("."):
            raise SkillNotFoundError("skill name is invalid")
        skill_dir = (self.base_dir / name).resolve()
        base = self.base_dir.resolve()
        if not str(skill_dir).startswith(str(base)) or not skill_dir.is_dir():
            raise SkillNotFoundError(f"skill not found: {name}")
        return skill_dir

    def _resolve_text_file(self, skill_dir: Path, relative_path: str) -> Path:
        if not relative_path or Path(relative_path).is_absolute() or ".." in Path(relative_path).parts:
            raise SkillFileRejectedError("skill file path is invalid")
        requested = (skill_dir / relative_path).resolve()
        if not str(requested).startswith(str(skill_dir.resolve())):
            raise SkillFileRejectedError("skill file path escapes skill directory")
        if requested.suffix.lower() not in {".md", ".txt", ".json"}:
            raise SkillFileRejectedError("skill file type is not allowed")
        if not requested.is_file():
            raise SkillFileRejectedError("skill file does not exist")
        if requested.stat().st_size > self.max_file_bytes:
            raise SkillFileRejectedError("skill file is too large")
        return requested

    def _read_description(self, skill_md: Path) -> str:
        try:
            text = skill_md.read_text(encoding="utf-8")
        except OSError:
            return "读取失败"
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("描述:") or stripped.lower().startswith("description:"):
                return stripped.split(":", 1)[-1].strip() or "无描述"
        for line in text.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                return stripped[:120]
        return "无描述"
