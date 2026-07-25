from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.agent_run import AgentRun
from app.models.agent_tool_call import AgentToolCall
from app.models.ai_usage import AIUsageLog
from app.models.knowledge_draft import KnowledgeDraft
from app.models.service_case import ServiceCase
from app.models.user import User
from app.schemas.agent_operations import (
    AgentOperationsOverview,
    AgentOutcomeCount,
    AgentRuntimeSummary,
    AgentToolStat,
    AICostCaseStat,
    AICostOverview,
    AICostScenarioStat,
    ArchivistRuntimeSummary,
    RecentAgentRun,
)

router = APIRouter()


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
    return AICostOverview(
        calls=sum(item.calls for item in scenarios),
        successful_calls=sum(item.successful_calls for item in scenarios),
        prompt_tokens=sum(item.prompt_tokens for item in scenarios),
        completion_tokens=sum(item.completion_tokens for item in scenarios),
        total_tokens=sum(item.total_tokens for item in scenarios),
        estimated_cost_yuan=round(
            sum(item.estimated_cost_yuan for item in scenarios), 6
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
        )
        for run, case_no, user_name in rows
    ]
