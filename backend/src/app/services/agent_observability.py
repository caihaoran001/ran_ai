from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings, get_settings
from app.db.session import SessionLocal
from app.models.agent_run import AgentRun
from app.models.agent_tool_call import AgentToolCall
from app.models.ai_usage import AIUsageLog
from app.services.ai_service.client import _estimated_cost_yuan

logger = logging.getLogger(__name__)

RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"
INTERRUPTED = "interrupted"
INTERRUPTED_OUTCOME = "process_interrupted"
# 超过这个时长还停在 running 的运行行，判定为进程被杀（部署/重启）后无人收尾。
# 取 30min 是因为最长的单次运行（入营文档生成、档案官大批量）也远低于它。
INTERRUPTED_STALE_AFTER_SECONDS = 30 * 60
_INTERRUPTED_SWEEP_LIMIT = 200
_WRITE_TIMEOUT_SECONDS = 2.0

# 产品名探针。**只用于事后统计**：当意图识别判定本轮不需要检索知识库时，记录用户消息里出现的产品名，
# 好让运营事后一条 SQL 就能捞出"提到产品却没查库"的可疑轮次（例如打卡时顺口一句"今天午B忘了喝"
# 被判成纯数据上报）。绝不参与风险判断、回复内容或是否检索的决策——那些一律由模型结构化输出决定，
# 所以这里不违反"杜绝关键词硬编码规则"。
_PRODUCT_TERM_PROBES = ("早C", "午B", "晚A", "卡点钥匙", "MCT", "加油包")


def detect_product_terms(text: str | None) -> list[str]:
    """列出文本里命中的产品名，仅供观测统计使用。"""
    content = text or ""
    if not content:
        return []
    return [term for term in _PRODUCT_TERM_PROBES if term in content]


def _diagnostic_context() -> tuple[bool, str | None]:
    run_id = (os.getenv("RAN_AI_DIAGNOSTIC_RUN_ID") or "").strip() or None
    return bool(run_id), run_id


