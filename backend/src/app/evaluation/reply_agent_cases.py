from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.evaluation.agent_cases import AgentEvalCase
from app.evaluation.rag_cases import RagEvalCase, validate_retrieval_clause_contract


FIXED_CASE_COUNT = 80
FIXED_RETRIEVAL_COUNTS = {"retrieve": 41, "skip": 39}
REQUIRED_RETRIEVAL_FAMILIES = frozenset(
    {
        "dosage_scale",
        "timing_missed",
        "product_pairing",
        "brewing_usage",
        "adverse_reaction",
        "multi_turn_resolution",
        "mixed_report_question",
        "retrieval_interference",
    }
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReplyHandoffExpectation(StrictModel):
    history: list[str] = Field(default_factory=list, max_length=10)
    user_query: str = Field(min_length=1, max_length=4000)
    expected_risk_codes: list[str] = Field(min_length=1, max_length=10)
    requires_handoff: Literal[True] = True
    reply_agent_must_not_run: Literal[True] = True
    forbidden_response_patterns: list[str] = Field(min_length=1, max_length=20)


class ReplyAgentEvalCase(StrictModel):
    case_id: str = Field(pattern=r"^RA-\d{3}$")
    title: str = Field(min_length=1, max_length=200)
    source_case_ids: list[str] = Field(min_length=1, max_length=10)
    execution_kind: Literal["rag", "agent", "handoff"]
    retrieval_decision: Literal["retrieve", "skip"]
    retrieval_families: list[
        Literal[
            "dosage_scale",
            "timing_missed",
            "product_pairing",
            "brewing_usage",
            "adverse_reaction",
            "multi_turn_resolution",
            "mixed_report_question",
            "retrieval_interference",
            "diet_rule",
            "service_rule",
            "health_knowledge",
        ]
    ] = Field(default_factory=list)
    metric_groups: list[Literal["M0", "MR", "MT", "MB"]] = Field(min_length=1)
    tags: list[str] = Field(min_length=1, max_length=30)
    rag_case: RagEvalCase | None = None
    agent_case: AgentEvalCase | None = None
    handoff: ReplyHandoffExpectation | None = None

    @model_validator(mode="after")
    def validate_routing(self) -> "ReplyAgentEvalCase":
        payloads = {
            "rag": self.rag_case,
            "agent": self.agent_case,
            "handoff": self.handoff,
        }
        if payloads[self.execution_kind] is None:
            raise ValueError(
                f"{self.execution_kind} case is missing its execution payload"
            )
        if sum(value is not None for value in payloads.values()) != 1:
            raise ValueError("case must define exactly one execution payload")
        if len(self.metric_groups) != len(set(self.metric_groups)):
            raise ValueError("metric_groups contains duplicates")
        if "M0" not in self.metric_groups:
            raise ValueError("every case must enable M0")
        if ("MR" in self.metric_groups) != (self.retrieval_decision == "retrieve"):
            raise ValueError("MR must be enabled exactly for retrieve cases")
        if self.retrieval_decision == "retrieve" and not self.retrieval_families:
            raise ValueError("retrieve case must declare at least one retrieval family")
        if self.retrieval_decision == "skip" and self.retrieval_families:
            raise ValueError("skip case cannot declare retrieval families")
        if self.rag_case and self.rag_case.rewrite.decision != self.retrieval_decision:
            raise ValueError(
                "rag payload retrieval decision disagrees with unified decision"
            )
        if self.rag_case:
            validate_retrieval_clause_contract(self.rag_case)
        if self.agent_case and self.retrieval_decision != "skip":
            raise ValueError(
                "legacy Agent payloads must skip the general knowledge index"
            )
        if self.agent_case:
            has_tools = bool(
                self.agent_case.expected.required_tools
                or self.agent_case.expected.forbidden_tools
                or self.agent_case.expected.allowed_additional_tools
            )
            if has_tools != ("MT" in self.metric_groups):
                raise ValueError("MT routing disagrees with Agent tool expectations")
        if self.execution_kind == "handoff" and "MB" not in self.metric_groups:
            raise ValueError("handoff cases must enable MB")
        return self


class ReplyAgentEvalSuite(StrictModel):
    suite_id: Literal[
        "reply-agent-core-v1",
        "reply-agent-core-v2",
        "reply-agent-core-v3",
        "reply-agent-core-v4",
    ]
    version: Literal[1, 2, 3, 4]
    description: str = Field(min_length=1, max_length=1000)
    cases: list[ReplyAgentEvalCase] = Field(
        min_length=FIXED_CASE_COUNT,
        max_length=FIXED_CASE_COUNT,
    )

    @model_validator(mode="after")
    def validate_suite_contract(self) -> "ReplyAgentEvalSuite":
        expected_version = int(self.suite_id.rsplit("v", 1)[1])
        if self.version != expected_version:
            raise ValueError("suite_id and version must identify the same contract")
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("suite contains duplicate case_id")
        expected_ids = [f"RA-{index:03d}" for index in range(1, FIXED_CASE_COUNT + 1)]
        if case_ids != expected_ids:
            raise ValueError("suite case_ids must be the ordered range RA-001..RA-080")
        source_ids = [source for case in self.cases for source in case.source_case_ids]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("source_case_ids cannot be reused across unified cases")
        decision_counts = Counter(case.retrieval_decision for case in self.cases)
        if dict(decision_counts) != FIXED_RETRIEVAL_COUNTS:
            raise ValueError("suite must contain exactly 41 retrieve and 39 skip cases")
        family_counts = Counter(
            family for case in self.cases for family in case.retrieval_families
        )
        missing = REQUIRED_RETRIEVAL_FAMILIES - set(family_counts)
        if missing:
            raise ValueError(f"suite misses retrieval families: {sorted(missing)}")
        underfilled = {
            family: family_counts[family]
            for family in REQUIRED_RETRIEVAL_FAMILIES
            if family_counts[family] < 4
        }
        if underfilled:
            raise ValueError(
                f"each retrieval family needs at least four cases: {underfilled}"
            )
        for required_kind in ("rag", "agent", "handoff"):
            if not any(case.execution_kind == required_kind for case in self.cases):
                raise ValueError(f"suite misses execution kind: {required_kind}")
        return self


def default_reply_agent_eval_path() -> Path:
    return (
        Path(__file__).resolve().parents[3]
        / "tests"
        / "fixtures"
        / "reply_agent_eval_cases.v4.json"
    )


def load_reply_agent_eval_suite(path: Path | None = None) -> ReplyAgentEvalSuite:
    payload = json.loads(
        (path or default_reply_agent_eval_path()).read_text(encoding="utf-8")
    )
    return ReplyAgentEvalSuite.model_validate(payload)


def summarize_reply_agent_eval_suite(
    suite: ReplyAgentEvalSuite,
) -> dict[str, object]:
    return {
        "suite_id": suite.suite_id,
        "version": suite.version,
        "case_count": len(suite.cases),
        "retrieval_counts": dict(
            sorted(Counter(case.retrieval_decision for case in suite.cases).items())
        ),
        "execution_counts": dict(
            sorted(Counter(case.execution_kind for case in suite.cases).items())
        ),
        "metric_group_counts": dict(
            sorted(
                Counter(
                    group for case in suite.cases for group in case.metric_groups
                ).items()
            )
        ),
        "retrieval_family_counts": dict(
            sorted(
                Counter(
                    family for case in suite.cases for family in case.retrieval_families
                ).items()
            )
        ),
    }
