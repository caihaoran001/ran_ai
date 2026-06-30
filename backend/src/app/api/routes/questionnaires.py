from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.db.session import get_session
from app.models.checkin import Checkin
from app.models.message import (
    Message,
    MessageDirection,
    MessagePurpose,
    MessageSenderType,
    MessageStatus,
)
from app.models.questionnaire import QuestionnaireSubmission
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user import User
from app.models.user_document import DocumentType, UserDocument
from app.models.user_profile import UserProfile
from app.services.checkin_service import normalize_checkin_json
from app.schemas.questionnaire import (
    QuestionnairePublic,
    QuestionnaireSubmissionRead,
    QuestionnaireSubmit,
)
from app.services.document_service import generate_profile_and_documents, personalized_checkin_fields_from_document_payloads
from app.db.session import SessionLocal
from app.services.channels import send_channel_text
from app.services.checkin_questionnaires import get_checkin_questionnaire_schedule
from app.services.checkin_questionnaires import DEFAULT_PERSONALIZED_CHECKIN_FIELDS
from app.services.intervention_service import (
    analyze_intervention,
    mark_intake_age_risk,
    merge_case_insight,
    questionnaire_content,
)
from app.services.outcome_engine import record_intake_profile_completed
from app.services.questionnaire_template import get_active_questionnaire_fields
from app.services.questionnaire_template import get_active_questionnaire_template
from app.services.service_case_queries import active_service_case_condition, get_active_service_case, service_window_ended

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/{token}", response_model=QuestionnairePublic)
async def get_questionnaire(
    token: str,
    template: str = Query(default="intake"),
    session: AsyncSession = Depends(get_session),
) -> QuestionnairePublic:
    service_case = await _case_by_token(session, token, include_deleted=False)
    default_values: dict[str, Any] = {}
    timezone = ZoneInfo(get_settings().reminder_timezone)
    if template != "intake" and service_window_ended(service_case, datetime.now(timezone), timezone):
        raise HTTPException(status_code=410, detail="28天服务已结束，无需继续打卡")
    if template == "personalized_checkin":
        fields = await _personalized_checkin_fields(session, service_case)
        template_payload = {
            "template_key": "personalized_checkin",
            "name": "每日行动",
            "title": f"HB.AI | {service_case.user.name if service_case.user else '您'}的每日行动（必填）",
            "description": "这是为您量身定制的每日固定行动（共 5 项）。今日如时间紧张，请优先完成此表（只需10秒）。",
            "fields": fields
        }
    else:
        template_payload = await _template_or_404(session, template)
        if template != "intake":
            default_values = await _build_checkin_default_values(session, service_case, template)

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
        default_values=default_values,
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
    timezone = ZoneInfo(get_settings().reminder_timezone)
    if template != "intake" and service_window_ended(service_case, datetime.now(timezone), timezone):
        raise HTTPException(status_code=410, detail="28天服务已结束，无需继续打卡")
    if template == "personalized_checkin":
        fields = await _personalized_checkin_fields(session, service_case)
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
        service_case.status = ServiceCaseStatus.GENERATING if get_settings().ai_api_key else ServiceCaseStatus.QUESTIONNAIRE_SUBMITTED
        await _sync_user_from_answers(session, service_case, fields, payload.answers)
        await record_intake_profile_completed(session, service_case=service_case)
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
    if template == "intake":
        mark_intake_age_risk(service_case, fields, payload.answers)
    session.add(submission)
    await session.commit()
    await session.refresh(submission)
    if template == "intake" and get_settings().ai_api_key:
        background_tasks.add_task(_send_intake_submit_reply, service_case.id)
        background_tasks.add_task(_generate_profile_and_documents, service_case.id)
    return submission


