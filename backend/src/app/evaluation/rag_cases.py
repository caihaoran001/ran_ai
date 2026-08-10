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
MINIMUM_CASE_COUNT = 16


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


class RagRewriteExpectation(StrictModel):
    decision: Literal["retrieve", "skip"]
    must_include: list[str] = Field(default_factory=list)
    must_preserve_meanings: list[str] = Field(default_factory=list)
    forbidden_inferences: list[str] = Field(default_factory=list)


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
    forbidden_claims: list[str] = Field(default_factory=list)


class RagEvalCase(StrictModel):
    case_id: str = Field(pattern=r"^rag-[a-z0-9-]+$")
    title: str = Field(min_length=1, max_length=120)
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
        elif self.retrieval.required_fact_ids:
            raise ValueError("skip case cannot require retrieved knowledge facts")
        elif any(
            fact_by_id[fact_id].source_scope == "knowledge"
            for fact_id in self.answer.required_fact_ids
        ):
            raise ValueError("skip case cannot require knowledge-scoped answer facts")
        return self


class RagEvalSuite(StrictModel):
    suite_id: str = Field(pattern=r"^rag-[a-z0-9-]+-v\d+$")
    version: int = Field(ge=1)
    description: str = Field(min_length=1)
    cases: list[RagEvalCase] = Field(min_length=MINIMUM_CASE_COUNT)

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
        return self


def default_rag_eval_path() -> Path:
    return Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "rag_eval_cases.v1.json"


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
        "required_risk_tags": sorted(REQUIRED_RISK_TAGS),
        "covered_tags": covered_tags,
    }
