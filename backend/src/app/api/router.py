from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import (
    checkins,
    dashboard,
    documents,
    messages,
    questionnaire_templates,
    questionnaires,
    service_cases,
    users,
    wecom,
)

api_router = APIRouter()
api_router.include_router(service_cases.router, prefix="/service-cases", tags=["service-cases"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(questionnaires.router, prefix="/questionnaires", tags=["questionnaires"])
api_router.include_router(questionnaire_templates.router, prefix="/questionnaire-template", tags=["questionnaire-template"])
api_router.include_router(documents.router, tags=["documents"])
api_router.include_router(checkins.router, tags=["checkins"])
api_router.include_router(messages.router, tags=["messages"])
api_router.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
api_router.include_router(wecom.router, tags=["wecom"])
