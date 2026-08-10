from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.evaluation.rag_cases import (
    RagAnswerExpectation,
    RagAuthoritativeFact,
    RagEvalCase,
    RagRetrievalExpectation,
    RagRewriteExpectation,
    load_rag_eval_suite,
)
from app.models.knowledge_faq import KnowledgeFAQ


DEFAULT_FAQ_SEED_COUNT = 25


class EvaluationDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=1, ge=1)
    dataset_id: str
    dataset_version: int = Field(ge=1)
    generated_at: datetime
    core_case_count: int = Field(ge=0)
    faq_seed_count: int = Field(ge=0)
    cases: list[RagEvalCase]


def _faq_case(faq: KnowledgeFAQ) -> RagEvalCase:
    digest = hashlib.sha256(faq.faq_uid.encode("utf-8")).hexdigest()[:16]
    fact_id = f"faq-{digest}"
    return RagEvalCase(
        case_id=f"rag-faq-{digest}",
        title=f"生产 FAQ：{faq.title}"[:120],
        tags=["direct_knowledge", "production_faq"],
        history=[],
        user_query=faq.question[:500],
        authoritative_facts=[
            RagAuthoritativeFact(
                fact_id=fact_id,
                statement=faq.answer[:12000],
                provenance=f"knowledge_faqs:{faq.faq_uid}:v{faq.version}",
                source_scope="knowledge",
                source_ids=[faq.faq_uid],
            )
        ],
        rewrite=RagRewriteExpectation(
            decision="retrieve",
            must_preserve_meanings=[faq.question[:500]],
        ),
        retrieval=RagRetrievalExpectation(required_fact_ids=[fact_id]),
        answer=RagAnswerExpectation(
            response_mode="answer",
            required_fact_ids=[fact_id],
        ),
    )


async def build_dataset(
    session: AsyncSession,
    *,
    faq_seed_count: int = DEFAULT_FAQ_SEED_COUNT,
) -> EvaluationDataset:
    core = load_rag_eval_suite()
    faqs = list(
        await session.scalars(
            select(KnowledgeFAQ)
            .where(
                KnowledgeFAQ.status == "active",
                KnowledgeFAQ.disabled_at.is_(None),
                KnowledgeFAQ.archived_at.is_(None),
            )
            .order_by(KnowledgeFAQ.faq_uid)
            .limit(max(0, faq_seed_count))
        )
    )
    cases = [*core.cases, *(_faq_case(faq) for faq in faqs)]
    return EvaluationDataset(
        dataset_id="rag-production-v1",
        dataset_version=1,
        generated_at=datetime.now(timezone.utc),
        core_case_count=len(core.cases),
        faq_seed_count=len(faqs),
        cases=cases,
    )


def load_dataset(path: Path) -> EvaluationDataset:
    return EvaluationDataset.model_validate_json(path.read_text(encoding="utf-8"))


def write_dataset(path: Path, dataset: EvaluationDataset) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(dataset.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)
