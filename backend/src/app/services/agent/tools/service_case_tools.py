from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field
from sqlalchemy import select
from langchain_core.tools import BaseTool, StructuredTool

from app.core.config import Settings
from app.db.session import SessionLocal
from app.models.checkin import Checkin
from app.models.message import Message
from app.models.service_case import ServiceCase
from app.models.user_document import UserDocument
from app.models.user_profile import UserProfile
from app.models.user_state_profile import UserStateProfile
from app.services.agent.state import AgentRunContext
from app.services.service_case_queries import effective_service_day, effective_start_at, select_active_service_cases


class DaysArgs(BaseModel):
    days: int = Field(default=7, ge=1, le=30, description="Lookback days, from 1 to 30.")


# 单次返回给 LLM 的文件正文上限（字符），控 token；入库全文上限更大（见 engine._MAX_FILE_TEXT_CHARS）。
_MAX_TOOL_FILE_CHARS = 4000


def build_service_case_tools(*, context: AgentRunContext, settings: Settings) -> list[BaseTool]:
    async def get_service_progress() -> dict[str, Any]:
        async with SessionLocal() as session:
            service_case = await session.scalar(select_active_service_cases(ServiceCase.id == context.service_case_id))
            if service_case is None:
                return {"ok": False, "error": "当前服务单不存在或已删除。"}
            start = effective_start_at(service_case)
            tz = ZoneInfo(settings.reminder_timezone)
            now = datetime.now(tz)
            day = effective_service_day(service_case, now, tz)
            docs = await session.scalars(
                select(UserDocument)
                .where(UserDocument.service_case_id == context.service_case_id)
                .order_by(UserDocument.created_at.desc())
            )
            document_statuses = [
                {"type": item.document_type.value, "title": item.title, "status": item.status.value}
                for item in list(docs)[:8]
            ]
            return {
                "ok": True,
                "status": service_case.status.value,
                "plan_start_date": service_case.service_plan_start_date.isoformat()
                if service_case.service_plan_start_date
                else None,
                "started_at": start.isoformat() if start else None,
                "service_day": day,
                "service_day_text": f"第 {day} 天" if day >= 1 else "服务尚未正式开始",
                "questionnaire_completed": service_case.status.value != "created",
                "documents": document_statuses,
            }

    async def get_recent_checkins(days: int = 7) -> dict[str, Any]:
        days = _clamp_days(days)
        async with SessionLocal() as session:
            rows = await _recent_checkins(session, context.service_case_id, days)
            return {
                "ok": True,
                "days": days,
                "count": len(rows),
                "items": [
                    {
                        "date": row.checkin_date.isoformat(),
                        "content": row.content[:300],
                        "parsed": row.parsed_json or {},
                    }
                    for row in rows
                ],
                "note": "数据不足，不能判断趋势。" if len(rows) < 2 else "",
            }

    async def get_weight_trend(days: int = 7) -> dict[str, Any]:
        days = _clamp_days(days)
        async with SessionLocal() as session:
            rows = await _recent_checkins(session, context.service_case_id, days)
            initial_weight = await _initial_weight_from_profile(session, context.service_case_id)
        points: list[dict[str, Any]] = []
        for row in rows:
            weight = _extract_float((row.parsed_json or {}).get("weight"))
            if weight is not None:
                points.append({"date": row.checkin_date.isoformat(), "weight": weight})
        if len(points) < 2:
            note = "体重数据不足，不能判断趋势。"
            if initial_weight is not None and not points:
                note = "暂无打卡体重，只有入营初始体重可参考，不能判断趋势。"
            return {
                "ok": True,
                "days": days,
                "points": points,
                "initial_weight": initial_weight,
                "trend": None,
                "note": note,
            }
        delta = round(points[-1]["weight"] - points[0]["weight"], 2)
        direction = "下降" if delta < 0 else ("上升" if delta > 0 else "持平")
        return {"ok": True, "days": days, "points": points, "delta_kg": delta, "direction": direction}

    async def read_group_file() -> dict[str, Any]:
        """读取本群最近上传文件的已解析正文（当前仅支持 docx）。"""
        async with SessionLocal() as session:
            rows = await session.scalars(
                select(Message)
                .where(Message.service_case_id == context.service_case_id)
                .where(Message.message_type == "file")
                .order_by(Message.id.desc())
                .limit(5)
            )
            for row in rows:
                analysis = row.analysis_json if isinstance(row.analysis_json, dict) else {}
                info = analysis.get("file_info")
                if not isinstance(info, dict):
                    return {
                        "ok": True,
                        "filename": None,
                        "parse_status": "missing",
                        "text": "",
                        "note": "最近这份文件还没有可读取的解析正文。",
                    }
                filename = info.get("filename") or ""
                status = info.get("parse_status") or "unknown"
                text = str(info.get("extracted_text") or "")
                if status == "ok" and text.strip():
                    return {
                        "ok": True,
                        "filename": filename,
                        "parse_status": status,
                        "text": text[:_MAX_TOOL_FILE_CHARS],
                        "truncated": len(text) > _MAX_TOOL_FILE_CHARS,
                    }
                # 找到最近的文件但内容不可读（格式不支持/解析失败/空）：如实告知，别让模型瞎编。
                return {
                    "ok": True,
                    "filename": filename,
                    "parse_status": status,
                    "text": "",
                    "note": "这份文件暂时读不了内容（可能不是 docx 或解析失败）。",
                }
            return {"ok": True, "filename": None, "text": "", "note": "本群最近没有可读取的上传文件。"}

    async def get_user_state_profile() -> dict[str, Any]:
        async with SessionLocal() as session:
            row = await session.scalar(
                select(UserStateProfile)
                .where(UserStateProfile.service_case_id == context.service_case_id)
                .order_by(UserStateProfile.updated_at.desc())
                .limit(1)
            )
            if row is None:
                return {"ok": True, "state": None, "note": "暂无用户动态状态画像。"}
            return {"ok": True, "summary": row.summary, "state": row.state_json or {}}

    return [
        StructuredTool.from_function(
            coroutine=get_service_progress,
            name="get_service_progress",
            description="Read the current group's service progress, service day, questionnaire and document status.",
        ),
        StructuredTool.from_function(
            coroutine=get_recent_checkins,
            name="get_recent_checkins",
            description="Read recent structured check-in records for the current service case only.",
            args_schema=DaysArgs,
        ),
        StructuredTool.from_function(
            coroutine=get_weight_trend,
            name="get_weight_trend",
            description="Calculate recent weight trend from structured check-in records for the current service case only.",
            args_schema=DaysArgs,
        ),
        StructuredTool.from_function(
            coroutine=get_user_state_profile,
            name="get_user_state_profile",
            description="Read the latest dynamic user state profile for the current service case only.",
        ),
        StructuredTool.from_function(
            coroutine=read_group_file,
            name="read_group_file",
            description=(
                "Read the extracted text of the most recent file uploaded into the current group "
                "(currently docx only). Use this whenever the user or staff asks about a document "
                "they sent in the group. Returns note when no readable file exists."
            ),
        ),
    ]


