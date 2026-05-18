from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.questionnaire import QuestionnaireSubmission
from app.models.checkin import Checkin
from app.models.message import Message, MessageDirection
from app.models.message import MessagePurpose, MessageSenderType, MessageStatus
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user import User
from app.models.user_document import DocumentStatus, DocumentType, UserDocument
from app.models.user_profile import UserProfile
from app.services.ai_service import AIService
from app.services.docx_service import (
    render_template_markdown,
    safe_filename,
    write_markdown_docx,
    write_template_docx,
)
from app.services.document_templates import (
    build_stage_report_content_json,
    build_document_content_json,
    normalize_content_json,
    prompt_schema_for,
    render_document_content,
    stage_report_prompt_schema,
)
from app.services.content_policy import get_content_delivery_mode
from app.services.manual_prompt import get_manual_prompt
from app.services.questionnaire_template import get_active_questionnaire_fields
from app.services.channels import send_channel_document, send_channel_text
from app.services.risk_service import is_high_risk


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
        fields_payload = await _generate_document_payload(
            session=session,
            ai=ai,
            service_case=service_case,
            user_name=user_name,
            document_type=document_type,
            title=title,
            submission=submission,
            latest_profile=latest_profile,
        )
        content_json = build_document_content_json(
            document_type=document_type,
            title=title,
            user_name=user_name,
            fields=fields_payload,
        )
        content = render_document_content(content_json)
        if not _document_content_is_sufficient(content):
            content = await ai.generate_document(
                document_type=document_type.value,
                title=title,
                answers=submission.answers_json,
                profile=latest_profile.profile_json if latest_profile else None,
            )
            if not _document_content_is_sufficient(content):
                raise RuntimeError(f"{title} generated empty content")
            content_json = None
        file_path = _write_document_file(
            service_case=service_case,
            user_name=user_name,
            document_type=document_type,
            title=title,
            content=content,
            content_json=content_json,
        )
        document = UserDocument(
            service_case_id=service_case.id,
            document_type=document_type,
            title=title,
            content_md=content,
            content_json=content_json,
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
    if auto_send or await _should_auto_send(session, service_case):
        await send_case_documents(session, service_case.id)
    return documents


async def generate_stage_report(
    session: AsyncSession,
    service_case: ServiceCase,
    *,
    stage: str,
) -> UserDocument:
    if stage not in {"day3", "day7", "day14", "day21", "day28", "graduation"}:
        raise RuntimeError("invalid stage")
    existing = await _existing_stage_report(session, service_case.id, stage)
    if existing:
        return existing

    user_name = await _user_name(session, service_case)
    latest_profile = await session.scalar(
        select(UserProfile)
        .where(UserProfile.service_case_id == service_case.id)
        .order_by(UserProfile.created_at.desc())
        .limit(1)
    )
    checkins = list(
        await session.scalars(
            select(Checkin)
            .where(Checkin.service_case_id == service_case.id)
            .order_by(Checkin.checkin_date.asc(), Checkin.created_at.asc())
        )
    )
    messages = list(
        await session.scalars(
            select(Message)
            .where(Message.service_case_id == service_case.id)
            .order_by(Message.created_at.asc())
        )
    )
    title = _stage_title(stage)
    ai = AIService()
    fields_payload = await _generate_stage_payload(
        ai=ai,
        service_case=service_case,
        user_name=user_name,
        stage=stage,
        title=title,
        latest_profile=latest_profile,
        checkins=checkins,
        messages=messages,
    )
    content_json = build_stage_report_content_json(
        stage=stage,
        title=title,
        user_name=user_name,
        fields=fields_payload,
    )
    content = render_document_content(content_json)
    document_type = (
        DocumentType.GRADUATION_REPORT
        if stage == "graduation"
        else DocumentType.STAGE_REPORT
    )
    file_path = _write_document_file(
        service_case=service_case,
        user_name=user_name,
        document_type=document_type,
        title=title,
        content=content,
        content_json=content_json,
    )
    document = UserDocument(
        service_case_id=service_case.id,
        document_type=document_type,
        title=title,
        content_md=content,
        content_json=content_json,
        file_path=str(file_path),
        status=DocumentStatus.REVIEWING,
        generated_by_model=ai.settings.ai_model,
    )
    session.add(document)
    await session.commit()
    await session.refresh(document)
    if await _should_auto_send(session, service_case):
        await send_one_document(session, document)
    return document


async def rewrite_document_file(session: AsyncSession, document: UserDocument) -> UserDocument:
    service_case = await session.get(ServiceCase, document.service_case_id)
    if not service_case:
        raise RuntimeError("service case not found")
    user_name = await _user_name(session, service_case)
    if document.content_json:
        document.content_json = normalize_content_json(document.content_json)
        document.content_md = render_template_markdown(document.content_json)
    document.file_path = str(
        _write_document_file(
            service_case=service_case,
            user_name=user_name,
            document_type=document.document_type,
            title=document.title,
            content=document.content_md,
            content_json=document.content_json,
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
    intro_result = await send_channel_text(service_case.group_id, intro)
    session.add(
        Message(
            service_case_id=service_case.id,
            group_id=service_case.group_id,
            wecom_message_id=intro_result.message_id,
            direction=MessageDirection.OUTBOUND,
            sender_type=MessageSenderType.BOT,
            message_type="markdown",
            content=intro,
            purpose=MessagePurpose.DOCUMENT_DELIVERY,
            status=MessageStatus.SENT if intro_result.success else MessageStatus.FAILED,
            error=intro_result.error,
        )
    )
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
        _record_document_delivery(session, service_case, document, result.message_id)
    service_case.status = ServiceCaseStatus.IN_SERVICE
    service_case.started_at = service_case.started_at or _utc_now()
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
    _record_document_delivery(session, service_case, document, result.message_id)
    await _mark_case_in_service_if_documents_sent(session, service_case)
    await session.commit()
    await session.refresh(document)
    return document


def _record_document_delivery(
    session: AsyncSession,
    service_case: ServiceCase,
    document: UserDocument,
    message_id: str | None,
) -> None:
    session.add(
        Message(
            service_case_id=service_case.id,
            group_id=service_case.group_id,
            wecom_message_id=message_id,
            direction=MessageDirection.OUTBOUND,
            sender_type=MessageSenderType.BOT,
            message_type="document",
            content=f"已发送文件：{document.title}.docx",
            purpose=MessagePurpose.DOCUMENT_DELIVERY,
            status=MessageStatus.SENT,
        )
    )


async def _should_auto_send(session: AsyncSession, service_case: ServiceCase) -> bool:
    return await get_content_delivery_mode(session) == "auto_send" and not is_high_risk(service_case)


async def _mark_case_in_service_if_documents_sent(
    session: AsyncSession,
    service_case: ServiceCase,
) -> None:
    documents = list(
        await session.scalars(
            select(UserDocument)
            .where(UserDocument.service_case_id == service_case.id)
            .order_by(UserDocument.created_at.desc())
        )
    )
    latest_by_type: dict[DocumentType, UserDocument] = {}
    for item in documents:
        latest_by_type.setdefault(item.document_type, item)
    latest_documents = [
        latest_by_type[document_type]
        for document_type in DOCUMENT_DEFINITIONS
        if document_type in latest_by_type
    ]
    if len(latest_documents) == len(DOCUMENT_DEFINITIONS) and all(
        item.status == DocumentStatus.SENT for item in latest_documents
    ):
        service_case.status = ServiceCaseStatus.IN_SERVICE
        service_case.started_at = service_case.started_at or _utc_now()


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
    fields = await get_active_questionnaire_fields(session, "intake")
    readable_answers = _readable_answers(fields, submission.answers_json)
    prompt = get_manual_prompt()
    text = await ai.generate_text(
        system_prompt=(
            prompt
            + "\n\n现在不是批量处理，请只为当前这一位用户生成指定的一份文件。"
            + "不要输出整篇文章，只输出用于填入 docx 模板占位符的 JSON 对象。"
            + "不要输出解释、不要输出代码块。"
            + "所有健康相关判断都只能写成生活方式管理建议，不能写成诊断、治疗或疗效承诺。"
        ),
        user_prompt=(
            f"当前用户：{user_name}\n"
            f"服务单：{service_case.case_no}\n"
            f"需要生成的文件：{title}\n"
            f"document_type={document_type.value}\n\n"
            f"请严格按这个字段结构填值：\n{json.dumps(prompt_schema_for(document_type), ensure_ascii=False, indent=2)}\n\n"
            f"用户画像：\n{json.dumps(latest_profile.profile_json if latest_profile else {}, ensure_ascii=False, indent=2)}\n\n"
            f"问卷结果：\n{readable_answers}"
        ),
    )
    payload = _parse_json_object(text)
    payload.setdefault("recipient", user_name)
    payload.setdefault("summary", "")
    payload.setdefault("sections", [])
    return payload


async def _generate_stage_payload(
    *,
    ai: AIService,
    service_case: ServiceCase,
    user_name: str,
    stage: str,
    title: str,
    latest_profile: UserProfile | None,
    checkins: list[Checkin],
    messages: list[Message],
) -> dict:
    checkin_summary = [
        {
            "date": item.checkin_date.isoformat(),
            "content": item.content,
            "parsed": item.parsed_json or {},
        }
        for item in checkins
    ]
    inbound_messages = [
        {
            "created_at": item.created_at.isoformat(),
            "content": item.content,
        }
        for item in messages
        if item.direction == MessageDirection.INBOUND
    ][-20:]
    prompt = (
        "你是燃AI阶段报告撰写助手。只输出 JSON 对象，不要输出解释、不要输出代码块。"
        "必须使用生活方式管理表达，不做医疗诊断、治疗建议、用药建议或疗效承诺。"
        "复购建议要先围绕用户下一阶段目标，再温和提出不中断执行。"
    )
    try:
        text = await ai.generate_text(
            system_prompt=prompt,
            user_prompt=(
                f"用户：{user_name}\n服务单：{service_case.case_no}\n报告：{title}\n阶段：{stage}\n"
                f"请严格按字段结构填值：\n{json.dumps(stage_report_prompt_schema(stage), ensure_ascii=False, indent=2)}\n\n"
                f"用户画像：\n{json.dumps(latest_profile.profile_json if latest_profile else {}, ensure_ascii=False, indent=2)}\n\n"
                f"打卡记录：\n{json.dumps(checkin_summary, ensure_ascii=False, indent=2)}\n\n"
                f"最近用户消息：\n{json.dumps(inbound_messages, ensure_ascii=False, indent=2)}"
            ),
        )
        payload = _parse_json_object(text)
    except Exception:
        payload = {}
    return _fallback_stage_payload(stage, payload, checkins)


def _fallback_stage_payload(stage: str, payload: dict, checkins: list[Checkin]) -> dict:
    checkin_days = len({item.checkin_date for item in checkins})
    latest = checkins[-1].parsed_json if checkins and checkins[-1].parsed_json else {}
    if stage == "graduation":
        payload.setdefault("weight_change", _weight_change(checkins) or "暂无完整体重变化数据。")
        payload.setdefault("rhythm_change", f"已记录 {checkin_days} 天打卡，节律变化以睡眠、饮水、运动记录为主要参考。")
        payload.setdefault("execution_rate", f"当前打卡天数 {checkin_days} 天，执行率需结合服务周期人工复核。")
        payload.setdefault("growth_summary", "用户已完成一个阶段的执行反馈，建议继续沉淀稳定动作。")
        payload.setdefault("next_route", "下一阶段优先保持打卡、稳定睡眠和晚间收口，再逐步提高运动一致性。")
    else:
        payload.setdefault("stage_summary", f"本阶段已记录 {checkin_days} 天打卡，最新反馈：{json.dumps(latest, ensure_ascii=False) if latest else '暂无结构化反馈'}。")
        payload.setdefault("risk_reminder", "如出现胸闷、心悸、严重失眠、过敏等明显不适，请及时联系班主任并建议就医。")
        payload.setdefault("adjustment_advice", "继续守住早C/午B/晚A、饮水、睡眠、情绪、运动和体重记录六项基础动作。")
        payload.setdefault("repurchase_advice", "如果执行节奏已经稳定，可以提前准备下一阶段，避免中断已建立的节律。")
    return payload


def _weight_change(checkins: list[Checkin]) -> str:
    weights: list[float] = []
    for item in checkins:
        value = (item.parsed_json or {}).get("weight")
        if not value:
            continue
        match = __import__("re").search(r"\d+(?:\.\d+)?", str(value))
        if match:
            weights.append(float(match.group()))
    if len(weights) < 2:
        return ""
    delta = round(weights[-1] - weights[0], 1)
    return f"初始记录 {weights[0]}，最新记录 {weights[-1]}，变化 {delta:+}。"


async def _existing_stage_report(
    session: AsyncSession,
    service_case_id: int,
    stage: str,
) -> UserDocument | None:
    documents = list(
        await session.scalars(
            select(UserDocument)
            .where(
                UserDocument.service_case_id == service_case_id,
                UserDocument.document_type.in_([DocumentType.STAGE_REPORT, DocumentType.GRADUATION_REPORT]),
            )
            .order_by(UserDocument.created_at.desc())
        )
    )
    for document in documents:
        if isinstance(document.content_json, dict) and document.content_json.get("stage") == stage:
            return document
    return None


def _stage_title(stage: str) -> str:
    labels = {
        "day3": "3天阶段报告",
        "day7": "7天阶段报告",
        "day14": "14天阶段报告",
        "day21": "21天阶段报告",
        "day28": "28天阶段报告",
        "graduation": "毕业报告",
    }
    return labels[stage]


def _write_document_file(
    *,
    service_case: ServiceCase,
    user_name: str,
    document_type: DocumentType,
    title: str,
    content: str,
    content_json: dict | None = None,
) -> Path:
    settings = get_settings()
    suffix = "女士" if _is_female_guess(content) else "先生"
    version = datetime.now().strftime("%Y%m%d%H%M%S")
    filename = safe_filename(f"{user_name} {title} {user_name}{suffix} {version}.docx")
    output_path = Path(settings.document_output_dir) / service_case.case_no / document_type.value / filename
    if content_json:
        write_template_docx(content_json, output_path)
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
    text = _strip_code_fence(text)
    try:
        value = json.loads(text)
        return _normalize_json_payload(value, text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                value = json.loads(text[start : end + 1])
                return _normalize_json_payload(value, text)
            except json.JSONDecodeError:
                pass
    return {"summary": text, "sections": [{"heading": "正文", "body": text, "bullets": []}]}


def _normalize_json_payload(value, raw_text: str) -> dict:
    if isinstance(value, str):
        nested = _parse_nested_json(value)
        if nested:
            return nested
        return {"summary": value, "sections": [{"heading": "正文", "body": value, "bullets": []}]}
    if not isinstance(value, dict):
        return {"summary": raw_text, "sections": []}

    for key in ("summary", "body", "content"):
        nested = _parse_nested_json(value.get(key))
        if nested:
            return nested
    return value


def _parse_nested_json(value) -> dict | None:
    if not isinstance(value, str):
        return None
    text = _strip_code_fence(value).strip()
    if not text.startswith("{"):
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _strip_code_fence(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return text


def _document_content_is_sufficient(content: str) -> bool:
    plain = content.replace("Powered by HumanBurning.AI", "")
    plain = plain.replace("#", "").replace("To：", "").strip()
    if len(plain) < 500:
        return False
    if '  "recipient":' in content or '"sections": [' in content:
        return False
    return True


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
        .where(
            QuestionnaireSubmission.service_case_id == service_case_id,
            QuestionnaireSubmission.template_key == "intake",
        )
        .order_by(QuestionnaireSubmission.created_at.desc())
        .limit(1)
    )
