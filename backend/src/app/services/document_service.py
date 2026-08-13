from __future__ import annotations

"""交付文档编排层：串联入营建档、阶段报告，并发送。

具体实现拆分到同目录子模块；本文件保留编排流程 + AIService 实例化
（测试通过 patch 本模块的 AIService 注入 Mock LLM），并重新导出公共 API。
"""

import logging

from copy import deepcopy

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.checkin import Checkin
from app.models.message import Message
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user_document import DocumentStatus, DocumentType, UserDocument
from app.models.user_document_original import UserDocumentOriginal
from app.models.user_profile import UserProfile
from app.models.user_state_profile import UserStateProfile
from app.core.config import get_settings
from app.services.ai_service import AIService
from app.services.docx_service import render_template_markdown
from app.services.document_templates import (
    build_stage_report_content_json,
    build_document_content_json,
    normalize_content_json,
    render_document_content,
)
from app.services.user_state_profile import initialize_user_state_from_intake
from app.services.risk_service import is_high_risk
from app.services.intake_facts import customer_type_for_profile
from app.services.service_case_queries import get_active_service_case
from app.services.service_case_activation import set_pre_service_status_or_activate

from app.services.document_common import (
    DOCUMENT_DEFINITIONS,
    _latest_submission,
    _recipient_name_and_gender,
    _user_name,
)
from app.services.document_prompts import _intake_generation_system_prompt
from app.services.intake_generation import (
    _apply_intake_facts_to_document_payloads,
    _generate_intake_analysis_package,
    personalized_checkin_fields_from_document_payloads,
)
from app.services.stage_report import (
    STAGE_REPORT_STAGES,
    _existing_stage_report,
    _generate_stage_payload,
    _stage_title,
)
from app.services.document_delivery import (
    _should_auto_send,
    create_temporary_document_file,
    document_manual_review_hold,
    documents_manual_review_hold,
    send_case_documents,
    send_one_document,
)

logger = logging.getLogger(__name__)


def _actual_model_for(ai: AIService, scenario: str) -> str:
    last_model_for = getattr(ai, "last_model_for", None)
    if callable(last_model_for):
        return str(last_model_for(scenario))
    return str(ai._model_for(scenario))

__all__ = [
    "generate_profile",
    "generate_documents",
    "generate_profile_and_documents",
    "generate_stage_report",
    "retry_degraded_stage_report",
    "rewrite_document_file",
    "create_temporary_document_file",
    "document_manual_review_hold",
    "documents_manual_review_hold",
    "send_case_documents",
    "send_one_document",
    "personalized_checkin_fields_from_document_payloads",
    # 供测试导入的内部实现再导出（保持旧的导入路径稳定）
    "_intake_generation_system_prompt",
    "_apply_intake_facts_to_document_payloads",
    "_generate_stage_payload",
]


async def generate_profile(session: AsyncSession, service_case: ServiceCase) -> UserProfile:
    profile, _documents = await generate_profile_and_documents(session, service_case)
    return profile


async def generate_documents(
    session: AsyncSession, service_case: ServiceCase, *, auto_send: bool = False
) -> list[UserDocument]:
    _profile, documents = await generate_profile_and_documents(
        session,
        service_case,
        auto_send=auto_send,
    )
    return documents


