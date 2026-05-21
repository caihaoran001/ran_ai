from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.db.session import get_session
from app.models.checkin import Checkin
from app.models.questionnaire import QuestionnaireSubmission
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user import User
from app.models.user_profile import UserProfile
from app.services.checkin_service import normalize_checkin_json
from app.schemas.questionnaire import (
    QuestionnairePublic,
    QuestionnaireSubmissionRead,
    QuestionnaireSubmit,
)
from app.services.document_service import generate_profile, generate_documents
from app.db.session import SessionLocal
from app.services.intervention_service import (
    analyze_intervention,
    merge_case_insight,
    questionnaire_content,
)
from app.services.questionnaire_template import get_active_questionnaire_fields
from app.services.questionnaire_template import get_active_questionnaire_template

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/{token}", response_model=QuestionnairePublic)
async def get_questionnaire(
    token: str,
    template: str = Query(default="intake"),
    session: AsyncSession = Depends(get_session),
) -> QuestionnairePublic:
    service_case = await _case_by_token(session, token, include_deleted=False)
    if template == "personalized_checkin":
        latest_profile = await session.scalar(
            select(UserProfile)
            .where(UserProfile.service_case_id == service_case.id)
            .order_by(UserProfile.created_at.desc())
            .limit(1)
        )
        fields = []
        if latest_profile and latest_profile.profile_json:
            fields = latest_profile.profile_json.get("personalized_checkin_fields") or []
        
        if not fields:
            fields = [
                {
                    "id": "personalized_weight",
                    "key": "personalized_weight",
                    "type": "SIMPLE",
                    "title": "今日晨起体重（kg）",
                    "label": "今日晨起体重（kg）",
                    "subtitle": "",
                    "required": True,
                    "options": []
                },
                {
                    "id": "personalized_water",
                    "key": "personalized_water",
                    "type": "RADIO",
                    "title": "今日饮水是否达到 2 升？",
                    "label": "今日饮水是否达到 2 升？",
                    "subtitle": "",
                    "required": True,
                    "options": ["是", "否"]
                },
                {
                    "id": "personalized_sleep",
                    "key": "personalized_sleep",
                    "type": "RADIO",
                    "title": "昨晚是否在 23:00 前入睡？",
                    "label": "昨晚是否在 23:00 前入睡？",
                    "subtitle": "",
                    "required": True,
                    "options": ["是", "否"]
                },
                {
                    "id": "personalized_diet",
                    "key": "personalized_diet",
                    "type": "RADIO",
                    "title": "今日晚餐是否保持干净饮食？（少油少糖）",
                    "label": "今日晚餐是否保持干净饮食？（少油少糖）",
                    "subtitle": "",
                    "required": True,
                    "options": ["是", "否"]
                },
                {
                    "id": "personalized_exercise",
                    "key": "personalized_exercise",
                    "type": "RADIO",
                    "title": "今日是否完成了预定身体活动/运动？",
                    "label": "今日是否完成了预定身体活动/运动？",
                    "subtitle": "",
                    "required": True,
                    "options": ["是", "否"]
                }
            ]
        
        template_payload = {
            "template_key": "personalized_checkin",
            "name": "每日行动",
            "title": f"HB.AI | {service_case.user.name if service_case.user else '您'}的每日行动（必填）",
            "description": "这是为您量身定制的每日固定行动（共 5 项）。今日如时间紧张，请优先完成此表（只需10秒）。",
            "fields": fields
        }
    else:
        template_payload = await _template_or_404(session, template)

    latest_submission = await _latest_submission(session, service_case.id, template)
    return QuestionnairePublic(
        service_case_id=service_case.id,
        token=token,
        template_key=template,
        title=template_payload.get("title"),
        description=template_payload.get("description"),
        allow_repeat=_allow_repeat(template),
        user_name=service_case.user.name if service_case.user else None,
        fields=list(template_payload.get("fields") or []),
        latest_submission=latest_submission,
    )


