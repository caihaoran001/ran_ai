from __future__ import annotations

import json
from collections import Counter
from types import SimpleNamespace

import pytest

from app.evaluation.reply_agent_ruler_calibration import (
    load_reply_agent_ruler_calibration_suite,
)
from eval.calibrate_reply_agent_rulers import async_main
from eval.rewrite_harm import assess_rewrite_harm


def test_fixed_reply_agent_ruler_calibration_suite_is_balanced() -> None:
    suite = load_reply_agent_ruler_calibration_suite()

    assert suite.calibration_id == "reply-agent-rulers-zh-v1"
    assert len(suite.task_completion_cases) == 12
    assert Counter(item.phase for item in suite.task_completion_cases) == {
        "selection": 8,
        "validation": 4,
    }
    assert len(suite.rewrite_harm_cases) == 6
    assert {item.error_type for item in suite.task_completion_cases} >= {
        "wrong_fact",
        "missing_fact",
        "wrong_response_mode",
        "unsupported_claim",
    }


def test_rewrite_harm_calibration_cases_match_locked_policy() -> None:
    suite = load_reply_agent_ruler_calibration_suite()

    for case in suite.rewrite_harm_cases:
        result = assess_rewrite_harm(
            original_query=case.original_query,
            rewritten_query=case.rewritten_query,
            raw_hit_count=case.raw_hit_count,
            rewritten_hit_count=case.rewritten_hit_count,
            raw_recall=case.raw_recall,
            rewritten_recall=case.rewritten_recall,
        )
        assert result.applicable is case.expected_applicable, case.case_id
        assert result.harmed is case.expected_harmed, case.case_id
        assert result.signal == case.expected_signal, case.case_id


@pytest.mark.asyncio
async def test_mock_ruler_calibration_selects_threshold_without_model_calls(
    tmp_path,
) -> None:
    args = SimpleNamespace(
        mock=True,
        run_id="reply-agent-ruler-calibration-mock",
        max_cost_yuan=10.0,
        reports_root=tmp_path,
        dataset=None,
    )

    exit_code = await async_main(args)

    report = json.loads(
        (tmp_path / args.run_id / "report.json").read_text(encoding="utf-8")
    )
    assert exit_code == 0
    assert report["status"] == "completed"
    assert report["passed"] is True
    assert report["rewrite_harm"]["passed"] is True
    assert report["task_completion"]["selected_threshold"] == 0.5
    assert report["task_completion"]["completed_items"] == 48
    assert report["task_completion"]["usage"]["total"]["call_count"] == 0
    assert report["task_completion"]["usage"]["total"][
        "known_estimated_cost_yuan"
    ] == 0
