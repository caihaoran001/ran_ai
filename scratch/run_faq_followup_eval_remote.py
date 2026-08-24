"""Start or inspect the final RA-021 regression after FAQ cleanup fixes."""

from __future__ import annotations

import argparse
import json
import shlex
import textwrap

from local_prebuilt_deploy import connect_ecs


RUN_ID = "reply-agent-ra021-final-real-bfe8988-20260824T0814Z"
CONTAINER = "ran_ai-reply-agent-ra021-final-real-bfe8988"
CASE_IDS = "RA-021"
DEPLOYED_SHA = "ced7603ef85a"
REPORT_DIR = f"/root/ran_ai/diagnostics/reply-agent-evaluations/{RUN_ID}"


def _run(client, command: str, *, timeout: int = 120) -> tuple[int, str, str]:
    _, stdout, stderr = client.exec_command(command, timeout=timeout)
    output = stdout.read().decode("utf-8", "replace").strip()
    error = stderr.read().decode("utf-8", "replace").strip()
    return stdout.channel.recv_exit_status(), output, error


def _preflight(client) -> dict[str, str]:
    checks = {
        "deployed_version": "cat /root/ran_ai/DEPLOYED_VERSION",
        "containers": (
            "docker ps --filter name=ran_ai-backend-1 --filter name=ran_ai-frontend-1 "
            "--format '{{.Names}} {{.Status}}'"
        ),
        "backend_health": (
            "docker exec ran_ai-backend-1 python -c "
            '"import urllib.request; print(urllib.request.urlopen('
            "'http://localhost:8000/health').getcode())\""
        ),
        "frontend_health": (
            "docker exec ran_ai-frontend-1 node -e \"const http=require('http');"
            "http.get('http://localhost:3000',r=>console.log(r.statusCode))\""
        ),
        "alembic": "cd /root/ran_ai && docker exec ran_ai-backend-1 uv run alembic current",
        "empty_reply_retry": (
            "grep -n 'EmptyAgentReply' "
            "/root/ran_ai/backend/src/app/services/agent/graph.py"
        ),
        "product_rule": (
            "grep -n '用户的猜测不覆盖口径' "
            "/root/ran_ai/backend/src/app/services/agent/prompts.py"
        ),
        "rewrite_subject": (
            "grep -n '只能还原对话已出现的产品名' "
            "/root/ran_ai/backend/src/app/services/ai_service/client.py"
        ),
        "retrieval_phrase": (
            "grep -n '边吃边喝' "
            "/root/ran_ai/backend/src/app/services/knowledge_base/keyword_whitelist.json"
        ),
        "dataset_path": (
            "cd /root/ran_ai && docker compose "
            "-f docker-compose.reply-agent-eval.yml config "
            "| grep 'REPLY_AGENT_EVALUATION_DATASET_PATH:.*reply_agent_eval_cases.v3.json'"
        ),
        "eval_import_probe": (
            "cd /root/ran_ai && docker compose -f docker-compose.reply-agent-eval.yml run "
            "--rm --no-deps --entrypoint /app/.venv/bin/python reply-agent-eval "
            "-c \"from app.services.agent.prompts import AGENT_SYSTEM_PROMPT; "
            "from app.evaluation.reply_agent_cases import load_reply_agent_eval_suite; "
            "from app.services.agent.graph import RanrenAgentGraph; "
            "suite=load_reply_agent_eval_suite(); "
            "assert len(AGENT_SYSTEM_PROMPT) == 4276; "
            "assert (suite.suite_id, suite.version) == ('reply-agent-core-v3', 3); "
            "assert suite.cases[20].rag_case.history[0].content.startswith('午B随餐'); "
            "print('ok')\""
        ),
    }
    evidence: dict[str, str] = {}
    for label, command in checks.items():
        code, output, error = _run(client, command, timeout=300)
        if code != 0:
            raise RuntimeError(f"{label} failed: {error or output}")
        evidence[label] = output
    if DEPLOYED_SHA not in evidence["deployed_version"]:
        raise RuntimeError(f"unexpected deployed version: {evidence['deployed_version']}")
    if evidence["backend_health"] != "200":
        raise RuntimeError(f"backend unhealthy: {evidence['backend_health']}")
    if evidence["frontend_health"] not in {"200", "302", "307"}:
        raise RuntimeError(f"frontend unhealthy: {evidence['frontend_health']}")
    if evidence["eval_import_probe"].splitlines()[-1] != "ok":
        raise RuntimeError(f"evaluation import probe failed: {evidence['eval_import_probe']}")
    return evidence


