from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from app.schemas.archivist_evaluation import (
    ArchivistEvaluationRunDetail,
    ArchivistEvaluationRunList,
)
from app.services.archivist_evaluation_reports import (
    ArchivistEvaluationReportInvalid,
    ArchivistEvaluationReportNotFound,
    ArchivistEvaluationReportStore,
)


router = APIRouter()


def get_report_store() -> ArchivistEvaluationReportStore:
    return ArchivistEvaluationReportStore()


@router.get("", response_model=ArchivistEvaluationRunList)
async def list_archivist_evaluations(
    store: ArchivistEvaluationReportStore = Depends(get_report_store),
) -> ArchivistEvaluationRunList:
    return ArchivistEvaluationRunList(items=await asyncio.to_thread(store.list_runs))


@router.get("/{run_id}", response_model=ArchivistEvaluationRunDetail)
async def get_archivist_evaluation(
    run_id: str,
    store: ArchivistEvaluationReportStore = Depends(get_report_store),
) -> ArchivistEvaluationRunDetail:
    try:
        return await asyncio.to_thread(store.get_run, run_id)
    except ArchivistEvaluationReportNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ArchivistEvaluationReportInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
