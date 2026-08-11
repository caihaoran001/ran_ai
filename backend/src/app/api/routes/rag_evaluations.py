from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from app.schemas.rag_evaluation import (
    AnswerRelevancyCalibrationReport,
    RagEvaluationRunDetail,
    RagEvaluationRunList,
)
from app.services.rag_evaluation_reports import (
    RagEvaluationReportInvalid,
    RagEvaluationReportNotFound,
    RagEvaluationReportStore,
)


router = APIRouter()


def get_report_store() -> RagEvaluationReportStore:
    return RagEvaluationReportStore()


@router.get("", response_model=RagEvaluationRunList)
async def list_rag_evaluations(
    store: RagEvaluationReportStore = Depends(get_report_store),
) -> RagEvaluationRunList:
    return RagEvaluationRunList(items=await asyncio.to_thread(store.list_runs))


@router.get(
    "/calibrations/answer-relevancy",
    response_model=AnswerRelevancyCalibrationReport | None,
)
async def get_answer_relevancy_calibration(
    store: RagEvaluationReportStore = Depends(get_report_store),
) -> AnswerRelevancyCalibrationReport | None:
    try:
        return await asyncio.to_thread(store.get_answer_relevancy_calibration)
    except RagEvaluationReportInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{run_id}", response_model=RagEvaluationRunDetail)
async def get_rag_evaluation(
    run_id: str,
    store: RagEvaluationReportStore = Depends(get_report_store),
) -> RagEvaluationRunDetail:
    try:
        return await asyncio.to_thread(store.get_run, run_id)
    except RagEvaluationReportNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RagEvaluationReportInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
