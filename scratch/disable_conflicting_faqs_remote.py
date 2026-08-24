"""Preview or disable the reviewed production FAQ conflicts.

Default mode is read-only.  ``--apply`` changes only the exact FAQ rows below
from active to disabled and synchronizes their knowledge chunks through the
normal FAQ service.  Disabled rows and their version history remain recoverable.
"""

from __future__ import annotations

import argparse
import json
import textwrap

from local_prebuilt_deploy import connect_ecs


# Production read-only audit on 2026-08-24 linked every FAQ to the originating
# knowledge draft.  Keeping both identifiers makes the operation fail closed if
# an ID is reused, edited, or no longer refers to the reviewed candidate.
TARGETS: dict[int, int] = {
    112: 149,
    113: 147,
    125: 132,
    129: 126,
    147: 184,
    162: 173,
    183: 214,
    186: 208,
    223: 220,
    252: 297,
    254: 304,
    255: 302,
    263: 333,
    265: 317,
    273: 340,
    275: 338,
    278: 330,
    282: 326,
    283: 325,
    286: 324,
    303: 370,
    306: 369,
    307: 365,
}


def _remote_code(
    *,
    apply: bool,
    verify_retrieval: bool,
    audit_active: bool,
    audit_min_id: int,
    audit_max_id: int,
) -> str:
    return textwrap.dedent(
        f"""
        import asyncio
        import json

        from sqlalchemy import select

        from app.db.session import SessionLocal
        from app.models.knowledge_chunk import KnowledgeChunk
        from app.models.knowledge_faq import KnowledgeFAQ
        from app.services.knowledge_faq_service import set_faq_status
        from app.services.knowledge_index import retrieve_indexed_knowledge_result

        TARGETS = {TARGETS!r}
        APPLY = {apply!r}
        VERIFY_RETRIEVAL = {verify_retrieval!r}
        AUDIT_ACTIVE = {audit_active!r}
        AUDIT_MIN_ID = {audit_min_id!r}
        AUDIT_MAX_ID = {audit_max_id!r}
        RETRIEVAL_CASES = [
            ("午B下午三点还能补喝吗", {{130, 189}}),
            ("早C午B晚A可以空腹喝吗，能先喝再吃饭吗", {{53, 225}}),
            (
                "产品服用顺序：先喝产品再吃正餐是否可以？之前建议随正餐边吃边喝，想确认先喝产品再吃正餐是否可行。",
                {{53, 149, 262}},
            ),
            ("瓶盖、一大刻度和三小刻度怎么换算", {{141}}),
            ("喝午B后心慌手抖怎么办", {{3}}),
            ("喝午B后腹泻怎么办", {{96}}),
        ]


        async def run():
            async with SessionLocal() as session:
                faqs = list(
                    await session.scalars(
                        select(KnowledgeFAQ)
                        .where(KnowledgeFAQ.id.in_(TARGETS))
                        .order_by(KnowledgeFAQ.id)
                    )
                )
                chunks = list(
                    await session.scalars(
                        select(KnowledgeChunk)
                        .where(
                            KnowledgeChunk.source_uri.in_(
                                [f"knowledge_faq:{{faq_id}}" for faq_id in TARGETS]
                            )
                        )
                        .order_by(KnowledgeChunk.id)
                    )
                )
                chunks_by_faq = {{
                    int((chunk.metadata_json or {{}}).get("faq_id")): chunk
                    for chunk in chunks
                    if str((chunk.metadata_json or {{}}).get("faq_id") or "").isdigit()
                }}
                faqs_by_id = {{faq.id: faq for faq in faqs}}
                issues = []
                rows = []
                for faq_id, draft_id in TARGETS.items():
                    faq = faqs_by_id.get(faq_id)
                    chunk = chunks_by_faq.get(faq_id)
                    if faq is None:
                        issues.append(f"FAQ {{faq_id}} missing")
                        continue
                    if faq.source_type != "knowledge_draft" or faq.source_id != str(draft_id):
                        issues.append(
                            f"FAQ {{faq_id}} provenance changed: "
                            f"{{faq.source_type}}/{{faq.source_id}}"
                        )
                    if faq.status not in {{"active", "disabled"}}:
                        issues.append(f"FAQ {{faq_id}} unexpected status {{faq.status}}")
                    if chunk is None:
                        issues.append(f"FAQ {{faq_id}} knowledge chunk missing")
                    elif chunk.status not in {{"active", "disabled"}}:
                        issues.append(
                            f"FAQ {{faq_id}} chunk {{chunk.id}} unexpected status {{chunk.status}}"
                        )
                    rows.append(
                        {{
                            "faq_id": faq.id,
                            "draft_id": draft_id,
                            "status": faq.status,
                            "chunk_id": chunk.id if chunk else None,
                            "chunk_status": chunk.status if chunk else None,
                            "question": faq.question,
                            "answer": faq.answer,
                        }}
                    )

                if len(faqs) != len(TARGETS):
                    issues.append(f"expected {{len(TARGETS)}} FAQs, found {{len(faqs)}}")
                if issues:
                    print(json.dumps({{"mode": "blocked", "issues": issues, "rows": rows}}, ensure_ascii=False))
                    return

                changed = []
                if APPLY:
                    for faq in faqs:
                        if faq.status == "active":
                            await set_faq_status(session, faq, "disabled")
                            changed.append(faq.id)
                    faqs = list(
                        await session.scalars(
                            select(KnowledgeFAQ)
                            .where(KnowledgeFAQ.id.in_(TARGETS))
                            .order_by(KnowledgeFAQ.id)
                        )
                    )
                    chunks = list(
                        await session.scalars(
                            select(KnowledgeChunk)
                            .where(
                                KnowledgeChunk.source_uri.in_(
                                    [f"knowledge_faq:{{faq_id}}" for faq_id in TARGETS]
                                )
                            )
                            .order_by(KnowledgeChunk.id)
                        )
                    )
                    final_faq = {{faq.id: faq.status for faq in faqs}}
                    final_chunk = {{
                        int((chunk.metadata_json or {{}}).get("faq_id")): chunk.status
                        for chunk in chunks
                        if str((chunk.metadata_json or {{}}).get("faq_id") or "").isdigit()
                    }}
                    if any(final_faq.get(faq_id) != "disabled" for faq_id in TARGETS):
                        raise RuntimeError("FAQ status verification failed")
                    if any(final_chunk.get(faq_id) != "disabled" for faq_id in TARGETS):
                        raise RuntimeError("knowledge chunk status verification failed")
                    mode = "applied"
                else:
                    final_faq = {{faq.id: faq.status for faq in faqs}}
                    final_chunk = {{faq_id: chunks_by_faq[faq_id].status for faq_id in TARGETS}}
                    mode = "preview"

                retrieval_checks = []
                if VERIFY_RETRIEVAL:
                    disabled_chunk_uids = {{f"faq:{{faq_id}}" for faq_id in TARGETS}}
                    for query, expected_any in RETRIEVAL_CASES:
                        result = await retrieve_indexed_knowledge_result(
                            session,
                            query,
                            exclude_source_types={{"case"}},
                        )
                        hit_faq_ids = [
                            int(hit.chunk_uid.split(":", 1)[1])
                            for hit in result.hits
                            if hit.chunk_uid.startswith("faq:")
                            and hit.chunk_uid.split(":", 1)[1].isdigit()
                        ]
                        leaked = [
                            hit.chunk_uid
                            for hit in result.hits
                            if hit.chunk_uid in disabled_chunk_uids
                        ]
                        retrieval_checks.append(
                            {{
                                "query": query,
                                "expected_any": sorted(expected_any),
                                "matched_expected": sorted(expected_any.intersection(hit_faq_ids)),
                                "disabled_hits": leaked,
                                "hits": [
                                    {{
                                        "rank": hit.rank,
                                        "chunk_uid": hit.chunk_uid,
                                        "title": hit.title,
                                        "content_excerpt": hit.content[:800],
                                    }}
                                    for hit in result.hits
                                ],
                            }}
                        )

                active_draft_faqs = []
                if AUDIT_ACTIVE:
                    active_faqs = list(
                        await session.scalars(
                            select(KnowledgeFAQ)
                            .where(
                                KnowledgeFAQ.status == "active",
                                KnowledgeFAQ.source_type == "knowledge_draft",
                                KnowledgeFAQ.category == "产品使用",
                                KnowledgeFAQ.id >= AUDIT_MIN_ID,
                                KnowledgeFAQ.id <= AUDIT_MAX_ID,
                            )
                            .order_by(KnowledgeFAQ.id)
                        )
                    )
                    active_draft_faqs = [
                        {{
                            "faq_id": faq.id,
                            "draft_id": faq.source_id,
                            "category": faq.category,
                            "question": faq.question,
                            "answer": faq.answer,
                        }}
                        for faq in active_faqs
                    ]

                print(
                    json.dumps(
                        {{
                            "mode": mode,
                            "target_count": len(TARGETS),
                            "changed": changed,
                            "faq_statuses": final_faq,
                            "chunk_statuses": final_chunk,
                            "rows": [] if AUDIT_ACTIVE else rows,
                            "retrieval_checks": retrieval_checks,
                            "active_draft_faqs": active_draft_faqs,
                        }},
                        ensure_ascii=False,
                    )
                )


        asyncio.run(run())
        """
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--verify-retrieval", action="store_true")
    parser.add_argument("--audit-active", action="store_true")
    parser.add_argument("--audit-min-id", type=int, default=1)
    parser.add_argument("--audit-max-id", type=int, default=10_000)
    args = parser.parse_args()

    client = connect_ecs()
    try:
        command = "docker exec -i ran_ai-backend-1 uv run python -"
        stdin, stdout, stderr = client.exec_command(command, timeout=300)
        stdin.write(
            _remote_code(
                apply=args.apply,
                verify_retrieval=args.verify_retrieval,
                audit_active=args.audit_active,
                audit_min_id=args.audit_min_id,
                audit_max_id=args.audit_max_id,
            )
        )
        stdin.channel.shutdown_write()
        output = stdout.read().decode("utf-8", "replace").strip()
        error = stderr.read().decode("utf-8", "replace").strip()
        exit_code = stdout.channel.recv_exit_status()
        if output:
            payload = json.loads(output.splitlines()[-1])
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        if error:
            print(error)
        if exit_code != 0:
            raise SystemExit(exit_code)
    finally:
        client.close()


if __name__ == "__main__":
    main()
