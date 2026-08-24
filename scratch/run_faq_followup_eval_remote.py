"""Start or inspect the two-case real regression after FAQ cleanup fixes."""

from __future__ import annotations

import argparse
import json
import shlex

from local_prebuilt_deploy import connect_ecs


RUN_ID = "reply-agent-faqfollowup-real-1128319-20260824T0746Z"
CONTAINER = "ran_ai-reply-agent-faqfollowup-real-1128319"
CASE_IDS = "RA-021,RA-067"
DEPLOYED_SHA = "112831942992"
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
        "eval_import_probe": (
            "cd /root/ran_ai && docker compose -f docker-compose.reply-agent-eval.yml run "
            "--rm --no-deps --entrypoint /app/.venv/bin/python reply-agent-eval "
            "-c \"from app.services.agent.prompts import AGENT_SYSTEM_PROMPT; "
            "from app.services.agent.graph import RanrenAgentGraph; "
            "assert len(AGENT_SYSTEM_PROMPT) == 4276; print('ok')\""
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
    payload: dict[str, object] = {
        "run_id": RUN_ID,
        "container_state": state,
        "status": json.loads(status_text) if status_text else None,
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
                    "response": (item.get("actual") or {}).get("response"),
                    "failure_reasons": item.get("failure_reasons"),
                    "nonpassing_metrics": [
                        metric
                        for metric in item.get("metrics") or []
                        if metric.get("passed") is False
                    ],
                    "usage": item.get("usage"),
                }
                for item in report.get("cases") or []
            ],
        }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("start", "status"))
    args = parser.parse_args()
    client = connect_ecs()
    try:
        if args.action == "start":
            _start(client)
        else:
            _status(client)
    finally:
        client.close()


if __name__ == "__main__":
    main()
