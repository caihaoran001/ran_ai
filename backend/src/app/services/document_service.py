from __future__ import annotations

"""交付文档编排层：串联入营建档、阶段报告、画像刷新，并发送。

具体实现拆分到同目录子模块；本文件保留编排流程 + AIService 实例化
（测试通过 patch 本模块的 AIService 注入 Mock LLM），并重新导出公共 API。
"""

import json
import logging

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.checkin import Checkin
from app.models.message import Message, MessageDirection
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user_document import DocumentStatus, DocumentType, UserDocument
from app.models.user_profile import UserProfile
from app.models.user_state_profile import UserStateProfile
from app.services.ai_service import AIService, parse_json_object
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

from app.services.document_common import (
    DOCUMENT_DEFINITIONS,
    _latest_submission,
    _user_name,
)
from app.services.document_prompts import (
    _PROFILE_REFRESH_SYSTEM_PROMPT,
    _intake_generation_system_prompt,
)
from app.services.intake_generation import (
    _apply_intake_facts_to_document_payloads,
    _generate_intake_analysis_package,
    personalized_checkin_fields_from_document_payloads,
)
from app.services.stage_report import (
    _existing_stage_report,
    _generate_stage_payload,
    _stage_title,
)
from app.services.document_delivery import (
    _should_auto_send,
    create_temporary_document_file,
    send_case_documents,
    send_one_document,
)

logger = logging.getLogger(__name__)