async def _recent_checkins(session, service_case_id: int, days: int) -> list[Checkin]:
    result = await session.scalars(
        select(Checkin)
        .where(Checkin.service_case_id == service_case_id)
        .order_by(Checkin.checkin_date.desc(), Checkin.created_at.desc())
        .limit(days)
    )
    return list(reversed(list(result)))


async def _initial_weight_from_profile(session, service_case_id: int) -> float | None:
    profile = await session.scalar(
        select(UserProfile)
        .where(UserProfile.service_case_id == service_case_id)
        .order_by(UserProfile.created_at.desc())
        .limit(1)
    )
    if profile is None:
        return None
    return _extract_initial_weight(profile.profile_json or {})


def _extract_initial_weight(profile_json: dict[str, Any]) -> float | None:
    overview = profile_json.get("overview") if isinstance(profile_json.get("overview"), dict) else {}
    for key in ("weight_kg", "initial_weight_kg", "current_weight_kg", "weight"):
        weight = _extract_float(overview.get(key))
        if weight is not None:
            return weight
    height_weight = overview.get("height_weight")
    if isinstance(height_weight, str):
        for part in height_weight.split("/"):
            if "kg" in part.lower() or "公斤" in part:
                weight = _extract_float(part)
                if weight is not None:
                    return weight
    return None


def _clamp_days(days: int) -> int:
    try:
        value = int(days)
    except (TypeError, ValueError):
        value = 7
    return max(1, min(value, 30))


def _extract_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if isinstance(value, str):
            value = value.replace("kg", "").replace("公斤", "").strip()
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None
