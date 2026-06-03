from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import (
    checkins,
    dashboard,
    diagnostics,
    documents,
    events,
    knowledge_drafts,
    knowledge_faqs,
    messages,
    outcomes,
    qiwe,
    questionnaire_templates,
    questionnaires,
    service_cases,
    settings,
    users,
)

api_router = APIRouter()
api_router.include_router(service_cases.router, prefix="/service-cases", tags=["service-cases"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(questionnaires.router, prefix="/questionnaires", tags=["questionnaires"])
api_router.include_router(questionnaire_templates.router, prefix="/questionnaire-template", tags=["questionnaire-template"])
api_router.include_router(documents.router, tags=["documents"])
api_router.include_router(knowledge_drafts.router, tags=["knowledge-drafts"])
api_router.include_router(knowledge_faqs.router, tags=["knowledge-faqs"])
api_router.include_router(checkins.router, tags=["checkins"])
api_router.include_router(messages.router, tags=["messages"])
api_router.include_router(outcomes.router, tags=["outcomes"])
api_router.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
api_router.include_router(diagnostics.router, prefix="/diagnostics", tags=["diagnostics"])
api_router.include_router(events.router, tags=["events"])
api_router.include_router(qiwe.router, tags=["qiwe"])
api_router.include_router(settings.router, tags=["settings"])
