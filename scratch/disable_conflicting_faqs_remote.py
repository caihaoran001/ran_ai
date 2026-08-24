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
    162: 173,
    186: 208,
    252: 297,
    263: 333,
    275: 338,
    286: 324,
    303: 370,
    306: 369,
    307: 365,
}


def _remote_code(*, apply: bool) -> str:
    return textwrap.dedent(
        f"""
        import asyncio
        import json

        from sqlalchemy import select

        from app.db.session import SessionLocal
        from app.models.knowledge_chunk import KnowledgeChunk
        from app.models.knowledge_faq import KnowledgeFAQ
        from app.services.knowledge_faq_service import set_faq_status

        TARGETS = {TARGETS!r}
        APPLY = {apply!r}


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

                print(
                    json.dumps(
                        {{
                            "mode": mode,
                            "target_count": len(TARGETS),
                            "changed": changed,
                            "faq_statuses": final_faq,
                            "chunk_statuses": final_chunk,
                            "rows": rows,
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
    args = parser.parse_args()

    client = connect_ecs()
    try:
        command = "docker exec -i ran_ai-backend-1 uv run python -"
        stdin, stdout, stderr = client.exec_command(command, timeout=300)
        stdin.write(_remote_code(apply=args.apply))
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
