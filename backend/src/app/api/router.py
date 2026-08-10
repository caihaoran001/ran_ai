from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import (
    agent_operations,
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
    rag_evaluations,
    service_cases,
    settings,
    short_links,
    users,
)

api_router = APIRouter()
api_router.include_router(
    agent_operations.router,
    prefix="/agent-operations",
    tags=["agent-operations"],
)
api_router.include_router(
    rag_evaluations.router,
    prefix="/rag-evaluations",
    tags=["rag-evaluations"],
)
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
api_router.include_router(short_links.router, tags=["short-links"])
