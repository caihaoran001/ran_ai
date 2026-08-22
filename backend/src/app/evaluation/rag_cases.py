from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


REQUIRED_RISK_TAGS = frozenset(
    {
        "ellipsis",
        "reference_resolution",
        "product_switch",
        "staff_correction",
        "number",
        "comparison",
        "negation",
        "ambiguous",
        "skip_retrieval",
    }
)
FIXED_CASE_COUNT = 50
FIXED_ORIGIN_COUNTS = {"faq_rewrite": 25, "real_chat": 25}
# Decision quotas are per registered suite version: v2 encodes the legacy
# retrieval gating (generic health/diet/exercise retrieve), v3 encodes the
# product-scoped gating where only product-related questions retrieve.
SUITE_DECISION_COUNTS = {
    ("rag-fixed-50-v2", 2): {"retrieve": 35, "skip": 15},
    ("rag-fixed-50-v3", 3): {"retrieve": 16, "skip": 34},
}
# Legacy suites force every faq_rewrite case to retrieve; product-scoped
# suites (v3+) may mark faq cases as skip when they are not product-related.
VERSIONS_REQUIRING_FAQ_RETRIEVAL = {("rag-fixed-50-v2", 2)}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RagHistoryMessage(StrictModel):
    role: Literal["user", "staff", "assistant"]
    content: str = Field(min_length=1, max_length=1000)


class RagAuthoritativeFact(StrictModel):
    fact_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    statement: str = Field(min_length=1, max_length=12000)
    provenance: str = Field(min_length=1, max_length=200)
    source_scope: Literal["knowledge", "conversation", "runtime"] = "knowledge"
    source_ids: list[str] = Field(default_factory=list, max_length=20)


class RagCaseOrigin(StrictModel):
    kind: Literal["faq_rewrite", "real_chat"]
    reference_id: str = Field(pattern=r"^[a-z0-9][a-z0-9:_-]*$")


class RagRewriteExpectation(StrictModel):
    decision: Literal["retrieve", "skip"]
    must_include: list[str] = Field(default_factory=list)
    must_preserve_meanings: list[str] = Field(default_factory=list)


class RagRetrievalExpectation(StrictModel):
    required_fact_ids: list[str] = Field(default_factory=list)
    forbidden_source_types: list[str] = Field(default_factory=lambda: ["case"])


class RagAnswerExpectation(StrictModel):
    response_mode: Literal[
        "answer",
        "acknowledge",
        "clarify",
        "use_runtime_data",
        "support",
    ]
    required_fact_ids: list[str] = Field(default_factory=list)


class RagEvalCase(StrictModel):
    case_id: str = Field(pattern=r"^rag-[a-z0-9-]+$")
    title: str = Field(min_length=1, max_length=120)
    origin: RagCaseOrigin
    tags: list[str] = Field(min_length=1)
    history: list[RagHistoryMessage] = Field(default_factory=list, max_length=10)
    user_query: str = Field(min_length=1, max_length=500)
    authoritative_facts: list[RagAuthoritativeFact] = Field(default_factory=list)
    rewrite: RagRewriteExpectation
    retrieval: RagRetrievalExpectation
    answer: RagAnswerExpectation

    @model_validator(mode="after")
    def validate_fact_references(self) -> "RagEvalCase":
        fact_ids = [fact.fact_id for fact in self.authoritative_facts]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("authoritative_facts contains duplicate fact_id")
        referenced = set(self.retrieval.required_fact_ids) | set(
            self.answer.required_fact_ids
        )
        unknown = referenced - set(fact_ids)
        if unknown:
            raise ValueError(f"expectations reference unknown facts: {sorted(unknown)}")
        fact_by_id = {fact.fact_id: fact for fact in self.authoritative_facts}
        retrieval_scopes = {
            fact_by_id[fact_id].source_scope
            for fact_id in self.retrieval.required_fact_ids
        }
        if retrieval_scopes - {"knowledge"}:
            raise ValueError("retrieval can require only knowledge-scoped facts")
        if self.rewrite.decision == "retrieve":
            if not self.retrieval.required_fact_ids:
                raise ValueError("retrieve case must declare required retrieval facts")
            if not self.answer.required_fact_ids:
                raise ValueError("retrieve case must declare grounded answer facts")
        return self


