from __future__ import annotations

import json

import pytest

from app.api.routes.archivist_evaluations import get_report_store
from app.services.archivist_evaluation_reports import ArchivistEvaluationReportStore


def _summary(run_id: str, *, minute: int = 0, **extra) -> dict:
    summary = {
        "schema_version": 1,
        "run_id": run_id,
        "label": f"批次 {run_id}",
        "started_at": f"2026-09-16T17:55:{minute:02d}+08:00",
        "mode": "real",
        "model": "deepseek-flash",
        "suite": "dedup12",
        "groups": 13,
        "repeats": 3,
        "group_runs": 39,
        "passed": 38,
        "failed": 1,
        "flagged": 0,
        "batches": 87,
        "total_tokens": 787129,
        "total_cost_yuan": 1.1201,
    }
    summary.update(extra)
    return summary


def _report(run_id: str, *, minute: int = 0, summary_extra: dict | None = None) -> dict:
    return {
        "summary": _summary(run_id, minute=minute, **(summary_extra or {})),
        "cases": [
            {
                "group_code": "G09",
                "title": "状态恢复＋昨天回补",
                "expectation": "胃不适进成效账本（worsened）；明确归属昨天的产品补记到昨天",
                "batches": [
                    {
                        "label": "B1 2026-09-14",
                        "messages": [
                            {
                                "sender": "user",
                                "content": "这两天胃一直不舒服，吃完就胀，晚A都没敢喝",
                                "time": "2026-09-14 22:00",
                            }
                        ],
                    },
                    {
                        "label": "B2 2026-09-16",
                        "messages": [
                            {
                                "sender": "user",
                                "content": "胃不胀了，昨天晚A正常喝了，谢谢老师",
                                "time": "2026-09-16 09:00",
                            }
                        ],
                    },
                ],
                "repeats": [
                    {
                        "repeat_index": 0,
                        "verdict": "FAIL",
                        "failed_assertions": [
                            "B1: 不适 outcome worsened MUST_ADD 缺失"
                        ],
                        "flags": [],
                        "batch_results": [
                            {
                                "batch_index": 0,
                                "label": "B1 2026-09-14",
                                "watermark_from": 0,
                                "watermark_to": 101,
                                "emitted": [
                                    {
                                        "name": "report_state_fact",
                                        "args": {"state_type": "discomfort"},
                                    }
                                ],
                                "kept": {"outcome": 0},
                                "dropped": [],
                                "usage": {
                                    "model": "deepseek-flash",
                                    "total_tokens": 8680,
                                    "estimated_cost_yuan": 0.011,
                                },
                                "db_changes": {
                                    "checkins_added": [
                                        {
                                            "date": "2026-09-14",
                                            "parsed": {
                                                "product_schedule": {"night_a": "未喝"}
                                            },
                                        }
                                    ],
                                    "checkins_updated": [],
                                    "outcomes_added": [],
                                    "state_changed": True,
                                    "memory_changed": True,
                                    "drafts_added": [],
                                },
                            }
                        ],
                    }
                ],
            }
        ],
    }


def _write_json(path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


@pytest.mark.asyncio
async def test_archivist_evaluation_api_lists_and_returns_report(
    admin_client, tmp_path
):
    app = admin_client._transport.app
    store = ArchivistEvaluationReportStore(tmp_path)
    app.dependency_overrides[get_report_store] = lambda: store
    _write_json(tmp_path / "eval-later" / "report.json", _report("eval-later"))
    _write_json(
        tmp_path / "eval-earlier" / "report.json",
        _report("eval-earlier", minute=1),
    )

    response = await admin_client.get("/archivist-evaluations")
    assert response.status_code == 200
    assert [item["run_id"] for item in response.json()["items"]] == [
        "eval-earlier",
        "eval-later",
    ]
    assert response.json()["items"][0]["passed"] == 38

    detail = await admin_client.get("/archivist-evaluations/eval-later")
    assert detail.status_code == 200
    body = detail.json()
    assert body["summary"]["mode"] == "real"
    assert body["summary"]["label"] == "批次 eval-later"
    case = body["cases"][0]
    assert case["group_code"] == "G09"
    assert case["batches"][0]["messages"][0]["content"].startswith("这两天胃")
    repeat = case["repeats"][0]
    assert repeat["verdict"] == "FAIL"
    assert repeat["failed_assertions"] == ["B1: 不适 outcome worsened MUST_ADD 缺失"]
    batch = repeat["batch_results"][0]
    assert batch["emitted"][0]["name"] == "report_state_fact"
    assert batch["db_changes"]["checkins_added"][0]["date"] == "2026-09-14"
    assert batch["usage"]["total_tokens"] == 8680


@pytest.mark.asyncio
async def test_archivist_evaluation_api_rejects_bad_paths_and_invalid_reports(
    admin_client, tmp_path
):
    app = admin_client._transport.app
    store = ArchivistEvaluationReportStore(tmp_path)
    app.dependency_overrides[get_report_store] = lambda: store
    _write_json(tmp_path / "broken" / "report.json", {"unexpected": True})

    missing = await admin_client.get("/archivist-evaluations/..%2Fsecrets")
    assert missing.status_code == 404
    broken = await admin_client.get("/archivist-evaluations/broken")
    assert broken.status_code == 422

    listing = await admin_client.get("/archivist-evaluations")
    assert listing.status_code == 200
    assert listing.json() == {"items": []}


@pytest.mark.asyncio
async def test_archivist_evaluation_extra_fields_are_tolerated(
    admin_client, tmp_path
):
    app = admin_client._transport.app
    store = ArchivistEvaluationReportStore(tmp_path)
    app.dependency_overrides[get_report_store] = lambda: store
    payload = _report("eval-extra")
    payload["summary"]["future_field"] = {"note": "评测脚本后续新增"}
    case = payload["cases"][0]
    case["future_case_field"] = [1, 2, 3]
    case["repeats"][0]["batch_results"][0]["future_batch_field"] = "ok"
    _write_json(tmp_path / "eval-extra" / "report.json", payload)

    detail = await admin_client.get("/archivist-evaluations/eval-extra")
    assert detail.status_code == 200
    assert detail.json()["cases"][0]["group_code"] == "G09"

    listing = await admin_client.get("/archivist-evaluations")
    assert listing.status_code == 200
    assert [item["run_id"] for item in listing.json()["items"]] == ["eval-extra"]