@router.patch(
    "/{token}/submit",
    response_model=QuestionnaireSubmissionRead,
)
async def edit_intake_submission(
    token: str,
    payload: QuestionnaireSubmit,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> QuestionnaireSubmission:
    """运营编辑已提交的入营问卷答案，并重新生成画像+文档。

    已发送的旧文档保留在库（发送时自动跳过），重新生成的新文档进入待审核。
    """
    service_case = await _case_by_token(session, token, include_deleted=False)
    fields = await get_active_questionnaire_fields(session, "intake")
    submission = await _latest_submission(session, service_case.id, "intake")
    if not submission:
        raise HTTPException(status_code=404, detail="no intake submission to edit")
    submission.answers_json = payload.answers
    await _sync_user_from_answers(session, service_case, fields, payload.answers)
    mark_intake_age_risk(service_case, fields, payload.answers)
    if get_settings().ai_api_key:
        service_case.status = ServiceCaseStatus.GENERATING
    await session.commit()
    await session.refresh(submission)
    if get_settings().ai_api_key:
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
        conditions.append(active_service_case_condition())
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


async def _build_checkin_default_values(
    session: AsyncSession,
    service_case: ServiceCase,
    template: str,
) -> dict[str, Any]:
    defaults: dict[str, Any] = {}
    if not service_case.started_at:
        return defaults
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    started = service_case.started_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=ZoneInfo("UTC"))
    day = (now - started.astimezone(ZoneInfo("Asia/Shanghai"))).days + 1
    defaults["day_no"] = max(1, day)

    all_checkins = list(
        await session.scalars(
            select(Checkin)
            .where(Checkin.service_case_id == service_case.id)
            .order_by(Checkin.checkin_date.desc())
        )
    )

    latest_checkin = all_checkins[0] if all_checkins else None
    if latest_checkin and isinstance(latest_checkin.parsed_json, dict):
        pj = latest_checkin.parsed_json
        w = pj.get("weight")
        if w:
            defaults["morning_weight"] = str(w)
        waist = pj.get("waist")
        if waist:
            defaults["waist"] = str(waist)

    if not defaults.get("morning_weight"):
        intake_submission = await session.scalar(
            select(QuestionnaireSubmission)
            .where(QuestionnaireSubmission.service_case_id == service_case.id)
            .where(QuestionnaireSubmission.template_key == "intake")
            .order_by(QuestionnaireSubmission.created_at.asc())
            .limit(1)
        )
        if intake_submission and isinstance(intake_submission.answers_json, dict):
            ans = intake_submission.answers_json
            w = ans.get("weight") or ans.get("current_weight")
            if w:
                defaults["morning_weight"] = str(w)
            waist = ans.get("waist") or ans.get("current_waist")
            if waist and not defaults.get("waist"):
                defaults["waist"] = str(waist)

    if template == "stage_review":
        intake_submission = await session.scalar(
            select(QuestionnaireSubmission)
            .where(QuestionnaireSubmission.service_case_id == service_case.id)
            .where(QuestionnaireSubmission.template_key == "intake")
            .order_by(QuestionnaireSubmission.created_at.asc())
            .limit(1)
        )
        if intake_submission and isinstance(intake_submission.answers_json, dict):
            ans = intake_submission.answers_json
            fw = ans.get("weight") or ans.get("current_weight")
            if fw:
                defaults["initial_weight"] = str(fw)
            fwaist = ans.get("waist") or ans.get("current_waist")
            if fwaist:
                defaults["initial_waist"] = str(fwaist)

        if not defaults.get("initial_weight"):
            first_checkin = all_checkins[-1] if all_checkins else None
            if first_checkin and isinstance(first_checkin.parsed_json, dict):
                fw = first_checkin.parsed_json.get("weight")
                if fw:
                    defaults["initial_weight"] = str(fw)
                fwaist = first_checkin.parsed_json.get("waist")
                if fwaist:
                    defaults["initial_waist"] = str(fwaist)

        seven_days_ago = now.date() - timedelta(days=7)
        recent_checkins = [ci for ci in all_checkins if ci.checkin_date >= seven_days_ago]
        c_count = 0
        b_count = 0
        a_count = 0
        for ci in recent_checkins:
            ps = (ci.parsed_json or {}).get("product_schedule") or {}
            if isinstance(ps, dict):
                if str(ps.get("morning_c") or "") == "已喝":
                    c_count += 1
                if str(ps.get("noon_b") or "") == "已喝":
                    b_count += 1
                if str(ps.get("night_a") or "") == "已喝":
                    a_count += 1
        defaults["morning_c_days"] = f"{c_count}/7天"
        defaults["noon_b_days"] = f"{b_count}/7天"
        defaults["night_a_days"] = f"{a_count}/7天"
    return defaults


