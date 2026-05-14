from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.questionnaire import QuestionnaireSubmission
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user import User
from app.models.user_document import DocumentStatus, DocumentType, UserDocument
from app.models.user_profile import UserProfile
from app.services.ai_service import AIService
from app.services.docx_service import (
    render_structured_markdown,
    safe_filename,
    write_markdown_docx,
    write_structured_docx,
)
from app.services.manual_prompt import get_manual_prompt
from app.services.questionnaire_template import get_active_questionnaire_fields
from app.services.channels import send_channel_document, send_channel_text


DOCUMENT_DEFINITIONS = {
    DocumentType.ENERGY_RESTART_PRESCRIPTION: "个人能量重启处方单",
    DocumentType.DAY28_EXECUTION_STANDARD: "28天执行标准与打卡要求",
}


async def generate_profile(session: AsyncSession, service_case: ServiceCase) -> UserProfile:
    submission = await _latest_submission(session, service_case.id)
    if not submission:
        raise RuntimeError("questionnaire submission is required before generating profile")

    ai = AIService()
    profile_json, summary = await ai.generate_profile(submission.answers_json)
    profile = UserProfile(
        service_case_id=service_case.id,
        profile_json=profile_json,
        summary=summary,
        generated_by_model=ai.settings.ai_model,
    )
    session.add(profile)
    service_case.status = ServiceCaseStatus.DOCUMENTS_GENERATED
    await session.commit()
    await session.refresh(profile)
    return profile


async def generate_documents(
    session: AsyncSession, service_case: ServiceCase, *, auto_send: bool = False
) -> list[UserDocument]:
    submission = await _latest_submission(session, service_case.id)
    if not submission:
        raise RuntimeError("questionnaire submission is required before generating documents")

    latest_profile = await session.scalar(
        select(UserProfile)
        .where(UserProfile.service_case_id == service_case.id)
        .order_by(UserProfile.created_at.desc())
        .limit(1)
    )
    user_name = await _user_name(session, service_case)
    ai = AIService()
    documents: list[UserDocument] = []
    for document_type, title in DOCUMENT_DEFINITIONS.items():
        payload = await _generate_document_payload(
            session=session,
            ai=ai,
            service_case=service_case,
            user_name=user_name,
            document_type=document_type,
            title=title,
            submission=submission,
            latest_profile=latest_profile,
        )
        content = render_structured_markdown(title, payload)
        file_path = _write_document_file(
            service_case=service_case,
            user_name=user_name,
            document_type=document_type,
            title=title,
            content=content,
            payload=payload,
        )
        document = UserDocument(
            service_case_id=service_case.id,
            document_type=document_type,
            title=title,
            content_md=content,
            file_path=str(file_path),
            status=DocumentStatus.REVIEWING,
            generated_by_model=ai.settings.ai_model,
        )
        session.add(document)
        documents.append(document)
    service_case.status = ServiceCaseStatus.REVIEWING
    await session.commit()
    for document in documents:
        await session.refresh(document)
    if auto_send:
        await send_case_documents(session, service_case.id)
    return documents


async def rewrite_document_file(session: AsyncSession, document: UserDocument) -> UserDocument:
    service_case = await session.get(ServiceCase, document.service_case_id)
    if not service_case:
        raise RuntimeError("service case not found")
    user_name = await _user_name(session, service_case)
    document.file_path = str(
        _write_document_file(
            service_case=service_case,
            user_name=user_name,
            document_type=document.document_type,
            title=document.title,
            content=document.content_md,
        )
    )
    await session.commit()
    await session.refresh(document)
    return document


async def send_case_documents(session: AsyncSession, service_case_id: int) -> list[UserDocument]:
    service_case = await session.get(ServiceCase, service_case_id)
    if not service_case:
        raise RuntimeError("service case not found")
    documents = list(
        await session.scalars(
            select(UserDocument)
            .where(UserDocument.service_case_id == service_case_id)
            .order_by(UserDocument.created_at.desc())
        )
    )
    latest_by_type: dict[DocumentType, UserDocument] = {}
    for document in documents:
        latest_by_type.setdefault(document.document_type, document)
    documents = [
        latest_by_type[document_type]
        for document_type in DOCUMENT_DEFINITIONS
        if document_type in latest_by_type
    ]
    if not documents:
        raise RuntimeError("documents are required before sending")
    if any(not document.file_path for document in documents):
        raise RuntimeError("document file is missing")

    intro = "两份专属文件已生成，请查收：\n1.《个人能量重启处方单》\n2.《28 天执行标准与打卡要求》"
    await send_channel_text(service_case.group_id, intro)
    for document in documents:
        result = await send_channel_document(
            service_case.group_id,
            document.file_path or "",
            caption=f"{document.title}.docx",
        )
        if not result.success:
            raise RuntimeError(result.error or f"failed to send {document.title}")
        document.status = DocumentStatus.SENT
        document.sent_at = _utc_now()
    service_case.status = ServiceCaseStatus.IN_SERVICE
    await session.commit()
    for document in documents:
        await session.refresh(document)
    return documents