async def ensure_agent_run(
    *,
    run_key: str,
    agent_type: str,
    run_kind: str,
    service_case_id: int | None,
    trigger_message_id: int | None = None,
    trigger_reason: str | None = None,
    input_count: int = 0,
    metadata: dict[str, Any] | None = None,
) -> int | None:
    """幂等取得运行行；观测写入失败只告警，不影响回复/归档业务。"""
    is_diagnostic, diagnostic_run_id = _diagnostic_context()
    normalized_run_key = run_key[:180]
    try:
        async with asyncio.timeout(_WRITE_TIMEOUT_SECONDS), SessionLocal() as session:
            existing = await session.scalar(
                select(AgentRun).where(AgentRun.run_key == normalized_run_key)
            )
            if existing is not None:
                return existing.id
            row = AgentRun(
                run_key=normalized_run_key,
                agent_type=agent_type[:32],
                run_kind=run_kind[:40],
                service_case_id=None if is_diagnostic else service_case_id,
                trigger_message_id=(
                    None if is_diagnostic else trigger_message_id
                ),
                status=RUNNING,
                trigger_reason=(trigger_reason or "")[:80] or None,
                input_count=max(0, int(input_count or 0)),
                metadata_json=metadata or None,
                is_diagnostic=is_diagnostic,
                diagnostic_run_id=diagnostic_run_id,
                started_at=datetime.now(timezone.utc),
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row.id
    except IntegrityError:
        try:
            async with asyncio.timeout(_WRITE_TIMEOUT_SECONDS), SessionLocal() as session:
                row_id = await session.scalar(
                    select(AgentRun.id).where(
                        AgentRun.run_key == normalized_run_key
                    )
                )
                return int(row_id) if row_id is not None else None
        except Exception:
            logger.exception("Failed to recover concurrent AgentRun run_key=%s", run_key)
            return None
    except Exception:
        logger.exception("Failed to ensure AgentRun run_key=%s", run_key)
        return None


async def record_model_usage(
    *,
    agent_run_id: int | None,
    scenario: str,
    service_case_id: int | None,
    message_id: int | None,
    model: str,
    elapsed_ms: int,
    response: Any = None,
    success: bool,
    error_type: str | None = None,
    request_messages: Any = None,
    settings: Settings | None = None,
) -> None:
    """记录一次逻辑模型调用，并累加到所属 AgentRun。"""
    if agent_run_id is None:
        return
    settings = settings or get_settings()
    prompt_tokens, completion_tokens, total_tokens, cache_hit_tokens = _usage_counts(
        response
    )
    prompt_cache_miss_tokens = (
        max(0, int(prompt_tokens) - int(cache_hit_tokens))
        if prompt_tokens is not None and cache_hit_tokens is not None
        else None
    )
    finish_reason = _finish_reason(response)
    # 计费档位按响应实际返回的模型名判断，先于成本计算解析：
    # 文档模型 fallback 时请求模型与实际模型可能不同，按实际的记才不会记错档。
    response_model = _response_model(response) or model
    estimated_cost = _estimated_cost_yuan(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        settings=settings,
        scenario=scenario,
        model=response_model,
        prompt_cache_hit_tokens=cache_hit_tokens,
    )
    is_diagnostic, diagnostic_run_id = _diagnostic_context()
    try:
        async with asyncio.timeout(_WRITE_TIMEOUT_SECONDS), SessionLocal() as session:
            run = await session.get(AgentRun, agent_run_id)
            if run is None:
                return
            session.add(
                AIUsageLog(
                    agent_run_id=agent_run_id,
                    service_case_id=None if is_diagnostic else service_case_id,
                    message_id=None if is_diagnostic else message_id,
                    scenario=scenario[:80],
                    model=response_model[:120],
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    prompt_cache_hit_tokens=cache_hit_tokens,
                    prompt_cache_miss_tokens=prompt_cache_miss_tokens,
                    estimated_cost_yuan=estimated_cost,
                    elapsed_ms=max(0, int(elapsed_ms)),
                    attempts=1,
                    success=success,
                    finish_reason=(finish_reason or "")[:60] or None,
                    error_type=(error_type or "")[:120] or None,
                    is_diagnostic=is_diagnostic,
                    diagnostic_run_id=diagnostic_run_id,
                )
            )
            run.model = response_model[:120]
            run.model_calls = int(run.model_calls or 0) + 1
            run.prompt_tokens = int(run.prompt_tokens or 0) + int(prompt_tokens or 0)
            run.completion_tokens = int(run.completion_tokens or 0) + int(
                completion_tokens or 0
            )
            run.total_tokens = int(run.total_tokens or 0) + int(total_tokens or 0)
            run.estimated_cost_yuan = round(
                float(run.estimated_cost_yuan or 0.0)
                + float(estimated_cost or 0.0),
                8,
            )
            if not success and error_type:
                run.error_type = error_type[:120]
            await session.commit()
    except Exception:
        logger.exception(
            "Failed to record Agent model usage run_id=%s scenario=%s",
            agent_run_id,
            scenario,
        )


async def record_tool_calls(
    *,
    agent_run_id: int | None,
    traces: list[dict[str, Any]],
) -> None:
    """幂等记录一个工具节点产生的调用；tool_call_id 防 checkpoint 重放重复计数。"""
    if agent_run_id is None or not traces:
        return
    try:
        async with asyncio.timeout(_WRITE_TIMEOUT_SECONDS), SessionLocal() as session:
            run = await session.get(AgentRun, agent_run_id)
            if run is None:
                return
            call_ids = {
                str(item.get("tool_call_id") or "")[:160]
                for item in traces
                if item.get("tool_call_id")
            }
            existing_ids = set(
                await session.scalars(
                    select(AgentToolCall.tool_call_id).where(
                        AgentToolCall.agent_run_id == agent_run_id,
                        AgentToolCall.tool_call_id.in_(call_ids),
                    )
                )
            )
            created = 0
            succeeded = 0
            seen_ids = set(existing_ids)
            for trace in traces:
                call_id = str(trace.get("tool_call_id") or "")[:160]
                if not call_id or call_id in seen_ids:
                    continue
                seen_ids.add(call_id)
                status = _tool_status(trace)
                error = str(trace.get("error") or "")
                session.add(
                    AgentToolCall(
                        agent_run_id=agent_run_id,
                        tool_call_id=call_id,
                        tool_name=str(trace.get("name") or "unknown")[:120],
                        status=status,
                        elapsed_ms=_nonnegative_int(trace.get("elapsed_ms")),
                        error_type=_tool_error_type(error),
                        metadata_json={
                            "arg_keys": sorted(
                                str(key)
                                for key in (trace.get("args") or {}).keys()
                            ),
                            "data_tool_failed": bool(
                                trace.get("data_tool_failed")
                            ),
                            "circuit_rejected": bool(
                                trace.get("circuit_rejected")
                            ),
                        },
                    )
                )
                created += 1
                succeeded += int(status == "success")
            if created:
                run.tool_calls = int(run.tool_calls or 0) + created
                run.successful_tool_calls = int(
                    run.successful_tool_calls or 0
                ) + succeeded
                await session.commit()
    except Exception:
        logger.exception("Failed to record Agent tool calls run_id=%s", agent_run_id)


async def complete_agent_run(
    *,
    agent_run_id: int | None,
    status: str,
    outcome: str,
    latency_ms: int | None = None,
    input_count: int | None = None,
    output_count: int | None = None,
    dropped_count: int | None = None,
    error_type: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    if agent_run_id is None:
        return
    try:
        async with asyncio.timeout(_WRITE_TIMEOUT_SECONDS), SessionLocal() as session:
            run = await session.get(AgentRun, agent_run_id)
            if run is None:
                return
            run.status = status[:24]
            run.outcome = outcome[:64]
            run.finished_at = datetime.now(timezone.utc)
            if latency_ms is not None:
                run.latency_ms = max(0, int(latency_ms))
            if input_count is not None:
                run.input_count = max(0, int(input_count))
            if output_count is not None:
                run.output_count = max(0, int(output_count))
            if dropped_count is not None:
                run.dropped_count = max(0, int(dropped_count))
            if error_type:
                run.error_type = error_type[:120]
            if metadata:
                run.metadata_json = {
                    **(run.metadata_json or {}),
                    **metadata,
                }
            await session.commit()
    except Exception:
        logger.exception(
            "Failed to complete AgentRun run_id=%s outcome=%s",
            agent_run_id,
            outcome,
        )


async def sweep_interrupted_agent_runs(
    *,
    stale_after_seconds: float = INTERRUPTED_STALE_AFTER_SECONDS,
) -> int:
    """把进程被杀后永远停在"运行中"的运行行收尾成"进程中断"终态。

    没有任何在线路径会回头收尾这些行：续跑是**新的一次尝试**（run_key 带每次尝试的随机
    标识 → 新行），收尾代码只认自己开跑时拿到的行号，也拿不到前一次的行号——业务恢复只
    依据领域事实（归档水位线 / 收件箱认领状态 / 消息上的回复标记），刻意不反查本台账。
    于是"运行中"这个只在运行那几十秒内成立的临时状态会变成永久假状态，真有运行卡死时被
    历史噪音掩盖，成功率分母也一直挂着永不收尾的行。

    只改状态：花费/调用数随每次模型调用即时入账，已经是准确值，不需要回填；真实结束时刻
    不可知，故不写 finished_at/latency_ms，绝不编造耗时。与其它观测写入一致，失败只告警。
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=max(0.0, stale_after_seconds))
    try:
        async with asyncio.timeout(_WRITE_TIMEOUT_SECONDS), SessionLocal() as session:
            runs = list(
                await session.scalars(
                    select(AgentRun)
                    .where(
                        AgentRun.status == RUNNING,
                        func.coalesce(AgentRun.started_at, AgentRun.created_at) < cutoff,
                    )
                    .order_by(AgentRun.id)
                    .limit(_INTERRUPTED_SWEEP_LIMIT)
                )
            )
            if not runs:
                return 0
            for run in runs:
                run.status = INTERRUPTED
                run.outcome = INTERRUPTED_OUTCOME
                run.error_type = run.error_type or "ProcessInterrupted"
            await session.commit()
            logger.warning(
                "Marked interrupted AgentRuns count=%d ids=%s",
                len(runs),
                [run.id for run in runs],
            )
            return len(runs)
    except Exception:
        logger.exception("Failed to sweep interrupted AgentRuns")
        return 0


def _usage_counts(
    response: Any,
) -> tuple[int | None, int | None, int | None, int | None]:
    """返回 (输入 token, 输出 token, 总 token, 缓存命中输入 token)。

    LangChain 把缓存命中放在 usage_metadata.input_token_details.cache_read；
    原始 DeepSeek 响应则在 response_metadata.token_usage.prompt_cache_hit_tokens。
    """
    usage = getattr(response, "usage_metadata", None)
    if isinstance(usage, dict):
        prompt = _optional_int(
            usage.get("input_tokens", usage.get("prompt_tokens"))
        )
        completion = _optional_int(
            usage.get("output_tokens", usage.get("completion_tokens"))
        )
        total = _optional_int(usage.get("total_tokens"))
        if total is None and (prompt is not None or completion is not None):
            total = int(prompt or 0) + int(completion or 0)
        details = usage.get("input_token_details")
        cache_hit = (
            _optional_int(details.get("cache_read"))
            if isinstance(details, dict)
            else None
        )
        return prompt, completion, total, cache_hit
    metadata = getattr(response, "response_metadata", None)
    token_usage = (
        metadata.get("token_usage")
        if isinstance(metadata, dict)
        and isinstance(metadata.get("token_usage"), dict)
        else {}
    )
    prompt = _optional_int(
        token_usage.get("prompt_tokens", token_usage.get("input_tokens"))
    )
    completion = _optional_int(
        token_usage.get("completion_tokens", token_usage.get("output_tokens"))
    )
    total = _optional_int(token_usage.get("total_tokens"))
    if total is None and (prompt is not None or completion is not None):
        total = int(prompt or 0) + int(completion or 0)
    cache_hit = _optional_int(token_usage.get("prompt_cache_hit_tokens"))
    return prompt, completion, total, cache_hit


def _finish_reason(response: Any) -> str | None:
    metadata = getattr(response, "response_metadata", None)
    if not isinstance(metadata, dict):
        return None
    return str(metadata.get("finish_reason") or "") or None


def _response_model(response: Any) -> str | None:
    metadata = getattr(response, "response_metadata", None)
    if not isinstance(metadata, dict):
        return None
    return str(metadata.get("model_name") or metadata.get("model") or "") or None


def _tool_status(trace: dict[str, Any]) -> str:
    if trace.get("ok"):
        return "success"
    error = str(trace.get("error") or "")
    if (
        trace.get("circuit_rejected")
        or error.startswith("TOOL_REJECTED")
        or error.startswith("TOOL_LIMIT_REACHED")
        or error.startswith("TOOL_CIRCUIT_OPEN")
    ):
        return "rejected"
    return "failed"


def _tool_error_type(error: str) -> str | None:
    if not error:
        return None
    if error.startswith("timeout>"):
        return "TimeoutError"
    prefix = error.split(":", 1)[0].strip()
    return (prefix or "ToolError")[:120]


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _nonnegative_int(value: Any) -> int | None:
    parsed = _optional_int(value)
    return max(0, parsed) if parsed is not None else None
