from __future__ import annotations

import json
import threading
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.core.config import Settings
from app.schemas.agent_evaluation import (
    AgentEvaluationCaseResult,
    AgentEvaluationCaseUsage,
    AgentEvaluationModelCall,
    AgentEvaluationUsageSummary,
    AgentEvaluationUsageTotals,
)
from app.services.ai_service.client import _estimated_cost_yuan
from app.services.agent_observability import _response_model, _usage_counts

if __package__:
    from .rag_costs import _jsonable
else:
    from rag_costs import _jsonable


CaseKey = tuple[str, int]


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _openai_usage(response: Any) -> tuple[int | None, int | None, int | None, int | None]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None, None, None, None
    prompt = _optional_int(
        getattr(usage, "prompt_tokens", getattr(usage, "input_tokens", None))
    )
    completion = _optional_int(
        getattr(usage, "completion_tokens", getattr(usage, "output_tokens", None))
    )
    total = _optional_int(getattr(usage, "total_tokens", None))
    if total is None and (prompt is not None or completion is not None):
        total = int(prompt or 0) + int(completion or 0)
    cache_hit = _optional_int(getattr(usage, "prompt_cache_hit_tokens", None))
    if cache_hit is None:
        details = getattr(usage, "prompt_tokens_details", None) or getattr(
            usage, "input_tokens_details", None
        )
        cache_hit = _optional_int(getattr(details, "cached_tokens", None))
    return prompt, completion, total, cache_hit


def _totals(calls: list[AgentEvaluationModelCall]) -> AgentEvaluationUsageTotals:
    costed = [item for item in calls if item.estimated_cost_yuan is not None]
    known_cost = round(
        sum(float(item.estimated_cost_yuan or 0) for item in costed),
        8,
    )
    complete = len(costed) == len(calls)
    return AgentEvaluationUsageTotals(
        call_count=len(calls),
        successful_calls=sum(item.success for item in calls),
        failed_calls=sum(not item.success for item in calls),
        usage_reported_calls=sum(
            item.prompt_tokens is not None and item.completion_tokens is not None
            for item in calls
        ),
        costed_calls=len(costed),
        prompt_tokens=sum(item.prompt_tokens or 0 for item in calls),
        completion_tokens=sum(item.completion_tokens or 0 for item in calls),
        total_tokens=sum(item.total_tokens or 0 for item in calls),
        prompt_cache_hit_tokens=sum(
            item.prompt_cache_hit_tokens or 0 for item in calls
        ),
        known_estimated_cost_yuan=known_cost,
        estimated_cost_yuan=known_cost if complete else None,
        complete=complete,
    )


def usage_from_calls(
    calls: list[AgentEvaluationModelCall],
    *,
    tracking_available: bool = True,
) -> AgentEvaluationCaseUsage:
    agent_calls = [item for item in calls if item.component == "agent"]
    judge_calls = [item for item in calls if item.component == "judge"]
    return AgentEvaluationCaseUsage(
        tracking_available=tracking_available,
        agent=_totals(agent_calls),
        judge=_totals(judge_calls),
        total=_totals(calls),
        calls=calls,
    )


def summarize_case_usage(
    cases: list[AgentEvaluationCaseResult],
    *,
    tracking_available: bool,
) -> AgentEvaluationUsageSummary:
    calls = [call for case in cases for call in case.usage.calls]
    usage = usage_from_calls(calls, tracking_available=tracking_available)
    return AgentEvaluationUsageSummary(
        tracking_available=usage.tracking_available,
        agent=usage.agent,
        judge=usage.judge,
        total=usage.total,
    )


