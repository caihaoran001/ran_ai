from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from app.schemas.reply_agent_evaluation import (
    ReplyAgentEvaluationRunDetail,
    ReplyAgentEvaluationRunList,
)
from app.services.reply_agent_evaluation_reports import (
    ReplyAgentEvaluationReportInvalid,
    ReplyAgentEvaluationReportNotFound,
    ReplyAgentEvaluationReportStore,
)


router = APIRouter()


def get_report_store() -> ReplyAgentEvaluationReportStore:
    return ReplyAgentEvaluationReportStore()


@router.get("", response_model=ReplyAgentEvaluationRunList)
async def list_reply_agent_evaluations(
    store: ReplyAgentEvaluationReportStore = Depends(get_report_store),
) -> ReplyAgentEvaluationRunList:
    return ReplyAgentEvaluationRunList(items=await asyncio.to_thread(store.list_runs))


@router.get("/{run_id}", response_model=ReplyAgentEvaluationRunDetail)
async def get_reply_agent_evaluation(
    run_id: str,
    store: ReplyAgentEvaluationReportStore = Depends(get_report_store),
) -> ReplyAgentEvaluationRunDetail:
    try:
        return await asyncio.to_thread(store.get_run, run_id)
    except ReplyAgentEvaluationReportNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ReplyAgentEvaluationReportInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