@router.post(
    "/{token}/submit",
    response_model=QuestionnaireSubmissionRead,
    status_code=status.HTTP_201_CREATED,
)
async def submit_questionnaire(
    token: str,
    payload: QuestionnaireSubmit,
    background_tasks: BackgroundTasks,
    template: str = Query(default="intake"),
    session: AsyncSession = Depends(get_session),
) -> QuestionnaireSubmission:
    service_case = await _case_by_token(session, token, include_deleted=False)
    if template == "personalized_checkin":
        latest_profile = await session.scalar(
            select(UserProfile)
            .where(UserProfile.service_case_id == service_case.id)
            .order_by(UserProfile.created_at.desc())
            .limit(1)
        )
        fields = []
        if latest_profile and latest_profile.profile_json:
            fields = latest_profile.profile_json.get("personalized_checkin_fields") or []
        if not fields:
            fields = [
                {"id": "personalized_weight", "key": "personalized_weight", "type": "SIMPLE", "title": "今日晨起体重（kg）", "label": "今日晨起体重（kg）", "subtitle": "", "required": True, "options": []},
                {"id": "personalized_water", "key": "personalized_water", "type": "RADIO", "title": "今日饮水是否达到 2 升？", "label": "今日饮水是否达到 2 升？", "subtitle": "", "required": True, "options": ["是", "否"]},
                {"id": "personalized_sleep", "key": "personalized_sleep", "type": "RADIO", "title": "昨晚是否在 23:00 前入睡？", "label": "昨晚是否在 23:00 前入睡？", "subtitle": "", "required": True, "options": ["是", "否"]},
                {"id": "personalized_diet", "key": "personalized_diet", "type": "RADIO", "title": "今日晚餐是否保持干净饮食？（少油少糖）", "label": "今日晚餐是否保持干净饮食？（少油少糖）", "subtitle": "", "required": True, "options": ["是", "否"]},
                {"id": "personalized_exercise", "key": "personalized_exercise", "type": "RADIO", "title": "今日是否完成了预定身体活动/运动？", "label": "今日是否完成了预定身体活动/运动？", "subtitle": "", "required": True, "options": ["是", "否"]}
            ]
    else:
        await _template_or_404(session, template)
        fields = await get_active_questionnaire_fields(session, template)

    if not _allow_repeat(template) and await _latest_submission(session, service_case.id, template):
        raise HTTPException(status_code=409, detail="questionnaire already submitted")
    submission = QuestionnaireSubmission(
        service_case_id=service_case.id,
        template_key=template,
        answers_json=payload.answers,
    )
    if template == "intake":
        service_case.status = ServiceCaseStatus.QUESTIONNAIRE_SUBMITTED
        await _sync_user_from_answers(session, service_case, fields, payload.answers)
    elif template != "intake":
        _create_checkin_from_questionnaire(session, service_case, fields, payload.answers)

    analysis = await analyze_intervention(
        source="questionnaire",
        content=questionnaire_content(fields, payload.answers),
        context={
            "service_case_id": service_case.id,
            "case_no": service_case.case_no,
            "template_key": template,
        },
    )
    merge_case_insight(service_case, analysis)
    session.add(submission)
    await session.commit()
    await session.refresh(submission)
    if template == "intake" and get_settings().ai_api_key:
        background_tasks.add_task(_generate_profile_and_documents, service_case.id)
    return submission


async def _case_by_token(
    session: AsyncSession,
    token: str,
    *,
    include_deleted: bool,
) -> ServiceCase:
    conditions = [ServiceCase.questionnaire_token == token]
    if not include_deleted:
        conditions.append(ServiceCase.deleted_at.is_(None))
    service_case = await session.scalar(
        select(ServiceCase)
        .where(*conditions)
        .options(selectinload(ServiceCase.user))
    )
    if not service_case:
        raise HTTPException(status_code=404, detail="questionnaire not found")
    return service_case


async def _latest_submission(
    session: AsyncSession,
    service_case_id: int,
    template_key: str = "intake",
) -> QuestionnaireSubmission | None:
    return await session.scalar(
        select(QuestionnaireSubmission)
        .where(
            QuestionnaireSubmission.service_case_id == service_case_id,
            QuestionnaireSubmission.template_key == template_key,
        )
        .order_by(QuestionnaireSubmission.created_at.desc())
        .limit(1)
    )


async def _template_or_404(session: AsyncSession, template_key: str) -> dict:
    try:
        return await get_active_questionnaire_template(session, template_key)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="questionnaire template not found") from exc


def _allow_repeat(template_key: str) -> bool:
    return template_key != "intake"


def _create_checkin_from_questionnaire(
    session: AsyncSession,
    service_case: ServiceCase,
    fields: list[dict],
    answers: dict,
) -> None:
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    content = questionnaire_content(fields, answers)
    parsed_json = _checkin_json_from_answers(answers, content)
    session.add(
        Checkin(
            service_case_id=service_case.id,
            group_id=service_case.group_id,
            user_id=service_case.user_id,
            checkin_date=today,
            content=content,
            raw_message_id=None,
            parsed_json=parsed_json,
        )
    )