class AgentEvaluationCostLedger:
    """In-memory API ledger for the sequential, isolated Agent evaluator."""

    def __init__(
        self,
        settings: Settings,
        *,
        call_log_path: Path | None = None,
        reset_call_log: bool = True,
    ):
        self._settings = settings
        self._calls: dict[CaseKey, list[AgentEvaluationModelCall]] = defaultdict(list)
        self._active_key: CaseKey | None = None
        self._call_log_path = call_log_path
        self._call_log_lock = threading.Lock()
        if self._call_log_path is not None:
            self._call_log_path.parent.mkdir(parents=True, exist_ok=True)
            if reset_call_log or not self._call_log_path.exists():
                self._call_log_path.write_text("", encoding="utf-8")

    def begin_case(self, case_id: str, round_index: int) -> None:
        if self._active_key is not None:
            raise RuntimeError(f"评测成本账本仍有未结束题目: {self._active_key}")
        self._active_key = (case_id, round_index)

    def end_case(self, case_id: str, round_index: int) -> None:
        expected = (case_id, round_index)
        if self._active_key != expected:
            raise RuntimeError(
                f"评测成本账本题目不匹配: active={self._active_key}, end={expected}"
            )
        self._active_key = None

    def case_usage(self, case_id: str, round_index: int) -> AgentEvaluationCaseUsage:
        return usage_from_calls(list(self._calls[(case_id, round_index)]))

    def _record(
        self,
        *,
        component: str,
        model: str,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        total_tokens: int | None,
        prompt_cache_hit_tokens: int | None,
        elapsed_ms: int,
        success: bool,
        error_type: str | None,
        metric_key: str | None,
        scenario: str,
    ) -> AgentEvaluationModelCall:
        if self._active_key is None:
            raise RuntimeError("评测成本账本没有活动题目")
        cost = None
        if prompt_tokens is not None and completion_tokens is not None:
            cost = _estimated_cost_yuan(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                settings=self._settings,
                scenario=scenario,
                model=model,
                prompt_cache_hit_tokens=prompt_cache_hit_tokens,
            )
        calls = self._calls[self._active_key]
        call = AgentEvaluationModelCall(
            component=component,
            metric_key=metric_key,
            call_index=len(calls) + 1,
            model=(model or "unknown")[:120],
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            prompt_cache_hit_tokens=prompt_cache_hit_tokens,
            estimated_cost_yuan=cost,
            elapsed_ms=max(0, int(elapsed_ms)),
            success=success,
            error_type=(error_type or "")[:120] or None,
        )
        calls.append(call)
        return call

    def _append_call_evidence(
        self,
        *,
        call: AgentEvaluationModelCall,
        request_payload: Any,
        response_payload: Any,
        error_message: str | None,
    ) -> None:
        if self._call_log_path is None:
            return
        case_id, round_index = self._active_key or (None, None)
        payload = {
            "schema_version": 1,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "case_id": case_id,
            "round_index": round_index,
            "component": call.component,
            "stage_key": call.metric_key,
            "call_index": call.call_index,
            "request": _jsonable(request_payload),
            "response": _jsonable(response_payload),
            "usage": call.model_dump(mode="json"),
            "error_message": error_message,
        }
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._call_log_lock:
            with self._call_log_path.open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")

    async def record_agent_model_usage(self, **payload: Any) -> None:
        response = payload.get("response")
        prompt, completion, total, cache_hit = _usage_counts(response)
        actual_model = _response_model(response) or str(payload.get("model") or "unknown")
        call = self._record(
            component="agent",
            model=actual_model,
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=total,
            prompt_cache_hit_tokens=cache_hit,
            elapsed_ms=int(payload.get("elapsed_ms") or 0),
            success=bool(payload.get("success")),
            error_type=(str(payload["error_type"]) if payload.get("error_type") else None),
            metric_key=None,
            scenario=str(payload.get("scenario") or "reply_agent"),
        )
        self._append_call_evidence(
            call=call,
            request_payload=payload.get("request_messages"),
            response_payload=response,
            error_message=(
                str(payload["error_type"]) if payload.get("error_type") else None
            ),
        )

    def record_judge_model_usage(
        self,
        *,
        model: str,
        response: Any,
        elapsed_ms: int,
        success: bool,
        error_type: str | None,
        metric_key: str | None,
        request_payload: Any = None,
    ) -> None:
        prompt, completion, total, cache_hit = _openai_usage(response)
        actual_model = str(getattr(response, "model", None) or model or "unknown")
        call = self._record(
            component="judge",
            model=actual_model,
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=total,
            prompt_cache_hit_tokens=cache_hit,
            elapsed_ms=elapsed_ms,
            success=success,
            error_type=error_type,
            metric_key=metric_key,
            scenario="agent_eval_judge",
        )
        self._append_call_evidence(
            call=call,
            request_payload=request_payload,
            response_payload=response,
            error_message=error_type,
        )


def install_agent_usage_collector(
    ledger: AgentEvaluationCostLedger,
) -> Callable[[], None]:
    import app.services.agent.graph as graph_module

    original = graph_module.record_model_usage
    graph_module.record_model_usage = ledger.record_agent_model_usage

    def restore() -> None:
        graph_module.record_model_usage = original

    return restore
