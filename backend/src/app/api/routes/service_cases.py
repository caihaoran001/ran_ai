from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_session
from app.models.ai_usage import AIUsageLog
from app.models.checkin import Checkin
from app.models.message import Message, MessageDirection, MessageSenderType
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user import User
from app.schemas.service_case import (
    ServiceCaseAIContextPreview,
    ServiceCaseRead,
    ServiceCaseStats,
    ServiceCaseUpdate,
)
from app.services.agent.context_builder import build_agent_memory_context
from app.services.checkin_completion import complete_checkin_day_count
from app.services.plateau_detector import (
    plateau_followup_done_condition,
    plateau_followup_pending_condition,
    plateau_followup_resolved_condition,
)
from app.services.service_case_queries import (
    active_service_case_condition,
    get_active_service_case,
    get_service_case as get_service_case_any,
    intake_submitted_between_condition,
    last_user_inbound_subquery,
    select_active_service_cases,
)

router = APIRouter()


def _search_service_cases(statement, filter: str | None, q: str | None, group_type: str | None = None):
    """列表与计数共用同一套筛选条件，避免"总数和列表对不上"。"""
    statement = _apply_service_case_filter(statement, filter)
    if group_type:
        statement = statement.where(ServiceCase.group_type == group_type)
    if q and q.strip():
        # 按 用户名 / 手机号 / 群名 模糊搜索。outerjoin 保留未绑定用户但有群名的服务单。
        like = f"%{q.strip()}%"
        statement = statement.outerjoin(User, ServiceCase.user_id == User.id).where(
            or_(
                User.name.ilike(like),
                User.phone.ilike(like),
                ServiceCase.group_name.ilike(like),
            )
        )
    return statement