async def send_one_document(session: AsyncSession, document: UserDocument) -> UserDocument:
    service_case = await session.get(ServiceCase, document.service_case_id)
    if not service_case:
        raise RuntimeError("service case not found")
    if not document.file_path:
        raise RuntimeError("document file is missing")
    result = await send_channel_document(
        service_case.group_id,
        document.file_path,
        caption=f"{document.title}.docx",
    )
    if not result.success:
        raise RuntimeError(result.error or f"failed to send {document.title}")
    document.status = DocumentStatus.SENT
    document.sent_at = _utc_now()
    await session.commit()
    await session.refresh(document)
    return document


async def _generate_document_payload(
    *,
    session: AsyncSession,
    ai: AIService,
    service_case: ServiceCase,
    user_name: str,
    document_type: DocumentType,
    title: str,
    submission: QuestionnaireSubmission,
    latest_profile: UserProfile | None,
) -> dict:
    fields = await get_active_questionnaire_fields(session)
    readable_answers = _readable_answers(fields, submission.answers_json)
    prompt = get_manual_prompt()
    text = await ai.generate_text(
        system_prompt=(
            prompt
            + "\n\n现在不是批量处理，请只为当前这一位用户生成指定的一份文件。"
            + "不要输出整篇文章。只输出 JSON 对象，不要输出解释、不要输出代码块。"
            + "JSON schema: {\"recipient\":\"\", \"subtitle\":\"\", \"summary\":\"\", "
            + "\"overview\":{}, \"sections\":[{\"heading\":\"\", \"body\":\"\", \"bullets\":[]}], "
            + "\"actions\":[], \"reminders\":[]}。"
            + "sections 是文档主体模块，body 可以是字符串；需要列表时放 bullets。"
        ),
        user_prompt=(
            f"当前用户：{user_name}\n"
            f"服务单：{service_case.case_no}\n"
            f"需要生成的文件：{title}\n"
            f"document_type={document_type.value}\n\n"
            f"用户画像：\n{json.dumps(latest_profile.profile_json if latest_profile else {}, ensure_ascii=False, indent=2)}\n\n"
            f"问卷结果：\n{readable_answers}"
        ),
    )
    payload = _parse_json_object(text)
    payload.setdefault("recipient", user_name)
    payload.setdefault("summary", "")
    payload.setdefault("sections", [])
    return payload


def _write_document_file(
    *,
    service_case: ServiceCase,
    user_name: str,
    document_type: DocumentType,
    title: str,
    content: str,
    payload: dict | None = None,
) -> Path:
    settings = get_settings()
    suffix = "女士" if _is_female_guess(content) else "先生"
    version = datetime.now().strftime("%Y%m%d%H%M%S")
    filename = safe_filename(f"{user_name} {title} {user_name}{suffix} {version}.docx")
    output_path = Path(settings.document_output_dir) / service_case.case_no / document_type.value / filename
    if payload:
        write_structured_docx(title, payload, output_path)
    else:
        write_markdown_docx(content, output_path)
    return output_path


def _readable_answers(fields: list[dict], answers: dict) -> str:
    title_by_key = {field.get("key") or field.get("id"): field.get("title") or field.get("label") for field in fields}
    lines: list[str] = []
    for key, value in answers.items():
        title = title_by_key.get(key, key)
        if isinstance(value, list):
            display = "、".join(str(item) for item in value)
        else:
            display = str(value).replace("||", "、")
        lines.append(f"- {title}: {display or '未提供'}")
    return "\n".join(lines)


def _is_female_guess(content: str) -> bool:
    return "女性" in content or "女士" in content


def _parse_json_object(text: str) -> dict:
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {"summary": text, "sections": []}
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                value = json.loads(text[start : end + 1])
                return value if isinstance(value, dict) else {"summary": text, "sections": []}
            except json.JSONDecodeError:
                pass
    return {"summary": text, "sections": [{"heading": "正文", "body": text, "bullets": []}]}


def _utc_now():
    from datetime import timezone

    return datetime.now(timezone.utc)


async def _user_name(session: AsyncSession, service_case: ServiceCase) -> str:
    if not service_case.user_id:
        return "未提供"
    user = await session.get(User, service_case.user_id)
    return user.name if user else "未提供"


async def _latest_submission(
    session: AsyncSession, service_case_id: int
) -> QuestionnaireSubmission | None:
    return await session.scalar(
        select(QuestionnaireSubmission)
        .where(QuestionnaireSubmission.service_case_id == service_case_id)
        .order_by(QuestionnaireSubmission.created_at.desc())
        .limit(1)
    )
