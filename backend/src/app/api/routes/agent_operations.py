from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.agent_run import AgentRun
from app.models.agent_tool_call import AgentToolCall
from app.models.ai_usage import AIUsageLog
from app.models.archivist_extract_trace import ArchivistExtractTrace
from app.models.knowledge_draft import KnowledgeDraft
from app.models.message import (
    Message,
    MessageDirection,
    MessagePurpose,
    MessageSenderType,
)
from app.models.service_case import ServiceCase
from app.models.user import User
from app.schemas.agent_operations import (
    AgentFailureDetail,
    AgentOperationsOverview,
    AgentConversationMessage,
    AgentKnowledgeDecision,
    AgentOutcomeCount,
    AgentRunDetail,
    AgentRunPage,
    AgentRuntimeSummary,
    AgentToolStat,
    AICostCaseStat,
    AICostOverview,
    AICostScenarioStat,
    ArchivistBatchChange,
    ArchivistBatchRun,
    ArchivistInputMessage,
    ArchivistRuntimeSummary,
    RecentAgentRun,
)

router = APIRouter()


@router.get("/runs", response_model=AgentRunPage)
async def list_agent_runs(
    days: int = Query(default=30, ge=1, le=90),
    outcome: str = Query(min_length=1, max_length=64),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> AgentRunPage:
    """按当前滚动时间范围下钻某一种群内回复结果，不受首页最近 40 条限制。"""
    period_start = datetime.now(timezone.utc) - timedelta(days=days)
    filters = (
        AgentRun.created_at >= period_start,
        AgentRun.is_diagnostic.is_(False),
        AgentRun.agent_type == "reply",
        AgentRun.run_kind == "interactive",
        AgentRun.outcome == outcome,
    )
    total = int(
        await session.scalar(select(func.count(AgentRun.id)).where(*filters)) or 0
    )
    rows = (
        await session.execute(
            select(AgentRun, ServiceCase.case_no, User.name)
            .outerjoin(ServiceCase, AgentRun.service_case_id == ServiceCase.id)
            .outerjoin(User, ServiceCase.user_id == User.id)
            .where(*filters)
            .order_by(AgentRun.started_at.desc(), AgentRun.id.desc())
            .offset(offset)
            .limit(limit)
        )
    ).all()
    return AgentRunPage(
        total=total,
        limit=limit,
        offset=offset,
        items=await _run_details(session, rows),
    )


@router.get("/runs/{run_id}", response_model=AgentRunDetail)
async def get_agent_run_detail(
    run_id: int,
    session: AsyncSession = Depends(get_session),
) -> AgentRunDetail:
    row = (
        await session.execute(
            select(AgentRun, ServiceCase.case_no, User.name)
            .outerjoin(ServiceCase, AgentRun.service_case_id == ServiceCase.id)
            .outerjoin(User, ServiceCase.user_id == User.id)
            .where(
                AgentRun.id == run_id,
                AgentRun.is_diagnostic.is_(False),
            )
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Agent run not found")

    run, case_no, user_name = row
    details = await _run_details(session, [(run, case_no, user_name)])
    return details[0]


async def _run_details(
    session: AsyncSession,
    rows: list[tuple[AgentRun, str | None, str | None]],
) -> list[AgentRunDetail]:
    trigger_ids = [
        int(run.trigger_message_id)
        for run, _case_no, _user_name in rows
        if run.agent_type == "reply" and run.trigger_message_id is not None
    ]
    inbound_by_id: dict[int, Message] = {}
    outbound_by_trigger: dict[int, Message] = {}
    if trigger_ids:
        inbound_rows = list(
            await session.scalars(
                select(Message).where(
                    Message.id.in_(trigger_ids),
                    Message.direction == MessageDirection.INBOUND,
                )
            )
        )
        inbound_by_id = {message.id: message for message in inbound_rows}
        outbound_rows = list(
            await session.scalars(
                select(Message)
                .where(
                    Message.trigger_id.in_(trigger_ids),
                    Message.direction == MessageDirection.OUTBOUND,
                    Message.sender_type == MessageSenderType.BOT,
                    Message.purpose == MessagePurpose.CHAT,
                )
                .order_by(Message.trigger_id, Message.id.desc())
            )
        )
        for message in outbound_rows:
            if message.trigger_id is not None:
                outbound_by_trigger.setdefault(int(message.trigger_id), message)

    details: list[AgentRunDetail] = []
    for run, case_no, user_name in rows:
        trigger_id = int(run.trigger_message_id) if run.trigger_message_id else None
        input_message = inbound_by_id.get(trigger_id) if trigger_id else None
        output_message = outbound_by_trigger.get(trigger_id) if trigger_id else None
        details.append(
            AgentRunDetail(
                **_recent_run_payload(run, case_no=case_no, user_name=user_name),
                input_message=_conversation_message(input_message),
                output_message=_conversation_message(output_message),
                knowledge=_knowledge_decision(run),
                failure=_failure_detail(run, input_message, output_message),
            )
        )
    return details


def _knowledge_decision(run: AgentRun) -> AgentKnowledgeDecision | None:
    """回复轮次的参考问答检索决策。历史运行没有这个字段，返回 None 而不是伪造。"""
    metadata = run.metadata_json if isinstance(run.metadata_json, dict) else {}
    knowledge = metadata.get("knowledge")
    if not isinstance(knowledge, dict) or not knowledge.get("decision"):
        return None
    return AgentKnowledgeDecision(
        decision=str(knowledge.get("decision")),
        query=(
            str(knowledge["query"])
            if isinstance(knowledge.get("query"), str)
            else None
        ),
        hit_count=_optional_int(knowledge.get("hit_count")),
        skipped_product_terms=[
            str(term)
            for term in (knowledge.get("skipped_product_terms") or [])
            if isinstance(term, str)
        ],
    )


def _failure_detail(
    run: AgentRun,
    input_message: Message | None,
    output_message: Message | None,
) -> AgentFailureDetail | None:
    if run.outcome not in {"agent_generation_failed", "send_failed", "send_unknown"}:
        return None

    run_metadata = run.metadata_json if isinstance(run.metadata_json, dict) else {}
    failure = run_metadata.get("failure")
    failure = dict(failure) if isinstance(failure, dict) else {}
    input_analysis = (
        input_message.analysis_json
        if input_message is not None and isinstance(input_message.analysis_json, dict)
        else {}
    )
    message_failure = input_analysis.get("agent_reply_failure")
    if isinstance(message_failure, dict):
        failure = {**message_failure, **failure}
    processing = input_analysis.get("_reply_processing")
    processing = processing if isinstance(processing, dict) else {}

    stage = str(
        failure.get("stage")
        or ("delivery" if run.outcome in {"send_failed", "send_unknown"} else "generation")
    )
    error_type = str(failure.get("error_type") or run.error_type or "").strip() or None
    timeout_seconds = _optional_float(failure.get("timeout_seconds"))
    elapsed_ms = _optional_int(failure.get("elapsed_ms"))
    return AgentFailureDetail(
        stage=stage,
        error_type=error_type,
        summary=_failure_summary(
            outcome=run.outcome or "",
            error_type=error_type,
            timeout_seconds=timeout_seconds,
            outbound_error=output_message.error if output_message is not None else None,
        ),
        retryable=bool(failure.get("retryable"))
        or processing.get("status") == "retryable_failure",
        timeout_seconds=timeout_seconds,
        elapsed_ms=elapsed_ms,
        failed_at=_optional_datetime(failure.get("failed_at")),
    )


def _failure_summary(
    *,
    outcome: str,
    error_type: str | None,
    timeout_seconds: float | None,
    outbound_error: str | None,
) -> str:
    if outcome == "send_unknown":
        return "回复已提交发送，但企微没有返回明确结果"
    if outcome == "send_failed":
        normalized = (outbound_error or "").lower()
        if "1011" in normalized or "spam" in normalized or "风控" in normalized:
            return "回复被企业微信风控拦截，未送达用户"
        return "回复已经生成，但发送到群里失败"
    if error_type in {"TimeoutError", "APITimeoutError"}:
        if timeout_seconds is not None:
            seconds = int(timeout_seconds) if timeout_seconds.is_integer() else timeout_seconds
            return f"回复生成超过 {seconds} 秒，系统停止了本次生成"
        return "回复生成超时，系统停止了本次生成"
    if error_type == "RateLimitError":
        return "AI 服务繁忙或触发限流，未能生成回复"
    if error_type == "APIConnectionError":
        return "连接 AI 服务失败，未能生成回复"
    if error_type in {"APIStatusError", "InternalServerError"}:
        return "AI 服务返回异常，未能生成回复"
    return "回复生成过程中出现异常，未能生成可发送的回复"


@router.get("", response_model=AgentOperationsOverview)
async def get_agent_operations(
    days: int = Query(default=30, ge=1, le=90),
    session: AsyncSession = Depends(get_session),
) -> AgentOperationsOverview:
    """当前实现的 Agent 运行、工具、效果和成本，不与历史版本做对照。"""
    generated_at = datetime.now(timezone.utc)
    period_start = generated_at - timedelta(days=days)

    run_groups = (
        await session.execute(
            select(
                AgentRun.agent_type,
                AgentRun.run_kind,
                AgentRun.status,
                AgentRun.outcome,
                func.count(AgentRun.id),
                func.coalesce(func.sum(AgentRun.model_calls), 0),
                func.coalesce(func.sum(AgentRun.tool_calls), 0),
                func.coalesce(func.sum(AgentRun.successful_tool_calls), 0),
                func.coalesce(func.sum(AgentRun.prompt_tokens), 0),
                func.coalesce(func.sum(AgentRun.completion_tokens), 0),
                func.coalesce(func.sum(AgentRun.total_tokens), 0),
                func.coalesce(func.sum(AgentRun.estimated_cost_yuan), 0.0),
                func.coalesce(func.sum(AgentRun.input_count), 0),
                func.coalesce(func.sum(AgentRun.output_count), 0),
                func.coalesce(func.sum(AgentRun.dropped_count), 0),
            )
            .where(
                AgentRun.created_at >= period_start,
                AgentRun.is_diagnostic.is_(False),
            )
            .group_by(
                AgentRun.agent_type,
                AgentRun.run_kind,
                AgentRun.status,
                AgentRun.outcome,
            )
        )
    ).all()
    latency_rows = (
        await session.execute(
            select(AgentRun.agent_type, AgentRun.run_kind, AgentRun.latency_ms)
            .where(
                AgentRun.created_at >= period_start,
                AgentRun.is_diagnostic.is_(False),
                AgentRun.latency_ms.is_not(None),
            )
        )
    ).all()

    all_agents = _runtime_summary(run_groups, latency_rows, lambda *_: True)
    reply = _runtime_summary(
        run_groups,
        latency_rows,
        lambda agent_type, run_kind: (
            agent_type == "reply" and run_kind == "interactive"
        ),
    )
    reminders = _runtime_summary(
        run_groups,
        latency_rows,
        lambda agent_type, run_kind: (
            agent_type == "reply" and run_kind == "scheduled_reminder"
        ),
    )
    archivist_base = _runtime_summary(
        run_groups,
        latency_rows,
        lambda agent_type, _run_kind: agent_type == "archivist",
    )
    archivist = ArchivistRuntimeSummary(
        **archivist_base.model_dump(),
        output_breakdown=await _archivist_output_breakdown(
            session, period_start=period_start
        ),
        draft_statuses=await _archivist_draft_statuses(
            session, period_start=period_start
        ),
    )

    return AgentOperationsOverview(
        days=days,
        period_start=period_start,
        generated_at=generated_at,
        all_agents=all_agents,
        reply=reply,
        reminders=reminders,
        archivist=archivist,
        tools=await _tool_stats(session, period_start=period_start),
        costs=await _cost_overview(session, period_start=period_start),
        recent_runs=await _recent_runs(session, period_start=period_start),
        archivist_batches=await _archivist_batches(
            session, period_start=period_start
        ),
    )


def _runtime_summary(
    rows: list[Any],
    latency_rows: list[Any],
    include: Callable[[str, str], bool],
) -> AgentRuntimeSummary:
    values = {
        "runs": 0,
        "completed_runs": 0,
        "failed_runs": 0,
        "running_runs": 0,
        "model_calls": 0,
        "tool_calls": 0,
        "successful_tool_calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "estimated_cost_yuan": 0.0,
        "input_count": 0,
        "output_count": 0,
        "dropped_count": 0,
    }
    outcomes: Counter[str] = Counter()
    for row in rows:
        agent_type, run_kind, status, outcome = row[:4]
        if not include(agent_type, run_kind):
            continue
        count = int(row[4] or 0)
        values["runs"] += count
        values["completed_runs"] += count if status == "completed" else 0
        values["failed_runs"] += count if status == "failed" else 0
        values["running_runs"] += count if status == "running" else 0
        for key, index in (
            ("model_calls", 5),
            ("tool_calls", 6),
            ("successful_tool_calls", 7),
            ("prompt_tokens", 8),
            ("completion_tokens", 9),
            ("total_tokens", 10),
            ("input_count", 12),
            ("output_count", 13),
            ("dropped_count", 14),
        ):
            values[key] += int(row[index] or 0)
        values["estimated_cost_yuan"] += float(row[11] or 0.0)
        outcomes[str(outcome or "running")] += count

    latencies = sorted(
        int(latency)
        for agent_type, run_kind, latency in latency_rows
        if include(agent_type, run_kind) and latency is not None
    )
    values["estimated_cost_yuan"] = round(
        float(values["estimated_cost_yuan"]), 6
    )
    return AgentRuntimeSummary(
        **values,
        latency_p50_ms=_percentile(latencies, 0.50),
        latency_p95_ms=_percentile(latencies, 0.95),
        outcomes=[
            AgentOutcomeCount(key=key, count=count)
            for key, count in outcomes.most_common()
        ],
    )


def _percentile(values: list[int], fraction: float) -> int | None:
    if not values:
        return None
    index = round((len(values) - 1) * fraction)
    return values[max(0, min(index, len(values) - 1))]


async def _archivist_output_breakdown(
    session: AsyncSession,
    *,
    period_start: datetime,
) -> dict[str, int]:
    rows = list(
        await session.scalars(
            select(AgentRun.metadata_json).where(
                AgentRun.agent_type == "archivist",
                AgentRun.created_at >= period_start,
                AgentRun.is_diagnostic.is_(False),
            )
        )
    )
    totals: Counter[str] = Counter()
    for metadata in rows:
        breakdown = (
            metadata.get("output_breakdown")
            if isinstance(metadata, dict)
            else None
        )
        if not isinstance(breakdown, dict):
            continue
        for key, value in breakdown.items():
            try:
                totals[str(key)] += max(0, int(value or 0))
            except (TypeError, ValueError):
                continue
    return dict(totals)


async def _archivist_draft_statuses(
    session: AsyncSession,
    *,
    period_start: datetime,
) -> dict[str, int]:
    rows = (
        await session.execute(
            select(KnowledgeDraft.status, KnowledgeDraft.content_json).where(
                KnowledgeDraft.created_at >= period_start,
            )
        )
    ).all()
    counts: Counter[str] = Counter()
    for status, content_json in rows:
        scan_type = (
            content_json.get("scan_type")
            if isinstance(content_json, dict)
            else None
        )
        if scan_type not in {"archivist_delta", "archivist_outcome_stable"}:
            continue
        counts[str(status)] += 1
    counts["total"] = sum(counts.values())
    return dict(counts)


async def _tool_stats(
    session: AsyncSession,
    *,
    period_start: datetime,
) -> list[AgentToolStat]:
    rows = (
        await session.execute(
            select(
                AgentToolCall.tool_name,
                AgentToolCall.status,
                AgentToolCall.error_type,
                func.count(AgentToolCall.id),
                func.coalesce(func.sum(AgentToolCall.elapsed_ms), 0),
                func.count(AgentToolCall.elapsed_ms),
            )
            .join(AgentRun, AgentToolCall.agent_run_id == AgentRun.id)
            .where(
                AgentToolCall.created_at >= period_start,
                AgentRun.is_diagnostic.is_(False),
            )
            .group_by(
                AgentToolCall.tool_name,
                AgentToolCall.status,
                AgentToolCall.error_type,
            )
        )
    ).all()
    grouped: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "calls": 0,
            "successful_calls": 0,
            "failed_calls": 0,
            "rejected_calls": 0,
            "elapsed_total": 0,
            "elapsed_count": 0,
            "errors": Counter(),
        }
    )
    for name, status, error_type, count, elapsed_total, elapsed_count in rows:
        item = grouped[str(name)]
        count = int(count or 0)
        item["calls"] += count
        item["successful_calls"] += count if status == "success" else 0
        item["failed_calls"] += count if status == "failed" else 0
        item["rejected_calls"] += count if status == "rejected" else 0
        item["elapsed_total"] += int(elapsed_total or 0)
        item["elapsed_count"] += int(elapsed_count or 0)
        if error_type:
            item["errors"][str(error_type)] += count
    return [
        AgentToolStat(
            name=name,
            calls=item["calls"],
            successful_calls=item["successful_calls"],
            failed_calls=item["failed_calls"],
            rejected_calls=item["rejected_calls"],
            success_rate=(
                round(item["successful_calls"] / item["calls"] * 100, 1)
                if item["calls"]
                else None
            ),
            average_latency_ms=(
                round(item["elapsed_total"] / item["elapsed_count"])
                if item["elapsed_count"]
                else None
            ),
            errors=[
                AgentOutcomeCount(key=key, count=count)
                for key, count in item["errors"].most_common(5)
            ],
        )
        for name, item in sorted(
            grouped.items(),
            key=lambda pair: (-pair[1]["calls"], pair[0]),
        )
    ]


async def _cost_overview(
    session: AsyncSession,
    *,
    period_start: datetime,
) -> AICostOverview:
    scenario_rows = (
        await session.execute(
            select(
                AIUsageLog.scenario,
                func.count(AIUsageLog.id),
                func.count(AIUsageLog.id).filter(AIUsageLog.success.is_(True)),
                func.coalesce(func.sum(AIUsageLog.prompt_tokens), 0),
                func.coalesce(func.sum(AIUsageLog.completion_tokens), 0),
                func.coalesce(func.sum(AIUsageLog.total_tokens), 0),
                func.coalesce(func.sum(AIUsageLog.estimated_cost_yuan), 0.0),
            )
            .where(
                AIUsageLog.created_at >= period_start,
                AIUsageLog.is_diagnostic.is_(False),
            )
            .group_by(AIUsageLog.scenario)
            .order_by(
                func.coalesce(
                    func.sum(AIUsageLog.estimated_cost_yuan), 0.0
                ).desc()
            )
        )
    ).all()
    scenarios = [
        AICostScenarioStat(
            scenario=str(row[0]),
            calls=int(row[1] or 0),
            successful_calls=int(row[2] or 0),
            prompt_tokens=int(row[3] or 0),
            completion_tokens=int(row[4] or 0),
            total_tokens=int(row[5] or 0),
            estimated_cost_yuan=round(float(row[6] or 0.0), 6),
        )
        for row in scenario_rows
    ]

    case_rows = (
        await session.execute(
            select(
                ServiceCase.id,
                ServiceCase.case_no,
                User.name,
                func.count(AIUsageLog.id),
                func.coalesce(func.sum(AIUsageLog.total_tokens), 0),
                func.coalesce(func.sum(AIUsageLog.estimated_cost_yuan), 0.0),
                func.max(AIUsageLog.created_at),
            )
            .join(ServiceCase, AIUsageLog.service_case_id == ServiceCase.id)
            .outerjoin(User, ServiceCase.user_id == User.id)
            .where(
                AIUsageLog.created_at >= period_start,
                AIUsageLog.is_diagnostic.is_(False),
            )
            .group_by(ServiceCase.id, ServiceCase.case_no, User.name)
            .order_by(
                func.coalesce(
                    func.sum(AIUsageLog.estimated_cost_yuan), 0.0
                ).desc()
            )
            .limit(200)
        )
    ).all()
    case_ids = [int(row[0]) for row in case_rows]
    case_scenario_rows = []
    if case_ids:
        case_scenario_rows = (
            await session.execute(
                select(
                    AIUsageLog.service_case_id,
                    AIUsageLog.scenario,
                    func.count(AIUsageLog.id),
                    func.count(AIUsageLog.id).filter(
                        AIUsageLog.success.is_(True)
                    ),
                    func.coalesce(func.sum(AIUsageLog.prompt_tokens), 0),
                    func.coalesce(func.sum(AIUsageLog.completion_tokens), 0),
                    func.coalesce(func.sum(AIUsageLog.total_tokens), 0),
                    func.coalesce(
                        func.sum(AIUsageLog.estimated_cost_yuan), 0.0
                    ),
                )
                .where(
                    AIUsageLog.created_at >= period_start,
                    AIUsageLog.is_diagnostic.is_(False),
                    AIUsageLog.service_case_id.in_(case_ids),
                )
                .group_by(AIUsageLog.service_case_id, AIUsageLog.scenario)
                .order_by(
                    AIUsageLog.service_case_id,
                    func.coalesce(
                        func.sum(AIUsageLog.estimated_cost_yuan), 0.0
                    ).desc(),
                )
            )
        ).all()
    scenarios_by_case: dict[int, list[AICostScenarioStat]] = defaultdict(list)
    for row in case_scenario_rows:
        scenarios_by_case[int(row[0])].append(
            AICostScenarioStat(
                scenario=str(row[1]),
                calls=int(row[2] or 0),
                successful_calls=int(row[3] or 0),
                prompt_tokens=int(row[4] or 0),
                completion_tokens=int(row[5] or 0),
                total_tokens=int(row[6] or 0),
                estimated_cost_yuan=round(float(row[7] or 0.0), 6),
            )
        )
    assigned_cost, service_case_count = (
        await session.execute(
            select(
                func.coalesce(func.sum(AIUsageLog.estimated_cost_yuan), 0.0),
                func.count(func.distinct(AIUsageLog.service_case_id)),
            ).where(
                AIUsageLog.created_at >= period_start,
                AIUsageLog.is_diagnostic.is_(False),
                AIUsageLog.service_case_id.is_not(None),
            )
        )
    ).one()
    total_cost = round(
        sum(item.estimated_cost_yuan for item in scenarios), 6
    )
    return AICostOverview(
        calls=sum(item.calls for item in scenarios),
        successful_calls=sum(item.successful_calls for item in scenarios),
        prompt_tokens=sum(item.prompt_tokens for item in scenarios),
        completion_tokens=sum(item.completion_tokens for item in scenarios),
        total_tokens=sum(item.total_tokens for item in scenarios),
        estimated_cost_yuan=total_cost,
        service_case_count=int(service_case_count or 0),
        unassigned_cost_yuan=round(
            max(0.0, total_cost - float(assigned_cost or 0.0)), 6
        ),
        scenarios=scenarios,
        cases=[
            AICostCaseStat(
                service_case_id=int(row[0]),
                case_no=str(row[1]),
                user_name=row[2],
                calls=int(row[3] or 0),
                total_tokens=int(row[4] or 0),
                estimated_cost_yuan=round(float(row[5] or 0.0), 6),
                last_call_at=row[6],
                scenarios=scenarios_by_case.get(int(row[0]), []),
            )
            for row in case_rows
        ],
    )


async def _recent_runs(
    session: AsyncSession,
    *,
    period_start: datetime,
) -> list[RecentAgentRun]:
    rows = (
        await session.execute(
            select(AgentRun, ServiceCase.case_no, User.name)
            .outerjoin(ServiceCase, AgentRun.service_case_id == ServiceCase.id)
            .outerjoin(User, ServiceCase.user_id == User.id)
            .where(
                AgentRun.created_at >= period_start,
                AgentRun.is_diagnostic.is_(False),
            )
            .order_by(AgentRun.started_at.desc(), AgentRun.id.desc())
            .limit(40)
        )
    ).all()
    return [
        RecentAgentRun(
            **_recent_run_payload(run, case_no=case_no, user_name=user_name)
        )
        for run, case_no, user_name in rows
    ]


async def _archivist_batches(
    session: AsyncSession,
    *,
    period_start: datetime,
) -> list[ArchivistBatchRun]:
    rows = (
        await session.execute(
            select(AgentRun, ServiceCase.case_no, User.name)
            .outerjoin(ServiceCase, AgentRun.service_case_id == ServiceCase.id)
            .outerjoin(User, ServiceCase.user_id == User.id)
            .where(
                AgentRun.agent_type == "archivist",
                AgentRun.created_at >= period_start,
                AgentRun.is_diagnostic.is_(False),
            )
            .order_by(AgentRun.started_at.desc(), AgentRun.id.desc())
            .limit(30)
        )
    ).all()
    if not rows:
        return []

    run_ids = [run.id for run, _case_no, _user_name in rows]
    traces = list(
        await session.scalars(
            select(ArchivistExtractTrace).where(
                ArchivistExtractTrace.agent_run_id.in_(run_ids)
            )
        )
    )
    dropped_by_run: dict[int, list[dict]] = defaultdict(list)
    for trace in traces:
        if trace.agent_run_id is None:
            continue
        dropped_by_run[trace.agent_run_id].extend(
            item
            for item in (trace.dropped_json or [])
            if isinstance(item, dict)
        )

    exact_ids: set[int] = set()
    for run, _case_no, _user_name in rows:
        metadata = run.metadata_json if isinstance(run.metadata_json, dict) else {}
        for value in metadata.get("input_message_ids") or []:
            try:
                exact_ids.add(int(value))
            except (TypeError, ValueError):
                continue
    exact_messages = (
        list(
            await session.scalars(
                select(Message)
                .where(Message.id.in_(exact_ids))
                .order_by(Message.id.asc())
            )
        )
        if exact_ids
        else []
    )
    message_by_id = {message.id: message for message in exact_messages}

    batches: list[ArchivistBatchRun] = []
    for run, case_no, user_name in rows:
        metadata = run.metadata_json if isinstance(run.metadata_json, dict) else {}
        raw_ids = metadata.get("input_message_ids")
        input_source = "exact" if isinstance(raw_ids, list) else "unavailable"
        message_ids: list[int] = []
        for value in raw_ids or []:
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                continue
            if parsed not in message_ids:
                message_ids.append(parsed)

        fallback_messages: list[Message] = []
        if not message_ids and run.service_case_id is not None:
            watermark_from = _optional_int(metadata.get("watermark_from"))
            watermark_to = _optional_int(metadata.get("watermark_to"))
            if watermark_to is not None:
                fallback_messages = list(
                    await session.scalars(
                        select(Message)
                        .where(
                            Message.service_case_id == run.service_case_id,
                            Message.id > (watermark_from or 0),
                            Message.id <= watermark_to,
                            Message.sender_type.in_(
                                [
                                    MessageSenderType.USER,
                                    MessageSenderType.STAFF,
                                    MessageSenderType.BOT,
                                ]
                            ),
                            Message.purpose == MessagePurpose.CHAT,
                        )
                        .order_by(Message.id.asc())
                    )
                )
                input_source = "watermark_range"
        selected_messages = (
            [message_by_id[item] for item in message_ids if item in message_by_id]
            if message_ids
            else fallback_messages
        )

        raw_changes = metadata.get("changes")
        detail_available = isinstance(raw_changes, list)
        changes = [
            ArchivistBatchChange.model_validate(item)
            for item in (raw_changes or [])
            if isinstance(item, dict)
        ]
        if not detail_available:
            changes = [
                ArchivistBatchChange(
                    kind=str(key),
                    action="count_only",
                    title=str(key),
                    after=f"{int(value or 0)} 项",
                )
                for key, value in (
                    metadata.get("output_breakdown")
                    if isinstance(metadata.get("output_breakdown"), dict)
                    else {}
                ).items()
                if int(value or 0) > 0
            ]

        batches.append(
            ArchivistBatchRun(
                id=run.id,
                agent_type=run.agent_type,
                run_kind=run.run_kind,
                status=run.status,
                outcome=run.outcome,
                service_case_id=run.service_case_id,
                case_no=case_no,
                user_name=user_name,
                model_calls=run.model_calls,
                tool_calls=run.tool_calls,
                prompt_tokens=run.prompt_tokens,
                completion_tokens=run.completion_tokens,
                total_tokens=run.total_tokens,
                estimated_cost_yuan=round(
                    float(run.estimated_cost_yuan or 0.0), 6
                ),
                input_count=run.input_count,
                output_count=run.output_count,
                latency_ms=run.latency_ms,
                error_type=run.error_type,
                started_at=run.started_at,
                finished_at=run.finished_at,
                trigger_reason=run.trigger_reason,
                input_source=input_source,
                input_messages=[
                    ArchivistInputMessage(
                        id=message.id,
                        role=_archivist_role(message.sender_type),
                        content=(message.content or "")[:4000],
                        created_at=message.created_at,
                        is_context=bool(
                            message.sender_type == MessageSenderType.BOT
                            and message.id
                            <= (_optional_int(metadata.get("watermark_from")) or 0)
                        ),
                    )
                    for message in selected_messages
                    if message.created_at is not None
                ],
                changes=changes,
                changes_detail_available=detail_available,
                dropped_items=dropped_by_run.get(run.id, [])[:50],
            )
        )
    return batches


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _optional_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _recent_run_payload(
    run: AgentRun,
    *,
    case_no: str | None,
    user_name: str | None,
) -> dict[str, Any]:
    return {
        "id": run.id,
        "agent_type": run.agent_type,
        "run_kind": run.run_kind,
        "status": run.status,
        "outcome": run.outcome,
        "service_case_id": run.service_case_id,
        "case_no": case_no,
        "user_name": user_name,
        "model_calls": run.model_calls,
        "tool_calls": run.tool_calls,
        "prompt_tokens": run.prompt_tokens,
        "completion_tokens": run.completion_tokens,
        "total_tokens": run.total_tokens,
        "estimated_cost_yuan": round(
            float(run.estimated_cost_yuan or 0.0), 6
        ),
        "input_count": run.input_count,
        "output_count": run.output_count,
        "latency_ms": run.latency_ms,
        "error_type": run.error_type,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
    }


def _conversation_message(
    message: Message | None,
) -> AgentConversationMessage | None:
    if message is None or message.created_at is None:
        return None
    role = {
        MessageSenderType.USER: "用户",
        MessageSenderType.STAFF: "班主任",
        MessageSenderType.BOT: "AI 助教",
    }.get(message.sender_type, message.sender_type.value)
    return AgentConversationMessage(
        id=message.id,
        role=role,
        content=message.content or "",
        message_type=message.message_type,
        status=message.status.value,
        created_at=message.created_at,
        sent_at=message.sent_at,
        error=message.error,
    )


def _archivist_role(sender_type: MessageSenderType) -> str:
    return {
        MessageSenderType.USER: "用户",
        MessageSenderType.STAFF: "班主任",
        MessageSenderType.BOT: "助教",
    }.get(sender_type, sender_type.value)
