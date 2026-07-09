from __future__ import annotations

"""交付文档模块的共享定义：文档类型表 + 用户名/问卷提交查询。

抽出到独立模块以避免 document_service 与 document_delivery 的循环依赖。
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.questionnaire import QuestionnaireSubmission
from app.models.service_case import ServiceCase
from app.models.user import User
from app.models.user_document import DocumentType
from app.services.intake_facts import build_intake_fact_summary
from app.services.questionnaire_template import get_active_questionnaire_fields


DOCUMENT_DEFINITIONS = {
    DocumentType.ENERGY_RESTART_PRESCRIPTION: "个人能量重启处方单",
    DocumentType.DAY28_EXECUTION_STANDARD: "28天执行标准与打卡要求",
}


async def _user_name(session: AsyncSession, service_case: ServiceCase) -> str:
    submission = await _latest_submission(session, service_case.id)
    if submission:
        fields = await get_active_questionnaire_fields(session, "intake")
        facts = build_intake_fact_summary(fields, submission.answers_json)
        preferred_name = str(facts.get("preferred_name") or "").strip()
        if preferred_name and preferred_name != "未提供":
            return preferred_name
        legal_name = str(facts.get("legal_name") or "").strip()
        if legal_name and legal_name != "未提供":
            return legal_name
    if not service_case.user_id:
        return "未提供"
    user = await session.get(User, service_case.user_id)
    return user.name if user else "未提供"


async def _latest_submission(
    session: AsyncSession, service_case_id: int
) -> QuestionnaireSubmission | None:
    return await session.scalar(
        select(QuestionnaireSubmission)
        .where(
            QuestionnaireSubmission.service_case_id == service_case_id,
            QuestionnaireSubmission.template_key == "intake",
        )
        .order_by(QuestionnaireSubmission.created_at.desc())
        .limit(1)
    )