__all__ = [
    "generate_profile",
    "generate_documents",
    "generate_profile_and_documents",
    "refresh_profile_for_case",
    "generate_stage_report",
    "retry_degraded_stage_report",
    "rewrite_document_file",
    "create_temporary_document_file",
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
    llm_output_at = datetime.now(timezone.utc)
    package = await _generate_intake_analysis_package(
        session=session,
        ai=ai,
        service_case=service_case,
        user_name=user_name,
        submission=submission,
    )
    llm_output_at = datetime.now(timezone.utc)
    intake_model = ai._model_for("intake_doc")
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
    service_case.content_generated_at = llm_output_at
    if submission.created_at:
        service_case.questionnaire_to_llm_output_ms = max(
            int((llm_output_at - submission.created_at).total_seconds() * 1000),
            0,
        )
    service_case.status = ServiceCaseStatus.REVIEWING
    await session.commit()
    await session.refresh(profile)
    for document in documents:
        await session.refresh(document)
    if (auto_send and not is_high_risk(service_case)) or await _should_auto_send(session, service_case):
        await send_case_documents(session, service_case.id)
    return profile, documents


def _state_evidence_summary(state_json: object) -> str:
    if not isinstance(state_json, dict):
        return "暂无动态状态"
    domains = state_json.get("domains")
    if not isinstance(domains, dict):
        return "暂无动态状态"
    lines: list[str] = []
    for key, domain in domains.items():
        if not isinstance(domain, dict):
            continue
        current = domain.get("current")
        if not isinstance(current, dict):
            continue
        evidence = current.get("evidence")
        if evidence:
            lines.append(f"{domain.get('label') or key}：{evidence}")
    return "\n".join(lines) if lines else "暂无动态状态"


async def _refresh_profile_from_progress(
    session: AsyncSession,
    *,
    service_case_id: int,
    base_profile_json: dict | None,
    checkins: list[Checkin],
    messages: list[Message],
    stage: str,
) -> None:
    """阶段节点用动态记忆+近期聊天/打卡刷新主画像，写入一版新的 UserProfile。

    复用传入 session；失败仅记日志并回滚，绝不影响阶段报告主流程（文档已先行 commit）。
    只更新动态字段，保留 overview(建档基础信息) 与 personalized_checkin_fields(打卡表单依赖)。
    """
    try:
        if not isinstance(base_profile_json, dict) or not base_profile_json:
            return
        base = base_profile_json
        checkin_lines = [
            f"- {c.checkin_date.isoformat()}: {(c.content or '').strip()[:80]}"
            for c in checkins[-10:]
        ]
        checkin_text = "\n".join(checkin_lines) or "暂无打卡"
        inbound = [
            (m.content or "").strip()
            for m in messages
            if m.direction == MessageDirection.INBOUND and (m.content or "").strip()
        ][-15:]
        inbound_text = "\n".join(f"- {t[:120]}" for t in inbound) or "暂无群聊"
        editable = {
            key: base.get(key)
            for key in ("summary", "state", "main_problem", "day28_goal", "risks", "need_analysis", "notes")
        }
        state_profile = await session.scalar(
            select(UserStateProfile)
            .where(UserStateProfile.service_case_id == service_case_id)
            .order_by(UserStateProfile.updated_at.desc())
            .limit(1)
        )
        state_summary = _state_evidence_summary(state_profile.state_json if state_profile else None)
        ai = AIService()
        text = await ai.generate_text(
            scenario="profile_refresh",
            temperature=0.3,
            service_case_id=service_case_id,
            system_prompt=_PROFILE_REFRESH_SYSTEM_PROMPT,
            user_prompt=(
                f"阶段：{stage}\n\n"
                f"【入营建档画像-可更新字段】\n{json.dumps(editable, ensure_ascii=False, indent=2)}\n\n"
                f"【动态状态记忆】\n{state_summary}\n\n"
                f"【近期打卡】\n{checkin_text}\n\n"
                f"【近期群聊原话】\n{inbound_text}"
            ),
        )
        updates = parse_json_object(text)
        if not isinstance(updates, dict) or not updates:
            return
        new_profile = dict(base)
        for key in ("summary", "state", "main_problem", "day28_goal", "risks", "need_analysis", "notes"):
            value = updates.get(key)
            if value not in (None, "", [], {}):
                new_profile[key] = value
        if "overview" in base:
            new_profile["overview"] = base.get("overview")
        if "personalized_checkin_fields" in base:
            new_profile["personalized_checkin_fields"] = base.get("personalized_checkin_fields")
        if "tags" in base:
            new_profile["tags"] = base.get("tags")
        # 每次刷新重推客户分型（带位/风险变了标签应跟着变，不冻结）。
        service_case = await get_active_service_case(session, service_case_id)
        new_profile["customer_type"] = customer_type_for_profile(
            new_profile, service_case.risk_level if service_case else None
        )
        new_profile["refreshed_from"] = {"stage": stage, "source": "stage_progress"}
        summary = str(new_profile.get("summary") or base.get("summary") or "")[:2000]
        session.add(
            UserProfile(
                service_case_id=service_case_id,
                profile_json=new_profile,
                summary=summary,
                generated_by_model=f"{ai.settings.ai_model} · 阶段刷新({stage})",
            )
        )
        await session.commit()
        logger.info("Profile refreshed from progress service_case_id=%s stage=%s", service_case_id, stage)
    except Exception:
        logger.exception(
            "Refresh profile from progress failed service_case_id=%s stage=%s", service_case_id, stage
        )
        await session.rollback()


async def refresh_profile_for_case(
    session: AsyncSession,
    service_case: ServiceCase,
    *,
    stage: str,
) -> None:
    """查取最新画像/打卡/聊天并刷新主画像，供阶段报告与每日定时刷新复用。"""
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
    await _refresh_profile_from_progress(
        session,
        service_case_id=service_case.id,
        base_profile_json=latest_profile.profile_json if latest_profile else None,
        checkins=checkins,
        messages=messages,
        stage=stage,
    )


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
    document_type = (
        DocumentType.GRADUATION_REPORT
        if stage == "graduation"
        else DocumentType.STAGE_REPORT
    )
    stage_model = ai._model_for("graduation_report" if stage == "graduation" else "stage_report")
    document = UserDocument(
        service_case_id=service_case.id,
        document_type=document_type,
        title=title,
        content_md=content,
        content_json=content_json,
        file_path=None,
        status=DocumentStatus.REVIEWING,
        generated_by_model=stage_model,
    )
    session.add(document)
    await session.commit()
    await session.refresh(document)
    if not degraded and await _should_auto_send(session, service_case):
        await send_one_document(session, document)
    if stage != "graduation":
        await _refresh_profile_from_progress(
            session,
            service_case_id=service_case.id,
            base_profile_json=latest_profile.profile_json if latest_profile else None,
            checkins=checkins,
            messages=messages,
            stage=stage,
        )
    return document


async def retry_degraded_stage_report(session: AsyncSession, document: UserDocument) -> bool:
    """重试一份降级(degraded)的阶段报告：LLM 恢复后用真内容替换并去掉降级标记。

    成功返回 True（document 已更新为正式内容）；仍失败返回 False 并把 degraded_retry_count +1。
    """
    service_case = await get_active_service_case(session, document.service_case_id)
    if not service_case:
        return False
    base = document.content_json if isinstance(document.content_json, dict) else {}
    if document.document_type == DocumentType.GRADUATION_REPORT:
        stage = "graduation"
    else:
        stage = str(base.get("stage") or "")
    if stage not in {"day3", "day7", "day14", "day21", "day28", "graduation"}:
        return False

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
    document.generated_by_model = ai._model_for("graduation_report" if stage == "graduation" else "stage_report")
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


def _document_content_is_sufficient(content: str) -> bool:
    plain = content.replace("Powered by HumanBurning.AI", "")
    plain = plain.replace("#", "").replace("To：", "").strip()
    if len(plain) < 500:
        return False
    if '  "recipient":' in content or '"sections": [' in content:
        return False
    return True