def _checkin_json_from_answers(answers: dict, content: str) -> dict:
    provided = {
        "product_schedule": {
            "morning_c": answers.get("morning_c") or (f'{answers.get("morning_c_days")}/7天' if answers.get("morning_c_days") else ""),
            "noon_b": answers.get("noon_b") or (f'{answers.get("noon_b_days")}/7天' if answers.get("noon_b_days") else ""),
            "night_a": answers.get("night_a") or (f'{answers.get("night_a_days")}/7天' if answers.get("night_a_days") else ""),
            "summary": answers.get("product_status") or "",
        },
        "water": answers.get("water") or answers.get("water_execution") or "",
        "sleep": _join_answers(
            answers,
            ("sleep", "sleep_time", "wake_time", "sleep_quality", "avg_sleep"),
        ),
        "mood": answers.get("mood") or answers.get("today_feedback") or answers.get("biggest_positive_change") or "",
        "exercise": answers.get("exercise") or answers.get("exercise_status") or "",
        "weight": answers.get("morning_weight") or answers.get("current_weight") or "",
        "waist": answers.get("waist") or answers.get("waist_line") or answers.get("current_waist") or "",
        "diet": answers.get("diet") or answers.get("diet_structure") or answers.get("diet_execution") or "",
        "bowel": answers.get("bowel") or answers.get("bowel_status") or answers.get("排便") or "",
        "hunger": answers.get("hunger") or answers.get("appetite") or answers.get("食欲") or "",
        "sensation": answers.get("sensation") or answers.get("body_feeling") or answers.get("体感") or "",
        "discomfort": answers.get("discomfort") or answers.get("uncomfortable") or answers.get("不适") or "",
        "tomorrow_focus": answers.get("tomorrow_focus") or answers.get("tomorrow_goal") or "",
    }
    _merge_personalized_checkin_answers(provided, answers)
    return normalize_checkin_json(provided, content)


def _join_answers(answers: dict, keys: tuple[str, ...]) -> str:
    values = [str(answers.get(key) or "").strip() for key in keys]
    return "；".join(value for value in values if value)


def _merge_personalized_checkin_answers(provided: dict, answers: dict) -> None:
    personalized_mapping = {
        "personalized_weight": "weight",
        "personalized_water": "water",
        "personalized_sleep": "sleep",
        "personalized_diet": "diet",
        "personalized_exercise": "exercise",
    }
    for source_key, target_key in personalized_mapping.items():
        value = str(answers.get(source_key) or "").strip()
        if value and not provided.get(target_key):
            provided[target_key] = value


async def _generate_profile_and_documents(service_case_id: int) -> None:
    async with SessionLocal() as session:
        existing = await session.scalar(
            select(UserProfile.id)
            .where(UserProfile.service_case_id == service_case_id)
            .limit(1)
        )
        if existing:
            return
        service_case = await session.scalar(
            select(ServiceCase).where(ServiceCase.id == service_case_id, ServiceCase.deleted_at.is_(None))
        )
        if not service_case:
            return
        try:
            await generate_profile(session, service_case)
            await generate_documents(session, service_case, auto_send=get_settings().auto_send_documents)
        except Exception as exc:
            logger.exception("Failed to generate profile and documents for service_case_id=%s", service_case_id)
            await session.rollback()
            failed_case = await session.scalar(
                select(ServiceCase).where(ServiceCase.id == service_case_id, ServiceCase.deleted_at.is_(None))
            )
            if failed_case:
                _append_generation_error_note(failed_case, exc)
                await session.commit()


def _append_generation_error_note(service_case: ServiceCase, exc: Exception) -> None:
    timestamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
    message = f"[{timestamp}] 自动生成画像/交付文件失败：{type(exc).__name__}: {exc}"
    existing = service_case.notes or ""
    service_case.notes = (f"{existing}\n{message}" if existing else message)[-2000:]


async def _sync_user_from_answers(
    session: AsyncSession,
    service_case: ServiceCase,
    fields: list[dict],
    answers: dict,
) -> None:
    user = service_case.user
    if not user and service_case.user_id:
        user = await session.get(User, service_case.user_id)
    if not user:
        return

    name = _answer_by_label(fields, answers, ("真实姓名", "姓名", "name"))
    phone = _answer_by_label(fields, answers, ("手机号", "手机", "phone"))
    if name:
        user.name = name[:100]
    if phone and not user.phone:
        user.phone = phone[:32]


def _answer_by_label(fields: list[dict], answers: dict, keywords: tuple[str, ...]) -> str | None:
    for field in fields:
        key = field.get("key") or field.get("id")
        if not key:
            continue
        label = str(field.get("title") or field.get("label") or "")
        if any(keyword in label for keyword in keywords):
            value = answers.get(key)
            if isinstance(value, str):
                value = value.strip()
                if value:
                    return value
    for key, value in answers.items():
        if any(keyword.lower() in str(key).lower() for keyword in keywords):
            if isinstance(value, str):
                value = value.strip()
                if value:
                    return value
    return None
