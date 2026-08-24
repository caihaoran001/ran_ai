from __future__ import annotations

import json
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.schemas.rag_evaluation import (
    RagEvaluationCaseResult,
    RagEvaluationCaseUsage,
    RagEvaluationModelCall,
    RagEvaluationUsageSummary,
    RagEvaluationUsageTotals,
)
from app.services.agent_observability import _response_model, _usage_counts
from app.services.ai_service.client import _estimated_cost_yuan, _pricing_for_usage


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _jsonable(value: Any) -> Any:
    """Serialize SDK requests/responses without dropping judge evidence."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _jsonable(model_dump(mode="json"))
        except TypeError:
            return _jsonable(model_dump())
    attributes = getattr(value, "__dict__", None)
    if isinstance(attributes, dict):
        return {
            str(key): _jsonable(item)
            for key, item in attributes.items()
            if not str(key).startswith("_")
        }
    return str(value)


def _openai_usage(
    response: Any,
    *,
    embedding: bool = False,
) -> tuple[int | None, int | None, int | None, int | None]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None, None, None, None
    prompt = _optional_int(
        getattr(usage, "prompt_tokens", getattr(usage, "input_tokens", None))
    )
    total = _optional_int(getattr(usage, "total_tokens", None))
    if embedding and prompt is None:
        prompt = total
    completion = (
        0
        if embedding and prompt is not None
        else _optional_int(
            getattr(
                usage,
                "completion_tokens",
                getattr(usage, "output_tokens", None),
            )
        )
    )
    if total is None and (prompt is not None or completion is not None):
        total = int(prompt or 0) + int(completion or 0)
    cache_hit = _optional_int(getattr(usage, "prompt_cache_hit_tokens", None))
    if cache_hit is None:
        details = getattr(usage, "prompt_tokens_details", None) or getattr(
            usage, "input_tokens_details", None
        )
        cache_hit = _optional_int(getattr(details, "cached_tokens", None))
    return prompt, completion, total, cache_hit


def _totals(calls: list[RagEvaluationModelCall]) -> RagEvaluationUsageTotals:
    costed = [item for item in calls if item.estimated_cost_yuan is not None]
    known_cost = round(
        sum(float(item.estimated_cost_yuan or 0) for item in costed),
        8,
    )
    complete = len(costed) == len(calls) and all(item.attempts == 1 for item in calls)
    return RagEvaluationUsageTotals(
        call_count=len(calls),
        request_count=sum(item.attempts for item in calls),
        successful_calls=sum(item.success for item in calls),
        failed_calls=sum(not item.success for item in calls),
        usage_reported_calls=sum(
            item.prompt_tokens is not None
            and (item.component == "embedding" or item.completion_tokens is not None)
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
    calls: list[RagEvaluationModelCall],
    *,
    tracking_available: bool = True,
) -> RagEvaluationCaseUsage:
    by_component = {
        component: [item for item in calls if item.component == component]
        for component in ("intent", "answer", "ragas_judge", "judge", "embedding")
    }
    return RagEvaluationCaseUsage(
        tracking_available=tracking_available,
        intent=_totals(by_component["intent"]),
        answer=_totals(by_component["answer"]),
        ragas_judge=_totals(by_component["ragas_judge"]),
        judge=_totals(by_component["judge"]),
        embedding=_totals(by_component["embedding"]),
        total=_totals(calls),
        calls=calls,
    )


def summarize_case_usage(
    cases: list[RagEvaluationCaseResult],
    *,
    tracking_available: bool,
) -> RagEvaluationUsageSummary:
    calls = [call for case in cases for call in case.usage.calls]
    usage = usage_from_calls(calls, tracking_available=tracking_available)
    return RagEvaluationUsageSummary(
        tracking_available=usage.tracking_available,
        intent=usage.intent,
        answer=usage.answer,
        ragas_judge=usage.ragas_judge,
        judge=usage.judge,
        embedding=usage.embedding,
        total=usage.total,
    )


class RagEvaluationCostLedger:
    """In-memory API-cost ledger for the sequential, isolated RAG evaluator."""

    def __init__(
        self,
        settings: Settings,
        *,
        call_log_path: Path | None = None,
    ):
        self._settings = settings
        self._calls: dict[str, list[RagEvaluationModelCall]] = defaultdict(list)
        self._active_case_id: str | None = None
        self._stage_key: str | None = None
        self._call_log_path = call_log_path
        self._call_log_lock = threading.Lock()
        if self._call_log_path is not None:
            self._call_log_path.parent.mkdir(parents=True, exist_ok=True)
            self._call_log_path.write_text("", encoding="utf-8")

    def begin_case(self, case_id: str) -> None:
        if self._active_case_id is not None:
            raise RuntimeError(
                f"RAG 评测费用账本仍有未结束题目: {self._active_case_id}"
            )
        self._active_case_id = case_id

    def end_case(self, case_id: str) -> None:
        if self._active_case_id != case_id:
            raise RuntimeError(
                f"RAG 评测费用账本题目不匹配: active={self._active_case_id}, end={case_id}"
            )
        self._active_case_id = None
        self._stage_key = None

    @contextmanager
    def stage(self, stage_key: str) -> Iterator[None]:
        previous = self._stage_key
        self._stage_key = stage_key
        try:
            yield
        finally:
            self._stage_key = previous

    def case_usage(self, case_id: str) -> RagEvaluationCaseUsage:
        return usage_from_calls(list(self._calls[case_id]))

    def _llm_cost(
        self,
        *,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        model: str,
        scenario: str,
        prompt_cache_hit_tokens: int | None,
    ) -> float | None:
        if prompt_tokens is None or completion_tokens is None:
            return None
        miss_price, _, completion_price = _pricing_for_usage(
            prompt_tokens=prompt_tokens,
            settings=self._settings,
            scenario=scenario,
            model=model,
        )
        if (prompt_tokens > 0 and miss_price <= 0) or (
            completion_tokens > 0 and completion_price <= 0
        ):
            return None
        return _estimated_cost_yuan(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            settings=self._settings,
            scenario=scenario,
            model=model,
            prompt_cache_hit_tokens=prompt_cache_hit_tokens,
        )

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
        attempts: int,
        success: bool,
        error_type: str | None,
        scenario: str,
    ) -> RagEvaluationModelCall:
        if self._active_case_id is None:
            raise RuntimeError("RAG 评测费用账本没有活动题目")
        if component == "embedding":
            rate = float(self._settings.embedding_cost_per_1m_tokens or 0)
            cost = (
                round((prompt_tokens * rate) / 1_000_000, 8)
                if prompt_tokens is not None and rate > 0
                else None
            )
        else:
            cost = self._llm_cost(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                model=model,
                scenario=scenario,
                prompt_cache_hit_tokens=prompt_cache_hit_tokens,
            )
        calls = self._calls[self._active_case_id]
        call = RagEvaluationModelCall(
            component=component,
            stage_key=self._stage_key,
            call_index=len(calls) + 1,
            attempts=max(1, int(attempts or 1)),
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
        call: RagEvaluationModelCall,
        request_payload: Any,
        response_payload: Any,
        error_message: str | None,
    ) -> None:
        if self._call_log_path is None:
            return
        payload = {
            "schema_version": 1,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "case_id": self._active_case_id,
            "component": call.component,
            "stage_key": call.stage_key,
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

    async def record_intent_model_usage(self, **payload: Any) -> None:
        usage = payload.get("usage")
        response = type("UsageResponse", (), {"usage": usage})()
        prompt, completion, total, cache_hit = _openai_usage(response)
        call = self._record(
            component="intent",
            model=str(payload.get("model") or "unknown"),
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=total,
            prompt_cache_hit_tokens=cache_hit,
            elapsed_ms=int(payload.get("elapsed_ms") or 0),
            attempts=int(payload.get("attempts") or 1),
            success=bool(payload.get("success")),
            error_type=str(payload.get("error_type") or "") or None,
            scenario=str(payload.get("scenario") or "message_insight"),
        )
        self._append_call_evidence(
            call=call,
            request_payload=payload.get("request_payload"),
            response_payload=payload.get("response_payload"),
            error_message=str(payload.get("error_type") or "") or None,
        )

    async def record_answer_model_usage(self, **payload: Any) -> None:
        response = payload.get("response")
        prompt, completion, total, cache_hit = _usage_counts(response)
        call = self._record(
            component="answer",
            model=_response_model(response) or str(payload.get("model") or "unknown"),
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=total,
            prompt_cache_hit_tokens=cache_hit,
            elapsed_ms=int(payload.get("elapsed_ms") or 0),
            attempts=1,
            success=bool(payload.get("success")),
            error_type=str(payload.get("error_type") or "") or None,
            scenario=str(payload.get("scenario") or "reply_agent"),
        )
        self._append_call_evidence(
            call=call,
            request_payload={"messages": payload.get("request_messages") or []},
            response_payload=response,
            error_message=str(payload.get("error_type") or "") or None,
        )

    def record_openai_response(
        self,
        *,
        component: str,
        configured_model: str,
        response: Any,
        elapsed_ms: int,
        success: bool,
        error_type: str | None,
        request_payload: Any = None,
        response_payload: Any = None,
        error_message: str | None = None,
    ) -> None:
        embedding = component == "embedding"
        prompt, completion, total, cache_hit = _openai_usage(
            response,
            embedding=embedding,
        )
        call = self._record(
            component=component,
            model=str(
                getattr(response, "model", None) or configured_model or "unknown"
            ),
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=total,
            prompt_cache_hit_tokens=cache_hit,
            elapsed_ms=elapsed_ms,
            attempts=1,
            success=success,
            error_type=error_type,
            scenario=(
                "embedding"
                if component == "embedding"
                else "ragas_judge"
                if component == "ragas_judge"
                else "judge"
            ),
        )
        self._append_call_evidence(
            call=call,
            request_payload=request_payload,
            response_payload=(
                response if response_payload is None else response_payload
            ),
            error_message=error_message,
        )


def instrument_openai_client(
    client: Any,
    ledger: RagEvaluationCostLedger,
    *,
    chat_component: str | None = None,
    embedding_component: str | None = None,
) -> Any:
    async def _wrap_create(
        original: Callable[..., Any], component: str, *args: Any, **kwargs: Any
    ) -> Any:
        started = time.monotonic()
        response = None
        try:
            response = await original(*args, **kwargs)
        except Exception as exc:
            ledger.record_openai_response(
                component=component,
                configured_model=str(kwargs.get("model") or "unknown"),
                response=None,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                success=False,
                error_type=type(exc).__name__,
                request_payload=kwargs,
                error_message=str(exc)[:2000],
            )
            raise
        ledger.record_openai_response(
            component=component,
            configured_model=str(kwargs.get("model") or "unknown"),
            response=response,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            success=True,
            error_type=None,
            request_payload=kwargs,
            response_payload=response,
        )
        return response

    if chat_component:
        original_chat_create = client.chat.completions.create

        async def _chat_create(*args: Any, **kwargs: Any) -> Any:
            return await _wrap_create(
                original_chat_create, chat_component, *args, **kwargs
            )

        client.chat.completions.create = _chat_create
        responses = getattr(client, "responses", None)
        if responses is not None and hasattr(responses, "create"):
            original_responses_create = responses.create

            async def _responses_create(*args: Any, **kwargs: Any) -> Any:
                return await _wrap_create(
                    original_responses_create, chat_component, *args, **kwargs
                )

            responses.create = _responses_create
    if embedding_component:
        original_embedding_create = client.embeddings.create

        async def _embedding_create(*args: Any, **kwargs: Any) -> Any:
            return await _wrap_create(
                original_embedding_create,
                embedding_component,
                *args,
                **kwargs,
            )

        client.embeddings.create = _embedding_create
    return client


def install_rag_usage_collectors(
    ledger: RagEvaluationCostLedger,
) -> Callable[[], None]:
    import app.services.agent.graph as graph_module
    import app.services.embedding_service as embedding_module
    from app.services.ai_service.client import AIService

    original_intent = AIService._record_usage
    original_answer = graph_module.record_model_usage
    original_openai = embedding_module.AsyncOpenAI

    AIService._record_usage = ledger.record_intent_model_usage  # type: ignore[method-assign]
    graph_module.record_model_usage = ledger.record_answer_model_usage

    def _instrumented_openai(*args: Any, **kwargs: Any) -> Any:
        return instrument_openai_client(
            original_openai(*args, **kwargs),
            ledger,
            embedding_component="embedding",
        )

    embedding_module.AsyncOpenAI = _instrumented_openai  # type: ignore[assignment,misc]

    def restore() -> None:
        AIService._record_usage = original_intent  # type: ignore[method-assign]
        graph_module.record_model_usage = original_answer
        embedding_module.AsyncOpenAI = original_openai

    return restore