async def generate_profile_and_documents(
    session: AsyncSession,
    service_case: ServiceCase,
    *,
    auto_send: bool = False,
) -> tuple[UserProfile, list[UserDocument]]:
    submission = await _latest_submission(session, service_case.id)
    if not submission:
        raise RuntimeError("questionnaire submission is required before generating documents")

    user_name = await _user_name(session, service_case)
    ai = AIService()
    generation_started_at = datetime.now(timezone.utc)
    package = await _generate_intake_analysis_package(
        session=session,
        ai=ai,
        service_case=service_case,
        user_name=user_name,
        submission=submission,
    )
    llm_output_at = datetime.now(timezone.utc)
    intake_model = _actual_model_for(ai, "intake_doc")
    intake_facts = package.get("intake_facts") if isinstance(package.get("intake_facts"), dict) else {}
    legal_name = str(intake_facts.get("legal_name") or user_name or "未提供").strip()
    display_name = str(intake_facts.get("recipient_name") or user_name or "未提供").strip()
    # 客户分型标签：在带位 + 风控风险等级上做确定性投影，供运营“定调”与群体聚合。
    if isinstance(package.get("profile_json"), dict):
        package["profile_json"]["customer_type"] = customer_type_for_profile(
            package["profile_json"], service_case.risk_level
        )
    profile = UserProfile(
        service_case_id=service_case.id,
        profile_json=package["profile_json"],
        summary=package["summary"],
        generated_by_model=intake_model,
    )
    session.add(profile)
    await initialize_user_state_from_intake(
        session,
        service_case=service_case,
        profile_json=package["profile_json"],
        intake_facts=intake_facts,
    )
    internal_review = _internal_review_metadata(package["profile_json"])
    documents: list[UserDocument] = []
    for document_type, title in DOCUMENT_DEFINITIONS.items():
        fields_payload = package["document_payloads"].get(document_type.value) or {}
        content_json = build_document_content_json(
            document_type=document_type,
            title=title,
            user_name=legal_name,
            display_name=display_name,
            fields=fields_payload,
        )
        if internal_review:
            # 顶层非渲染元数据：docx/Markdown 只渲染 blocks，所以运营能在后台看到复核标记，
            # 用户拿到的文件里不会出现。绝不能写进 blocks 或 content_md。
            content_json["internal_review"] = internal_review
        content = render_document_content(content_json)
        if not _document_content_is_sufficient(content):
            raise RuntimeError(f"{title} generated empty content")
        document = UserDocument(
            service_case_id=service_case.id,
            document_type=document_type,
            title=title,
            content_md=content,
            content_json=content_json,
            file_path=None,
            status=DocumentStatus.REVIEWING,
            generated_by_model=intake_model,
        )
        session.add(document)
        documents.append(document)
    # 首稿在生成文件的同一事务中冻结。运营后续编辑只覆盖 UserDocument 当前稿，
    # 不会再影响此快照；不要等到首次编辑时再抄，避免任何非前端更新先覆盖首稿。
    await session.flush()
    for document in documents:
        session.add(
            UserDocumentOriginal(
                document_id=document.id,
                title=document.title,
                content_md=document.content_md,
                content_json=deepcopy(document.content_json),
                generated_by_model=document.generated_by_model,
            )
        )
    service_case.content_generated_at = llm_output_at
    # 页面展示的是最近一次内容生成耗时。每次生成都从本次任务开始计时，
    # 不能在重新生成时继续拿首次问卷提交时间计算，否则旧服务单会得到数十天的伪耗时并溢出 int32。
    service_case.questionnaire_to_llm_output_ms = max(
        int((llm_output_at - generation_started_at).total_seconds() * 1000),
        0,
    )
    tz = ZoneInfo(get_settings().reminder_timezone)
    set_pre_service_status_or_activate(
        service_case,
        ServiceCaseStatus.REVIEWING,
        now=llm_output_at,
        tz=tz,
    )
    await session.commit()
    await session.refresh(profile)
    for document in documents:
        await session.refresh(document)
    # 需人工复核的入营文件一律不自动发送：显式 auto_send 参数和全局 auto_send 策略都不能绕过。
    # 运营在后台看到标记、检查或编辑文件后，仍可走原有「确认发送」人工路径。
    if documents_manual_review_hold(documents):
        logger.info(
            "Intake documents held for manual review service_case_id=%s reasons=%s",
            service_case.id,
            internal_review.get("reasons"),
        )
    elif (auto_send and not is_high_risk(service_case)) or await _should_auto_send(session, service_case):
        await send_case_documents(session, service_case.id)
    return profile, documents


async def generate_stage_report(
    session: AsyncSession,
    service_case: ServiceCase,
    *,
    stage: str,
) -> UserDocument:
    if stage not in STAGE_REPORT_STAGES:
        raise RuntimeError("invalid stage")
    existing = await _existing_stage_report(session, service_case.id, stage)
    if existing:
        return existing

    user_name = await _user_name(session, service_case)
    recipient_name, gender = await _recipient_name_and_gender(session, service_case)
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
    state_profile = await session.scalar(
        select(UserStateProfile)
        .where(UserStateProfile.service_case_id == service_case.id)
        .order_by(UserStateProfile.updated_at.desc())
        .limit(1)
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
        state_json=state_profile.state_json if state_profile else None,
        checkins=checkins,
        messages=messages,
        recipient_name=recipient_name,
        gender=gender,
    )
    degraded = bool(fields_payload.pop("__degraded__", False)) if isinstance(fields_payload, dict) else False
    content_json = build_stage_report_content_json(
        stage=stage,
        title=title,
        user_name=user_name,
        fields=fields_payload,
    )
    if degraded and isinstance(content_json, dict):
        content_json["degraded"] = True
    content = render_document_content(content_json)
    document = UserDocument(
        service_case_id=service_case.id,
        document_type=DocumentType.STAGE_REPORT,
        title=title,
        content_md=content,
        content_json=content_json,
        file_path=None,
        status=DocumentStatus.REVIEWING,
        generated_by_model=_actual_model_for(ai, "stage_report"),
    )
    session.add(document)
    await session.commit()
    await session.refresh(document)
    if not degraded and await _should_auto_send(session, service_case):
        await send_one_document(session, document)
    return document