@router.get("", response_model=list[ServiceCaseRead])
async def list_service_cases(
    filter: str | None = None,
    q: str | None = None,
    group_type: Literal["new_group", "legacy"] | None = None,
    limit: int | None = Query(default=None, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> list[ServiceCase]:
    """支持分页：用户管理一次全量渲染近三百条时首屏要 5 秒，后台默认只取第一页。"""
    statement = (
        select_active_service_cases()
        .options(selectinload(ServiceCase.user))
        .order_by(ServiceCase.created_at.desc())
    )
    statement = _search_service_cases(statement, filter, q, group_type)
    if limit is not None:
        statement = statement.offset(offset).limit(limit)
    elif offset:
        statement = statement.offset(offset)
    cases = list(await session.scalars(statement))
    return cases


@router.get("/count")
async def count_service_cases(
    filter: str | None = None,
    q: str | None = None,
    group_type: Literal["new_group", "legacy"] | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict[str, int]:
    """只回条数，供前端显示"共 N 条"而不必拉全量列表。"""
    statement = _search_service_cases(
        select(func.count()).select_from(ServiceCase).where(active_service_case_condition()),
        filter,
        q,
        group_type,
    )
    return {"total": await session.scalar(statement) or 0}


@router.get("/{service_case_id}", response_model=ServiceCaseRead)
async def get_service_case(
    service_case_id: int,
    session: AsyncSession = Depends(get_session),
) -> ServiceCase:
    service_case = await get_service_case_any(session, service_case_id, options=(selectinload(ServiceCase.user),))
    if not service_case:
        raise HTTPException(status_code=404, detail="service case not found")
    return service_case


@router.get("/{service_case_id}/stats", response_model=ServiceCaseStats)
async def get_service_case_stats(
    service_case_id: int,
    session: AsyncSession = Depends(get_session),
) -> ServiceCaseStats:
    service_case = await get_service_case_any(session, service_case_id)
    if not service_case:
        raise HTTPException(status_code=404, detail="service case not found")

    # 只做聚合，别把整段消息/打卡搬进内存（活跃群一个月可上千条，还会和聊天记录接口重复捞）
    message_stats = await session.execute(
        select(
            func.count(Message.id),
            func.count(Message.id).filter(Message.direction == MessageDirection.INBOUND),
            func.count(Message.id).filter(Message.direction == MessageDirection.OUTBOUND),
            func.max(Message.created_at),
            func.max(Message.created_at).filter(Message.direction == MessageDirection.INBOUND),
            func.min(Message.created_at).filter(Message.direction == MessageDirection.INBOUND),
        ).where(Message.service_case_id == service_case_id)
    )
    (
        message_count,
        inbound_count,
        outbound_count,
        last_message_at,
        last_user_message_at,
        first_inbound_at,
    ) = message_stats.one()

    checkin_rows = list(
        await session.scalars(
            select(Checkin).where(
                Checkin.service_case_id == service_case_id,
                Checkin.deleted_at.is_(None),
            )
        )
    )
    checkin_count = len(checkin_rows)
    checkin_days = len({row.checkin_date for row in checkin_rows})
    complete_checkin_days = complete_checkin_day_count(checkin_rows)
    last_checkin_at = max(
        (row.created_at for row in checkin_rows),
        default=None,
    )

    now = datetime.now(timezone.utc)
    days = 1.0
    if first_inbound_at:
        started_at = first_inbound_at
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        days = max((now - started_at).total_seconds() / 86400, 1.0)
    ai_usage = await session.execute(
        select(
            func.count(AIUsageLog.id),
            func.coalesce(func.sum(AIUsageLog.total_tokens), 0),
            func.coalesce(func.sum(AIUsageLog.estimated_cost_yuan), 0.0),
            func.coalesce(
                func.sum(AIUsageLog.estimated_cost_yuan).filter(AIUsageLog.scenario == "qwen_vl_translate"),
                0.0,
            ),
            func.coalesce(
                func.sum(AIUsageLog.estimated_cost_yuan).filter(AIUsageLog.scenario != "qwen_vl_translate"),
                0.0,
            ),
        ).where(AIUsageLog.service_case_id == service_case_id)
    )
    ai_calls, ai_tokens, ai_cost, ai_vision_cost, ai_deepseek_cost = ai_usage.one()

    return ServiceCaseStats(
        message_count=message_count,
        inbound_message_count=inbound_count,
        outbound_message_count=outbound_count,
        avg_daily_inbound_messages=round(inbound_count / days, 2),
        last_message_at=last_message_at,
        last_user_message_at=last_user_message_at,
        checkin_count=checkin_count,
        checkin_days=checkin_days,
        complete_checkin_days=complete_checkin_days,
        last_checkin_at=last_checkin_at,
        ai_total_calls=ai_calls or 0,
        ai_total_tokens=ai_tokens or 0,
        ai_estimated_cost_yuan=round(float(ai_cost or 0), 6),
        ai_deepseek_cost_yuan=round(float(ai_deepseek_cost or 0), 6),
        ai_vision_cost_yuan=round(float(ai_vision_cost or 0), 6),
    )


@router.get("/{service_case_id}/ai-context-preview", response_model=ServiceCaseAIContextPreview)
async def get_service_case_ai_context_preview(
    service_case_id: int,
    session: AsyncSession = Depends(get_session),
) -> ServiceCaseAIContextPreview:
    service_case = await get_service_case_any(session, service_case_id)
    if not service_case:
        raise HTTPException(status_code=404, detail="service case not found")
    # 预览 AI 每轮回复真实携带的「隐性用户背景」（profile_memory：稳定事实/历程小结/偏好/
    # 习惯/当前状态/最近话题/风险边界）。全量渲染，不做 agent 端的按需截断。
    memory = await build_agent_memory_context(
        session, service_case_id=service_case.id
    )
    context = f"### AI 每轮携带的隐性用户背景\n{memory}"
    return ServiceCaseAIContextPreview(context=context)


@router.patch("/{service_case_id}", response_model=ServiceCaseRead)
async def update_service_case(
    service_case_id: int,
    payload: ServiceCaseUpdate,
    session: AsyncSession = Depends(get_session),
) -> ServiceCase:
    service_case = await get_active_service_case(session, service_case_id)
    if not service_case:
        raise HTTPException(status_code=404, detail="service case not found")
    data = payload.model_dump(exclude_unset=True)
    if data.pop("reset_risk", False):
        service_case.risk_level = "normal"
        service_case.risk_flags = None
        service_case.intervention_reasons = None
    if "plateau_followed_up" in data:
        # 标记只记时间，不动判定结论；判定之后再变一次，这条标记自然过期回到待办。
        followed_up = data.pop("plateau_followed_up")
        service_case.plateau_followed_up_at = (
            datetime.now(timezone.utc) if followed_up else None
        )
    for key, value in data.items():
        setattr(service_case, key, value)
    await session.commit()
    await session.refresh(service_case)
    result = await get_active_service_case(session, service_case.id, options=(selectinload(ServiceCase.user),))
    assert result is not None
    return result


@router.delete("/{service_case_id}", response_model=ServiceCaseRead)
async def delete_service_case(
    service_case_id: int,
    session: AsyncSession = Depends(get_session),
) -> ServiceCase:
    service_case = await get_active_service_case(session, service_case_id, options=(selectinload(ServiceCase.user),))
    if not service_case:
        raise HTTPException(status_code=404, detail="service case not found")
    if not service_case.deleted_at:
        deleted_at = datetime.now(timezone.utc)
        service_case.deleted_at = deleted_at
        if service_case.user_id:
            active_sibling = await session.scalar(
                select(ServiceCase.id)
                .where(
                    ServiceCase.user_id == service_case.user_id,
                    ServiceCase.id != service_case.id,
                    active_service_case_condition(),
                )
                .limit(1)
            )
            if not active_sibling and service_case.user and not service_case.user.deleted_at:
                service_case.user.deleted_at = deleted_at
        await session.commit()
        await session.refresh(service_case)
    result = await session.scalar(
        select(ServiceCase)
        .where(ServiceCase.id == service_case.id)
        .options(selectinload(ServiceCase.user))
    )
    assert result is not None
    return result


def _apply_service_case_filter(statement, metric: str | None):
    if not metric:
        return statement

    now = datetime.now(timezone.utc)
    china_tz = ZoneInfo("Asia/Shanghai")
    today = datetime.now(china_tz).date()
    today_start = datetime.combine(today, time.min, tzinfo=china_tz).astimezone(timezone.utc)
    today_end = today_start + timedelta(days=1)

    if metric == "new_today":
        # 与看板「今日新增用户」同一个口径：今天交了入营问卷的才算新客户，
        # 否则点开卡片看到的名单会比数字多出一堆还没有客户的空群。
        return statement.where(intake_submitted_between_condition(today_start, today_end))
    if metric == "risk":
        return statement.where(ServiceCase.risk_level == "high")
    if metric == "plateau":
        return statement.where(ServiceCase.plateau_status == "plateau")
    if metric == "rebound":
        return statement.where(ServiceCase.plateau_status == "rebound")
    if metric == "plateau_followup":
        return statement.where(plateau_followup_pending_condition())
    if metric == "plateau_followup_done":
        return statement.where(plateau_followup_done_condition())
    if metric == "plateau_followup_resolved":
        return statement.where(plateau_followup_resolved_condition())
    if metric == "in_service":
        return statement.where(ServiceCase.status == ServiceCaseStatus.IN_SERVICE)
    if metric == "renewal_followup":
        return statement.where(
            ServiceCase.status == ServiceCaseStatus.IN_SERVICE,
            ServiceCase.started_at.is_not(None),
            ServiceCase.started_at <= now - timedelta(days=18),
            ServiceCase.started_at >= now - timedelta(days=28),
        )
    if metric == "renewal_intent":
        return statement.where(ServiceCase.renewal_intent_flags.is_not(None))
    if metric == "refund_intent":
        return statement.where(ServiceCase.refund_intent_flags.is_not(None))
    if metric == "refund_pending":
        # 模型识别到"疑似已退款"、等待运营确认（尚未改成 REFUNDED、仍在正常服务）
        return statement.where(
            ServiceCase.refund_pending_confirmation.is_(True),
            ServiceCase.status != ServiceCaseStatus.REFUNDED,
        )
    if metric == "refunded":
        return statement.where(ServiceCase.status == ServiceCaseStatus.REFUNDED)
    if metric in {"unfollowed", "lost"}:
        # 只看「用户本人」的发言：班主任在群里跟进不代表用户还活着。
        last_message_subq = last_user_inbound_subquery()
        last_activity_at = func.coalesce(last_message_subq.c.last_at, ServiceCase.created_at)
        statement = statement.outerjoin(last_message_subq, last_message_subq.c.service_case_id == ServiceCase.id)
        if metric == "unfollowed":
            return statement.where(
                ServiceCase.status == ServiceCaseStatus.IN_SERVICE,
                last_activity_at < now - timedelta(days=2),
                last_activity_at >= now - timedelta(days=7),
            )
        return statement.where(
            ServiceCase.status == ServiceCaseStatus.IN_SERVICE,
            last_activity_at < now - timedelta(days=7),
        )
    if metric == "messaged_today":
        message_subq = (
            select(Message.service_case_id)
            .where(
                Message.created_at >= today_start,
                Message.created_at < today_end,
            )
            .distinct()
            .subquery()
        )
        return statement.join(message_subq, message_subq.c.service_case_id == ServiceCase.id)

    if metric == "active_24h":
        # 活跃用户：近 24 小时内「用户本人」发过言的服务单。
        # 只按 INBOUND 不够：班主任在群里说话同样是 INBOUND，会把没吭声的群也算成活跃。
        active_subq = (
            select(Message.service_case_id)
            .where(
                Message.direction == MessageDirection.INBOUND,
                Message.sender_type == MessageSenderType.USER,
                Message.created_at >= now - timedelta(hours=24),
            )
            .distinct()
            .subquery()
        )
        return statement.join(active_subq, active_subq.c.service_case_id == ServiceCase.id)

    return statement