class RagEvalSuite(StrictModel):
    suite_id: str = Field(pattern=r"^rag-[a-z0-9-]+-v\d+$")
    version: int = Field(ge=1)
    description: str = Field(min_length=1)
    cases: list[RagEvalCase] = Field(min_length=FIXED_CASE_COUNT)

    @model_validator(mode="after")
    def validate_suite(self) -> "RagEvalSuite":
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("suite contains duplicate case_id")
        covered_tags = {tag for case in self.cases for tag in case.tags}
        missing_tags = REQUIRED_RISK_TAGS - covered_tags
        if missing_tags:
            raise ValueError(f"suite misses required risk tags: {sorted(missing_tags)}")
        decisions = {case.rewrite.decision for case in self.cases}
        if decisions != {"retrieve", "skip"}:
            raise ValueError("suite must contain both retrieve and skip cases")
        origin_counts = Counter(case.origin.kind for case in self.cases)
        if dict(origin_counts) != FIXED_ORIGIN_COUNTS:
            raise ValueError(
                "suite must contain exactly 25 faq_rewrite and 25 real_chat cases"
            )
        suite_key = (self.suite_id, self.version)
        expected_counts = SUITE_DECISION_COUNTS.get(suite_key)
        if expected_counts is None:
            raise ValueError(
                "suite decision quota is not registered: "
                f"register ({self.suite_id}, {self.version}) in SUITE_DECISION_COUNTS"
            )
        origin_refs = [
            (case.origin.kind, case.origin.reference_id) for case in self.cases
        ]
        if len(origin_refs) != len(set(origin_refs)):
            raise ValueError("suite contains duplicate origin references")
        if (
            suite_key in VERSIONS_REQUIRING_FAQ_RETRIEVAL
            and any(
                case.origin.kind == "faq_rewrite"
                and case.rewrite.decision != "retrieve"
                for case in self.cases
            )
        ):
            raise ValueError("faq_rewrite cases must retrieve knowledge")
        decision_counts = Counter(case.rewrite.decision for case in self.cases)
        if dict(decision_counts) != expected_counts:
            raise ValueError(
                f"suite {self.suite_id} v{self.version} must contain exactly "
                f"{expected_counts['retrieve']} retrieve and {expected_counts['skip']} skip cases"
            )
        if suite_key in VERSIONS_REQUIRING_FAQ_RETRIEVAL:
            for case in self.cases:
                if case.rewrite.decision != "skip":
                    continue
                fact_by_id = {
                    fact.fact_id: fact for fact in case.authoritative_facts
                }
                if case.retrieval.required_fact_ids:
                    raise ValueError(
                        "legacy suites: skip case cannot require retrieved "
                        f"knowledge facts: {case.case_id}"
                    )
                if any(
                    fact_by_id[fact_id].source_scope == "knowledge"
                    for fact_id in case.answer.required_fact_ids
                    if fact_id in fact_by_id
                ):
                    raise ValueError(
                        "legacy suites: skip case cannot require "
                        f"knowledge-scoped answer facts: {case.case_id}"
                    )
        return self


def default_rag_eval_path() -> Path:
    return Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "rag_eval_cases.v2.json"


def load_rag_eval_suite(path: Path | None = None) -> RagEvalSuite:
    case_path = path or default_rag_eval_path()
    payload = json.loads(case_path.read_text(encoding="utf-8"))
    return RagEvalSuite.model_validate(payload)


def summarize_rag_eval_suite(suite: RagEvalSuite) -> dict[str, object]:
    decision_counts = Counter(case.rewrite.decision for case in suite.cases)
    covered_tags = sorted({tag for case in suite.cases for tag in case.tags})
    return {
        "suite_id": suite.suite_id,
        "version": suite.version,
        "case_count": len(suite.cases),
        "decision_counts": dict(sorted(decision_counts.items())),
        "origin_counts": dict(
            sorted(Counter(case.origin.kind for case in suite.cases).items())
        ),
        "required_risk_tags": sorted(REQUIRED_RISK_TAGS),
        "covered_tags": covered_tags,
    }
