from __future__ import annotations

from pydantic import BaseModel, Field
from langchain_core.tools import BaseTool, StructuredTool

from app.services.agent.skill_registry import SkillRegistry


class SkillArgs(BaseModel):
    name: str = Field(description="Skill name from the visible catalog.")


class ReadSkillFileArgs(BaseModel):
    skill_name: str = Field(description="Skill name from the visible catalog.")
    relative_path: str = Field(description="Relative text file path inside that skill directory.")


def build_skill_tools(registry: SkillRegistry) -> list[BaseTool]:
    async def skill(name: str) -> str:
        try:
            return registry.read_skill(name)
        except Exception as exc:
            return f"SKILL_LOAD_ERROR: {type(exc).__name__}: {exc}"

    async def read_skill_file(skill_name: str, relative_path: str) -> str:
        try:
            return registry.read_skill_file(skill_name, relative_path)
        except Exception as exc:
            return f"SKILL_FILE_ERROR: {type(exc).__name__}: {exc}"

    return [
        StructuredTool.from_function(
            coroutine=skill,
            name="skill",
            description="Load the full SKILL.md for a listed skill when its guidance is relevant.",
            args_schema=SkillArgs,
        ),
        StructuredTool.from_function(
            coroutine=read_skill_file,
            name="read_skill_file",
            description="Read an allowed referenced text file inside a previously loaded skill directory.",
            args_schema=ReadSkillFileArgs,
        ),
    ]
