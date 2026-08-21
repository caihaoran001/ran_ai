from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from app.schemas.agent_evaluation import (
    AgentEvaluationRunDetail,
    AgentEvaluationRunList,
)
from app.services.agent_evaluation_reports import (
    AgentEvaluationReportInvalid,
    AgentEvaluationReportNotFound,
    AgentEvaluationReportStore,
)


router = APIRouter()


def get_report_store() -> AgentEvaluationReportStore:
    return AgentEvaluationReportStore()


@router.get("", response_model=AgentEvaluationRunList)
async def list_agent_evaluations(
    store: AgentEvaluationReportStore = Depends(get_report_store),
) -> AgentEvaluationRunList:
    return AgentEvaluationRunList(items=await asyncio.to_thread(store.list_runs))


@router.get("/{run_id}", response_model=AgentEvaluationRunDetail)
async def get_agent_evaluation(
    run_id: str,
    store: AgentEvaluationReportStore = Depends(get_report_store),
) -> AgentEvaluationRunDetail:
    try:
        return await asyncio.to_thread(store.get_run, run_id)
    except AgentEvaluationReportNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AgentEvaluationReportInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