def _create_checkin_from_questionnaire(
    session: AsyncSession,
    service_case: ServiceCase,
    fields: list[dict],
    answers: dict,
) -> None:
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    if not answers.get("day_no") and service_case.started_at:
        started = service_case.started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=ZoneInfo("UTC"))
        day = (today - started.astimezone(ZoneInfo("Asia/Shanghai")).date()).days + 1
        answers["day_no"] = max(1, day)
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
        "diet": answers.get("diet") or answers.get("diet_execution") or "",
        "bowel": answers.get("bowel") or answers.get("bowel_status") or answers.get("排便") or "",
        "hunger": answers.get("hunger") or answers.get("appetite") or answers.get("食欲") or "",
        "sensation": answers.get("sensation") or answers.get("body_feeling") or answers.get("体感") or "",
        "discomfort": answers.get("discomfort") or answers.get("uncomfortable") or answers.get("不适") or "",
        "tomorrow_focus": answers.get("tomorrow_focus") or answers.get("tomorrow_goal") or "",
    }
    _merge_diet_from_meals(provided, answers)
    _merge_personalized_checkin_answers(provided, answers)
    return normalize_checkin_json(provided, content)


def _join_answers(answers: dict, keys: tuple[str, ...]) -> str:
    values = [str(answers.get(key) or "").strip() for key in keys]
    return "；".join(value for value in values if value)


_PERSONALIZED_MAPPING = {
    "personalized_weight": "weight",
    "personalized_water": "water",
    "personalized_sleep": "sleep",
    "personalized_diet": "diet",
    "personalized_exercise": "exercise",
}


def _merge_personalized_checkin_answers(provided: dict, answers: dict) -> None:
    STANDARD_KEYS = {"weight", "water", "sleep", "diet", "exercise", "bowel", "hunger", "sensation", "discomfort", "tomorrow_focus", "mood", "waist", "product_schedule"}
    for key, value in answers.items():
        if not key.startswith("personalized_"):
            continue
        value_str = str(value or "").strip()
        if not value_str:
            continue
        mapped = _PERSONALIZED_MAPPING.get(key)
        if mapped and not provided.get(mapped):
            provided[mapped] = value_str
        elif not mapped:
            provided[key] = value_str


def _merge_diet_from_meals(provided: dict, answers: dict) -> None:
    if provided.get("diet"):
        return
    meal_parts = []
    for key in ("breakfast", "lunch", "dinner"):
        val = str(answers.get(key) or "").strip()
        if val:
            meal_parts.append(f"{key}: {val}")
    if meal_parts:
        provided["diet"] = "; ".join(meal_parts)
    risk = str(answers.get("diet_risk") or "").strip()
    risk_detail = str(answers.get("diet_risk_detail") or "").strip()
    if risk and risk != "没有":
        risk_text = f"饮食风险: {risk}"
        if risk_detail:
            risk_text += f" ({risk_detail})"
        if provided.get("diet"):
            provided["diet"] += f"; {risk_text}"
        else:
            provided["diet"] = risk_text


async def _generate_profile_and_documents(service_case_id: int) -> None:
    async with SessionLocal() as session:
        service_case = await get_active_service_case(session, service_case_id)
        if not service_case:
            return
        try:
            await generate_profile_and_documents(session, service_case, auto_send=get_settings().auto_send_documents)
        except Exception as exc:
            logger.exception("Failed to generate profile and documents for service_case_id=%s", service_case_id)
            await session.rollback()
            failed_case = await get_active_service_case(session, service_case_id)
            if failed_case:
                _append_generation_error_note(failed_case, exc)
                await session.commit()


async def _personalized_checkin_fields(session: AsyncSession, service_case: ServiceCase) -> list[dict[str, Any]]:
    # The reviewed execution document is the source of truth for the daily form.
    # This also repairs older profiles that persisted the generic fallback fields.
    document_fields = await _personalized_checkin_fields_from_latest_document(session, service_case.id)
    if document_fields:
        return document_fields

    latest_profile = await session.scalar(
        select(UserProfile)
        .where(UserProfile.service_case_id == service_case.id)
        .order_by(UserProfile.created_at.desc())
        .limit(1)
    )
    fields = []
    if latest_profile and latest_profile.profile_json:
        fields = list(latest_profile.profile_json.get("personalized_checkin_fields") or [])
    if len(fields) >= 5:
        return fields

    return [dict(f) for f in DEFAULT_PERSONALIZED_CHECKIN_FIELDS]


