from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.agent_evaluation import (
    AgentCaseExpected,
    AgentEvaluationHistoryMessage,
)


FIXED_CASE_COUNT = 30
REQUIRED_SCENARIOS = frozenset(
    {
        "data_lookup",
        "preference_update",
        "checklist_authorization",
        "reminder_action",
        "service_lifecycle",
        "reward_shipping",
        "knowledge_boundary",
        "context_resolution",
        "no_reply",
    }
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentToolStub(StrictModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{1,119}$")
    results: list[Any] = Field(min_length=1, max_length=10)


class AgentEvalCase(StrictModel):
    case_id: str = Field(pattern=r"^agent-[a-z0-9-]+$")
    title: str = Field(min_length=1, max_length=200)
    origin: Literal["production_derived", "boundary"]
    scenario: str = Field(pattern=r"^[a-z][a-z0-9_]{1,79}$")
    tags: list[str] = Field(min_length=1, max_length=20)
    query_speaker: str = Field(default="用户", min_length=1, max_length=40)
    history: list[AgentEvaluationHistoryMessage] = Field(default_factory=list, max_length=20)
    user_query: str = Field(min_length=1, max_length=4000)
    current_time_context: str = Field(min_length=1, max_length=1000)
    memory_context: str = Field(default="暂无可用用户背景。", max_length=12000)
    knowledge_context: str = Field(default="暂无相关参考问答。", max_length=12000)
    knowledge_hit_count: int | None = Field(default=None, ge=0)
    tool_stubs: list[AgentToolStub] = Field(default_factory=list, max_length=24)
    expected: AgentCaseExpected

    @model_validator(mode="after")
    def validate_tools(self) -> "AgentEvalCase":
        stub_names = [item.name for item in self.tool_stubs]
        if len(stub_names) != len(set(stub_names)):
            raise ValueError("tool_stubs contains duplicate tool names")
        required_names = {item.name for item in self.expected.required_tools}
        allowed_names = set(self.expected.allowed_additional_tools)
        forbidden_names = set(self.expected.forbidden_tools)
        if len(allowed_names) != len(self.expected.allowed_additional_tools):
            raise ValueError("allowed_additional_tools contains duplicate tool names")
        overlap = allowed_names & (required_names | forbidden_names)
        if overlap:
            raise ValueError(
                "allowed_additional_tools must not repeat required or forbidden tools: "
                f"{sorted(overlap)}"
            )
        missing = (required_names | allowed_names) - set(stub_names)
        if missing:
            raise ValueError(
                "required or allowed tools have no sandbox result: "
                f"{sorted(missing)}"
            )
        return self


class AgentEvalSuite(StrictModel):
    suite_id: str = Field(pattern=r"^agent-task-[a-z0-9-]+-v\d+$")
    version: int = Field(ge=1)
    repeat_count: int = Field(default=2, ge=1, le=10)
    description: str = Field(min_length=1, max_length=1000)
    cases: list[AgentEvalCase] = Field(min_length=FIXED_CASE_COUNT, max_length=FIXED_CASE_COUNT)

    @model_validator(mode="after")
    def validate_suite(self) -> "AgentEvalSuite":
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("suite contains duplicate case_id")
        scenarios = {case.scenario for case in self.cases}
        missing = REQUIRED_SCENARIOS - scenarios
        if missing:
            raise ValueError(f"suite misses required scenarios: {sorted(missing)}")
        origins = Counter(case.origin for case in self.cases)
        if origins != {"production_derived": 15, "boundary": 15}:
            raise ValueError("suite must contain 15 production-derived and 15 boundary cases")
        return self


def default_agent_eval_path() -> Path:
    return Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "agent_eval_cases.v3.json"


def load_agent_eval_suite(path: Path | None = None) -> AgentEvalSuite:
    payload = json.loads((path or default_agent_eval_path()).read_text(encoding="utf-8"))
    return AgentEvalSuite.model_validate(payload)


def summarize_agent_eval_suite(suite: AgentEvalSuite) -> dict[str, object]:
    return {
        "suite_id": suite.suite_id,
        "version": suite.version,
        "repeat_count": suite.repeat_count,
        "case_count": len(suite.cases),
        "origin_counts": dict(sorted(Counter(case.origin for case in suite.cases).items())),
        "scenario_counts": dict(sorted(Counter(case.scenario for case in suite.cases).items())),
        "tags": sorted({tag for case in suite.cases for tag in case.tags}),
    }
