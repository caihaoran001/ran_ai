from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import SessionLocal
from app.models.knowledge_faq import KnowledgeFAQ
from app.services.knowledge_faq_service import confirm_faq, create_faq, update_faq


KNOWLEDGE_FILE = (
    Path(__file__).resolve().parent
    / "knowledge_base"
    / "execution_commitment_faqs.json"
)


def load_execution_commitment_faq_specs() -> tuple[str, list[dict[str, Any]]]:
    payload = json.loads(KNOWLEDGE_FILE.read_text(encoding="utf-8"))
    source_type = str(payload["source_type"])
    specs = payload["faqs"]
    if not isinstance(specs, list) or not specs:
        raise ValueError("execution commitment FAQ specs are empty")
    return source_type, specs


async def upsert_execution_commitment_faqs(
    session: AsyncSession,
) -> list[KnowledgeFAQ]:
    """Idempotently publish the approved execution rules into FAQ + chunks."""

    source_type, specs = load_execution_commitment_faq_specs()
    published: list[KnowledgeFAQ] = []
    for spec in specs:
        source_id = str(spec["source_id"])
        title = str(spec["title"])
        faq = await session.scalar(
            select(KnowledgeFAQ)
            .where(
                or_(
                    (
                        (KnowledgeFAQ.source_type == source_type)
                        & (KnowledgeFAQ.source_id == source_id)
                    ),
                    KnowledgeFAQ.title == title,
                )
            )
            .order_by(KnowledgeFAQ.id.asc())
            .limit(1)
        )
        fields = {
            "title": title,
            "question": str(spec["question"]),
            "answer": str(spec["answer"]),
            "category": str(spec["category"]),
            "tags": [str(item) for item in spec.get("tags", [])],
            "source_type": source_type,
            "source_id": source_id,
        }
        if faq is None:
            faq = await create_faq(
                session,
                **fields,
                change_note="发布执行承诺权威问答",
            )
        elif any(getattr(faq, key) != value for key, value in fields.items()):
            faq = await update_faq(
                session,
                faq,
                **fields,
                change_note="同步执行承诺定版规则",
            )
        if faq.confirmation_state != "confirmed":
            faq = await confirm_faq(session, faq, expected_version=faq.version)
        published.append(faq)
    return published


async def _main() -> None:
    async with SessionLocal() as session:
        faqs = await upsert_execution_commitment_faqs(session)
    for faq in faqs:
        print(f"{faq.faq_uid}\tv{faq.version}\t{faq.confirmation_state}\t{faq.title}")


if __name__ == "__main__":
    asyncio.run(_main())