async def _personalized_checkin_fields_from_latest_document(
    session: AsyncSession,
    service_case_id: int,
) -> list[dict[str, Any]]:
    document = await session.scalar(
        select(UserDocument)
        .where(
            UserDocument.service_case_id == service_case_id,
            UserDocument.document_type == DocumentType.DAY28_EXECUTION_STANDARD,
        )
        .order_by(UserDocument.created_at.desc())
        .limit(1)
    )
    if not document or not isinstance(document.content_json, dict):
        return []
    return personalized_checkin_fields_from_document_payloads(
        {DocumentType.DAY28_EXECUTION_STANDARD.value: document.content_json}
    )


async def _send_intake_submit_reply(service_case_id: int) -> None:
    async with SessionLocal() as session:
        service_case = await get_active_service_case(session, service_case_id)
        if not service_case:
            return
        schedule = await get_checkin_questionnaire_schedule(session)
        reply = schedule.get("intake_submit_reply")
        if not isinstance(reply, dict) or reply.get("enabled", True) is False:
            return
        user = await session.get(User, service_case.user_id) if service_case.user_id else None
        plan = service_case.service_plan_start_date
        if plan:
            plan_start_date = plan.strftime("%Y年%m月%d日")
            plan_start_line = (
                f"你的服务计划于 {plan_start_date} 正式开始（打卡也从那天开始）。"
                "在那之前，建议先把这两份专属文件好好看一遍、提前熟悉，到时候就能顺利开跑～"
            )
        else:
            plan_start_date = ""
            plan_start_line = "收到文件后先好好看看内容，提前熟悉，有任何问题随时在群里问我们～"
        content = _format_template(
            str(reply.get("message") or ""),
            {
                "user_name": user.name if user else "",
                "case_no": service_case.case_no,
                "group_id": service_case.group_id,
                "plan_start_date": plan_start_date,
                "plan_start_line": plan_start_line,
            },
        ).strip()
        if not content:
            return
        result = await send_channel_text(service_case.group_id, content)
        session.add(
            Message(
                service_case_id=service_case.id,
                group_id=service_case.group_id,
                wecom_message_id=result.message_id,
                direction=MessageDirection.OUTBOUND,
                sender_type=MessageSenderType.BOT,
                message_type="markdown",
                content=content,
                purpose=MessagePurpose.CHAT,
                status=MessageStatus.SENT if result.success else MessageStatus.FAILED,
                error=result.error,
            )
        )
        await session.commit()


def _append_generation_error_note(service_case: ServiceCase, exc: Exception) -> None:
    timestamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
    settings = get_settings()
    service_case.status = ServiceCaseStatus.QUESTIONNAIRE_SUBMITTED
    message = (
        f"[{timestamp}] 自动生成画像/交付文件失败：{type(exc).__name__}: {exc}"
        f"（AI_MAX_TOKENS={settings.ai_max_tokens}）"
    )
    existing = service_case.notes or ""
    service_case.notes = (f"{existing}\n{message}" if existing else message)[-2000:]


def _format_template(template: str, context: dict[str, object]) -> str:
    class _SafeDict(dict[str, object]):
        def __missing__(self, key: str) -> str:
            return "{" + key + "}"

    return template.format_map(_SafeDict(context))


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

    # 「计划开始日期」控制服务计时起点(打卡/阶段报告/看板服务天数都跟它)；未填/解析失败则留空，回退 started_at
    plan_start = _parse_plan_start_date(
        _answer_by_label(fields, answers, ("计划开始日期", "计划开始", "开始日期"))
    )
    if plan_start:
        service_case.service_plan_start_date = plan_start


def _parse_plan_start_date(raw: str | None):
    """解析问卷『计划开始日期』答案(input type=date 为 YYYY-MM-DD)，返回 date 或 None。"""
    if not raw or not isinstance(raw, str):
        return None
    text = raw.strip()[:10]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


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
