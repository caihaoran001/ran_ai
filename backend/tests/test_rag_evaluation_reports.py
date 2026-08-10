from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.api.routes.rag_evaluations import get_report_store
from app.services.rag_evaluation_reports import RagEvaluationReportStore


def _summary(run_id: str, *, status: str = "completed", minute: int = 0) -> dict:
    timestamp = datetime(2026, 8, 10, 12, minute, tzinfo=timezone.utc).isoformat()
    return {
        "schema_version": 1,
        "run_id": run_id,
        "status": status,
        "started_at": timestamp,
        "updated_at": timestamp,
        "finished_at": timestamp if status == "completed" else None,
        "git_sha": "1234567",
        "dataset_id": "rag-chat-core-v1",
        "dataset_version": 1,
        "model": "mock-model",
        "embedding_model": "mock-embedding",
        "ragas_version": "0.4.3",
        "counts": {
            "total": 1,
            "completed": 1 if status == "completed" else 0,
            "passed": 1 if status == "completed" else 0,
            "failed": 0,
            "skipped": 0,
            "errors": 0,
        },
        "metrics": {
            "retrieval_decision_error_rate": {
                "value": 0.0,
                "numerator": 0,
                "denominator": 1,
            },
            "rewrite_harm_rate": {
                "value": 0.0,
                "numerator": 0,
                "denominator": 1,
            },
            "context_recall": {"value": 0.91, "denominator": 1},
            "context_precision": {"value": 0.82, "denominator": 1},
            "faithfulness": {"value": 0.94, "denominator": 1},
            "answer_relevancy": {"value": 0.89, "denominator": 1},
        },
        "critical_fact_errors": 0,
        "estimated_cost_yuan": 0.0,
        "error": None,
    }


def _report(run_id: str) -> dict:
    return {
        "summary": _summary(run_id),
        "cases": [
            {
                "case_id": "rag-cap-ellipsis",
                "title": "瓶盖用量保持比较方向",
                "tags": ["comparison"],
                "status": "passed",
                "history": [],
                "user_query": "一瓶盖够一大刻度吗？",
                "expected": {
                    "retrieval_decision": "retrieve",
                    "required_facts": ["一瓶盖约1.5小刻度"],
                    "forbidden_claims": ["一瓶盖够一大刻度"],
                },
                "actual": {
                    "retrieval_decision": "retrieve",
                    "decision_source": "rewritten",
                    "knowledge_query": "一瓶盖和一大刻度用量换算",
                    "raw_query_hits": [],
                    "rewritten_query_hits": [],
                    "response": "一瓶盖约1.5小刻度，一大刻度是3小刻度。",
                },
                "scores": {
                    "context_recall": 0.91,
                    "context_precision": 0.82,
                    "faithfulness": 0.94,
                    "answer_relevancy": 0.89,
                },
                "failure_reasons": [],
                "elapsed_ms": 42,
            }
        ],
    }


def _write_json(path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


@pytest.mark.asyncio
async def test_rag_evaluation_api_lists_status_and_returns_report(
    admin_client, tmp_path
):
    app = admin_client._transport.app
    store = RagEvaluationReportStore(tmp_path)
    app.dependency_overrides[get_report_store] = lambda: store
    _write_json(tmp_path / "eval-later" / "report.json", _report("eval-later"))
    _write_json(
        tmp_path / "eval-running" / "status.json",
        _summary("eval-running", status="running", minute=1),
    )

    response = await admin_client.get("/rag-evaluations")
    assert response.status_code == 200
    assert [item["run_id"] for item in response.json()["items"]] == [
        "eval-running",
        "eval-later",
    ]

    detail = await admin_client.get("/rag-evaluations/eval-later")
    assert detail.status_code == 200
    assert detail.json()["cases"][0]["actual"]["response"].startswith("一瓶盖")


@pytest.mark.asyncio
async def test_rag_evaluation_api_rejects_bad_paths_and_invalid_reports(
    admin_client, tmp_path
):
    app = admin_client._transport.app
    store = RagEvaluationReportStore(tmp_path)
    app.dependency_overrides[get_report_store] = lambda: store
    _write_json(tmp_path / "broken" / "report.json", {"unexpected": True})

    missing = await admin_client.get("/rag-evaluations/..%2Fsecrets")
    assert missing.status_code == 404
    broken = await admin_client.get("/rag-evaluations/broken")
    assert broken.status_code == 422

    listing = await admin_client.get("/rag-evaluations")
    assert listing.status_code == 200
    assert listing.json() == {"items": []}