def _start(client) -> None:
    evidence = _preflight(client)
    collision_check = (
        f"if docker inspect {shlex.quote(CONTAINER)} >/dev/null 2>&1 "
        f"|| test -e {shlex.quote(REPORT_DIR)}; then exit 17; fi"
    )
    code, _, error = _run(client, collision_check)
    if code == 17:
        raise RuntimeError("run id or container already exists; refusing duplicate start")
    if code != 0:
        raise RuntimeError(f"collision preflight failed: {error}")

    command = (
        "cd /root/ran_ai && "
        "docker compose -f docker-compose.reply-agent-eval.yml run -d "
        f"--name {shlex.quote(CONTAINER)} "
        f"-e REPLY_AGENT_EVAL_GIT_SHA={shlex.quote(DEPLOYED_SHA)} "
        "--no-deps reply-agent-eval "
        f"--run-id {shlex.quote(RUN_ID)} "
        f"--case-ids {shlex.quote(CASE_IDS)} "
        "--max-cost-yuan 2"
    )
    code, output, error = _run(client, command, timeout=300)
    if code != 0:
        raise RuntimeError(f"evaluation start failed: {error or output}")
    print(
        json.dumps(
            {
                "started": True,
                "run_id": RUN_ID,
                "container": CONTAINER,
                "case_ids": CASE_IDS.split(","),
                "max_cost_yuan": 2,
                "container_id": output.splitlines()[-1] if output else None,
                "deployment": evidence,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _status(client) -> None:
    code, state, state_error = _run(
        client,
        f"docker inspect -f '{{{{.State.Status}}}} {{{{.State.ExitCode}}}}' {shlex.quote(CONTAINER)}",
    )
    if code != 0:
        raise RuntimeError(f"container not found: {state_error or state}")
    _, status_text, _ = _run(
        client,
        f"cat {shlex.quote(REPORT_DIR + '/status.json')} 2>/dev/null || true",
    )
    _, report_text, _ = _run(
        client,
        f"cat {shlex.quote(REPORT_DIR + '/report.json')} 2>/dev/null || true",
    )
    _, calls_count, _ = _run(
        client,
        f"wc -l < {shlex.quote(REPORT_DIR + '/calls.jsonl')} 2>/dev/null || true",
    )
    payload: dict[str, object] = {
        "run_id": RUN_ID,
        "container_state": state,
        "status": json.loads(status_text) if status_text else None,
        "calls_written": int(calls_count) if calls_count.isdigit() else 0,
    }
    if report_text:
        report = json.loads(report_text)
        payload["report"] = {
            "summary": report.get("summary"),
            "cases": [
                {
                    "case_id": item.get("case_id"),
                    "status": item.get("status"),
                    "knowledge_query": (item.get("actual") or {}).get("knowledge_query"),
                    "raw_query_hits": (
                        ((item.get("evidence") or {}).get("rag") or {}).get("actual")
                        or {}
                    ).get("raw_query_hits"),
                    "rewritten_query_hits": (
                        ((item.get("evidence") or {}).get("rag") or {}).get("actual")
                        or {}
                    ).get("rewritten_query_hits"),
                    "response": (item.get("actual") or {}).get("response"),
                    "failure_reasons": item.get("failure_reasons"),
                    "nonpassing_metrics": [
                        metric
                        for metric in item.get("metrics") or []
                        if metric.get("passed") is False
                    ],
                    "usage": (item.get("usage") or {}).get("total"),
                }
                for item in report.get("cases") or []
            ],
        }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _logs(client) -> None:
    code, output, error = _run(
        client,
        f"docker logs --tail 120 {shlex.quote(CONTAINER)}",
    )
    if code != 0:
        raise RuntimeError(f"container logs failed: {error or output}")
    print(output)


def _diagnose(client) -> None:
    program = textwrap.dedent(
        """
        import asyncio
        import json

        from sqlalchemy import or_, select

        from app.db.session import SessionLocal
        from app.models.knowledge_chunk import KnowledgeChunk
        from app.models.knowledge_faq import KnowledgeFAQ
        from app.services.knowledge_index import retrieve_indexed_knowledge_result

        QUERIES = [
            "这个如果我想先喝了产品再接着吃正餐呢",
            "用户询问：产品是否可以先喝掉产品、再接着吃正餐？与之前建议的“随正餐边吃边喝”相比，这种服用顺序是否可行、是否有影响？需要确认产品是否允许空腹或饭前先喝，再正常进食正餐。",
            "产品服用顺序：先喝产品再吃正餐是否可以？之前建议随正餐边吃边喝，想确认先喝产品再吃正餐是否可行。",
            "产品先喝再吃正餐是否算空腹，还是必须随餐边吃边喝？",
            "早C或午B先喝再吃正餐是否算空腹，还是必须随餐边吃边喝？",
        ]

        async def run():
            async with SessionLocal() as session:
                retrieval = []
                for query in QUERIES:
                    result = await retrieve_indexed_knowledge_result(
                        session,
                        query,
                        exclude_source_types={"case"},
                    )
                    retrieval.append({
                        "query": query,
                        "hits": [
                            {
                                "rank": hit.rank,
                                "chunk_id": hit.chunk_id,
                                "chunk_uid": hit.chunk_uid,
                                "title": hit.title,
                                "source_type": hit.source_type,
                                "content_excerpt": hit.content[:1200],
                            }
                            for hit in result.hits
                        ],
                    })
                chunks = list(
                    await session.scalars(
                        select(KnowledgeChunk)
                        .where(
                            KnowledgeChunk.status == "active",
                            or_(
                                KnowledgeChunk.content.ilike("%先喝早C%"),
                                KnowledgeChunk.content.ilike("%蛋白粉%"),
                                KnowledgeChunk.content.ilike("%先喝产品%"),
                                KnowledgeChunk.content.ilike("%边吃边喝%"),
                                KnowledgeChunk.content.ilike("%随餐饮用%"),
                            ),
                        )
                        .order_by(KnowledgeChunk.id)
                    )
                )
                faq_ids = [
                    int((chunk.metadata_json or {}).get("faq_id"))
                    for chunk in chunks
                    if str((chunk.metadata_json or {}).get("faq_id") or "").isdigit()
                ]
                faqs = {
                    faq.id: faq
                    for faq in await session.scalars(
                        select(KnowledgeFAQ).where(KnowledgeFAQ.id.in_(faq_ids))
                    )
                } if faq_ids else {}
                matching = []
                for chunk in chunks:
                    faq_id = (chunk.metadata_json or {}).get("faq_id")
                    faq = faqs.get(int(faq_id)) if str(faq_id or "").isdigit() else None
                    matching.append({
                        "chunk_id": chunk.id,
                        "chunk_uid": chunk.chunk_uid,
                        "source_type": chunk.source_type,
                        "source_uri": chunk.source_uri,
                        "title": chunk.title,
                        "content_excerpt": chunk.content[:1200],
                        "faq": {
                            "id": faq.id,
                            "status": faq.status,
                            "source_type": faq.source_type,
                            "source_id": faq.source_id,
                            "question": faq.question,
                            "answer": faq.answer,
                        } if faq else None,
                    })
                print(json.dumps({"retrieval": retrieval, "matching_active_chunks": matching}, ensure_ascii=False))

        asyncio.run(run())
        """
    )
    command = (
        "docker exec ran_ai-backend-1 uv run python -c "
        + shlex.quote(program)
    )
    code, output, error = _run(client, command, timeout=300)
    if code != 0:
        raise RuntimeError(f"retrieval diagnosis failed: {error or output}")
    print(json.dumps(json.loads(output), ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=("verify", "start", "status", "logs", "diagnose"),
    )
    args = parser.parse_args()
    client = connect_ecs()
    try:
        if args.action == "verify":
            print(json.dumps(_preflight(client), ensure_ascii=False, indent=2))
        elif args.action == "start":
            _start(client)
        elif args.action == "status":
            _status(client)
        elif args.action == "logs":
            _logs(client)
        else:
            _diagnose(client)
    finally:
        client.close()


if __name__ == "__main__":
    main()
