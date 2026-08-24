"""Start or inspect the one-off real regression for the FAQ conflict cleanup."""

from __future__ import annotations

import argparse
import json
import shlex

from local_prebuilt_deploy import connect_ecs


RUN_ID = "reply-agent-faqcleanup-real-72e6dfa-20260824T0716Z"
CONTAINER = "ran_ai-reply-agent-faqcleanup-real-72e6dfa"
CASE_IDS = "RA-005,RA-008,RA-021,RA-067,RA-068,RA-074"
DEPLOYED_SHA = "72e6dfa476c3"
REPORT_DIR = f"/root/ran_ai/diagnostics/reply-agent-evaluations/{RUN_ID}"


def _run(client, command: str, *, timeout: int = 120) -> tuple[int, str, str]:
    _, stdout, stderr = client.exec_command(command, timeout=timeout)
    output = stdout.read().decode("utf-8", "replace").strip()
    error = stderr.read().decode("utf-8", "replace").strip()
    return stdout.channel.recv_exit_status(), output, error


def _start(client) -> None:
    checks = {
        "deployed_version": "cat /root/ran_ai/DEPLOYED_VERSION",
        "backend_health": (
            "docker exec ran_ai-backend-1 python -c "
            '"import urllib.request; print(urllib.request.urlopen('
            "'http://localhost:8000/health').getcode())\""
        ),
        "alembic": "cd /root/ran_ai && docker exec ran_ai-backend-1 uv run alembic current",
        "conflict_gate": (
            "docker exec ran_ai-backend-1 grep -n "
            "'answer_relation == \"conflict\"' /app/src/app/services/knowledge_dedupe.py"
        ),
    }
    evidence: dict[str, str] = {}
    for label, command in checks.items():
        code, output, error = _run(client, command)
        if code != 0:
            raise RuntimeError(f"{label} failed: {error or output}")
        evidence[label] = output
    if DEPLOYED_SHA not in evidence["deployed_version"]:
        raise RuntimeError(f"unexpected deployed version: {evidence['deployed_version']}")
    if evidence["backend_health"] != "200":
        raise RuntimeError(f"backend unhealthy: {evidence['backend_health']}")

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
        "--max-cost-yuan 3"
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
                "max_cost_yuan": 3,
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
    status_path = f"{REPORT_DIR}/status.json"
    report_path = f"{REPORT_DIR}/report.json"
    _, status_text, _ = _run(client, f"cat {shlex.quote(status_path)} 2>/dev/null || true")
    _, report_text, _ = _run(client, f"cat {shlex.quote(report_path)} 2>/dev/null || true")
    payload: dict[str, object] = {
        "run_id": RUN_ID,
        "container_state": state,
        "status": json.loads(status_text) if status_text else None,
    }
    if report_text:
        report = json.loads(report_text)
        payload["report_summary"] = {
            "status": report.get("status"),
            "summary": report.get("summary"),
            "cost_summary": report.get("cost_summary"),
            "errors": report.get("errors"),
            "cases": [
                {
                    "case_id": item.get("case_id"),
                    "title": item.get("title"),
                    "status": item.get("status"),
                    "knowledge_query": (item.get("actual") or {}).get("knowledge_query"),
                    "response": (item.get("actual") or {}).get("response"),
                    "failure_reasons": item.get("failure_reasons"),
                    "nonpassing_metrics": [
                        {
                            "key": metric.get("key"),
                            "score": metric.get("score"),
                            "passed": metric.get("passed"),
                            "enforced": metric.get("enforced"),
                            "reason": metric.get("reason"),
                        }
                        for metric in item.get("metrics") or []
                        if metric.get("passed") is False
                    ],
                }
                for item in report.get("cases") or []
            ],
        }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _evidence(client) -> None:
    source_case_id = "rag-eval-retrieval-report-noon-time"
    program = f'''import json
from pathlib import Path

rows = []
for line in Path({str(REPORT_DIR + "/calls.jsonl")!r}).read_text(encoding="utf-8").splitlines():
    item = json.loads(line)
    if item.get("case_id") == {source_case_id!r} and item.get("component") == "answer":
        rows.append({{
            "stage_key": item.get("stage_key"),
            "request": item.get("request"),
            "response": item.get("response"),
            "usage": item.get("usage"),
            "error_message": item.get("error_message"),
        }})
print(json.dumps(rows, ensure_ascii=False))
'''
    code, output, error = _run(client, f"python3 -c {shlex.quote(program)}")
    if code != 0:
        raise RuntimeError(f"evidence read failed: {error or output}")
    print(json.dumps(json.loads(output), ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("start", "status", "evidence"))
    args = parser.parse_args()
    client = connect_ecs()
    try:
        if args.action == "start":
            _start(client)
        elif args.action == "status":
            _status(client)
        else:
            _evidence(client)
    finally:
        client.close()


if __name__ == "__main__":
    main()