async def retry_degraded_stage_report(session: AsyncSession, document: UserDocument) -> bool:
    """重试一份降级(degraded)的阶段报告：LLM 恢复后用真内容替换并去掉降级标记。

    成功返回 True（document 已更新为正式内容）；仍失败返回 False 并把 degraded_retry_count +1。
    """
    service_case = await get_active_service_case(session, document.service_case_id)
    if not service_case:
        return False
    base = document.content_json if isinstance(document.content_json, dict) else {}
    if document.document_type != DocumentType.STAGE_REPORT:
        return False
    stage = str(base.get("stage") or "")
    if stage not in STAGE_REPORT_STAGES:
        return False

    user_name = await _user_name(session, service_case)
    recipient_name, gender = await _recipient_name_and_gender(session, service_case)
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
    state_profile = await session.scalar(
        select(UserStateProfile)
        .where(UserStateProfile.service_case_id == service_case.id)
        .order_by(UserStateProfile.updated_at.desc())
        .limit(1)
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
        state_json=state_profile.state_json if state_profile else None,
        checkins=checkins,
        messages=messages,
        recipient_name=recipient_name,
        gender=gender,
    )
    still_degraded = isinstance(fields_payload, dict) and bool(fields_payload.pop("__degraded__", False))
    if still_degraded:
        retry_count = int(base.get("degraded_retry_count", 0)) + 1
        new_cj = dict(base)
        new_cj["degraded_retry_count"] = retry_count
        document.content_json = new_cj
        await session.commit()
        logger.info("Stage report retry still degraded doc_id=%s stage=%s count=%s", document.id, stage, retry_count)
        return False

    content_json = build_stage_report_content_json(stage=stage, title=title, user_name=user_name, fields=fields_payload)
    document.content_json = content_json  # 不含 degraded / degraded_retry_count
    document.content_md = render_document_content(content_json)
    document.file_path = None
    document.generated_by_model = _actual_model_for(ai, "stage_report")
    await session.commit()
    logger.info("Stage report degraded -> recovered doc_id=%s stage=%s", document.id, stage)
    return True


async def rewrite_document_file(session: AsyncSession, document: UserDocument) -> UserDocument:
    service_case = await get_active_service_case(session, document.service_case_id)
    if not service_case:
        raise RuntimeError("service case not found")
    if document.content_json:
        document.content_json = normalize_content_json(document.content_json)
        document.content_md = render_template_markdown(document.content_json)
    document.file_path = None
    await session.commit()
    await session.refresh(document)
    return document


def _internal_review_metadata(profile_json: object) -> dict[str, object]:
    """把画像里的内部安全状态收成一份非渲染元数据，供后台标记与发送闸门使用。

    只在真的需要人工复核时返回内容；不需要时返回空 dict，避免给每份文档都塞噪声字段。
    """

    if not isinstance(profile_json, dict):
        return {}
    if not profile_json.get("manual_review_required"):
        return {}
    reasons = profile_json.get("manual_review_reasons")
    metadata: dict[str, object] = {
        "manual_review_required": True,
        "reasons": [str(item).strip() for item in reasons if str(item).strip()]
        if isinstance(reasons, list)
        else [],
        "safety_status": str(profile_json.get("safety_status") or "manual_review"),
    }
    if profile_json.get("provisional_band"):
        metadata["provisional_band"] = True
    band_confidence = str(profile_json.get("band_confidence") or "").strip()
    if band_confidence:
        metadata["band_confidence"] = band_confidence
    return metadata


def _document_content_is_sufficient(content: str) -> bool:
    plain = content.replace("Powered by HumanBurning.AI", "")
    plain = plain.replace("#", "").replace("To：", "").strip()
    if len(plain) < 500:
        return False
    if '  "recipient":' in content or '"sections": [' in content:
        return False
    return True
