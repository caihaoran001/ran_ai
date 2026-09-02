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
    DocumentType.ENERGY_RESTART_PRESCRIPTION: "个人能量重启诊断报告",
    DocumentType.DAY28_EXECUTION_STANDARD: "28天高燃人生执行建议书",
}

DOCUMENT_DEFINITIONS_EN = {
    DocumentType.ENERGY_RESTART_PRESCRIPTION: "Personal Energy Reboot Diagnostic Report",
    DocumentType.DAY28_EXECUTION_STANDARD: "28-Day HumanBurning Action Plan",
    DocumentType.STAGE_REPORT: "Stage Progress Report",
}


def get_document_title(doc_type: DocumentType, language: str = "zh") -> str:
    if language == "en":
        return DOCUMENT_DEFINITIONS_EN.get(doc_type, doc_type.value)
    return DOCUMENT_DEFINITIONS.get(doc_type, doc_type.value)


async def _user_name(session: AsyncSession, service_case: ServiceCase) -> str:
    submission = await _latest_submission(session, service_case.id)
    if submission:
        case_lang = getattr(service_case, "language", "zh") or "zh"
        fields = await get_active_questionnaire_fields(session, "intake", language=case_lang)
        facts = build_intake_fact_summary(fields, submission.answers_json)
        preferred_name = str(facts.get("preferred_name") or "").strip()
        if preferred_name and preferred_name not in ("未提供", "N/A"):
            return preferred_name
        legal_name = str(facts.get("legal_name") or "").strip()
        if legal_name and legal_name not in ("未提供", "N/A"):
            return legal_name
    if not service_case.user_id:
        return "未提供"
    user = await session.get(User, service_case.user_id)
    return user.name if user else "未提供"


async def _recipient_name_and_gender(
    session: AsyncSession, service_case: ServiceCase
) -> tuple[str, str]:
    """确定性称呼 + 性别，与诊断报告/执行建议书同源（入营问卷「性别」字段）。

    - 称呼：男/女时带「先生/女士」后缀（如「小明 先生」），性别未提供时不带后缀。
    - 性别：返回「男」「女」或「未提供」。
    供阶段报告提示词与交付文件名使用，避免大模型或关键词各自瞎猜导致前后不一致。
    """
    submission = await _latest_submission(session, service_case.id)
    if submission:
        case_lang = getattr(service_case, "language", "zh") or "zh"
        fields = await get_active_questionnaire_fields(session, "intake", language=case_lang)
        facts = build_intake_fact_summary(fields, submission.answers_json)
        recipient = str(facts.get("recipient_name") or "").strip()
        gender = str(facts.get("gender") or "").strip() or "未提供"
        if recipient and recipient not in ("未提供", "N/A"):
            return recipient, gender
    name = await _user_name(session, service_case)
    return name, "未提供"


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
