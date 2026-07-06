from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field
from sqlalchemy import select
from langchain_core.tools import BaseTool, StructuredTool

from app.core.config import Settings
from app.db.session import SessionLocal
from app.models.checkin import Checkin
from app.models.message import (
    Message,
    MessageDirection,
    MessagePurpose,
    MessageSenderType,
    MessageStatus,
)
from app.models.service_case import ServiceCase
from app.models.user_document import DocumentStatus, DocumentType, UserDocument
from app.models.user_profile import UserProfile
from app.models.user_state_profile import UserStateProfile
from app.services.agent.state import AgentRunContext
from app.services.service_case_queries import effective_service_day, effective_start_at, select_active_service_cases


class DaysArgs(BaseModel):
    days: int = Field(default=7, ge=1, le=30, description="Lookback days, from 1 to 30.")


class ListFilesArgs(BaseModel):
    limit: int = Field(default=10, ge=1, le=20, description="How many recent uploaded files to list, 1 to 20.")


class ReadFileArgs(BaseModel):
    message_id: int | None = Field(
        default=None,
        description="Read this exact file by its message_id (from list_group_files). Highest priority.",
    )
    filename: str | None = Field(
        default=None,
        description="Fuzzy match a file by (partial) filename, case-insensitive. Used when message_id is not given.",
    )


class ListDeliveryDocumentsArgs(BaseModel):
    document_type: DocumentType | None = Field(
        default=None,
        description=(
            "Optional exact UserDocument.document_type enum filter. Valid values: "
            "energy_restart_prescription, day28_execution_standard, stage_report, graduation_report."
        ),
    )
    limit: int = Field(default=12, ge=1, le=30, description="How many recent delivery documents to list, 1 to 30.")


class ReadDeliveryDocumentArgs(BaseModel):
    document_id: int | None = Field(
        default=None,
        description="Read this exact delivery document by document_id from list_delivery_documents. Highest priority.",
    )
    document_type: DocumentType | None = Field(
        default=None,
        description=(
            "Optional exact UserDocument.document_type enum filter when document_id is unavailable. "
            "For stage_report, prefer list_delivery_documents then document_id because there can be multiple reports."
        ),
    )


# 单次返回给 LLM 的文件正文上限（字符），控 token；入库全文上限更大（见 engine._MAX_FILE_TEXT_CHARS）。
_MAX_TOOL_FILE_CHARS = 4000
# 单次返回给 LLM 的交付文档正文上限（字符），避免阶段报告/执行单过长撑爆上下文。
_MAX_TOOL_DOCUMENT_CHARS = 6000
# 列表/定位时最多回溯的入站文件消息条数（防一次扫全群历史）。
_MAX_FILE_SCAN = 20
# 列表/定位时最多回溯的交付文档条数。
_MAX_DOCUMENT_SCAN = 30
# 一次最多回溯的已发打卡链接条数（一个服务窗口最多 28 天，留足余量）。
_MAX_CHECKIN_LINK_ROWS = 40
# 从提醒正文里抽取打卡/问卷短链（形如 https://admin.humanburning.ai/s/xxxx）。
_CHECKIN_LINK_RE = re.compile(r"https?://\S+/s/\S+")

_DOCUMENT_TYPE_LABELS: dict[DocumentType, str] = {
    DocumentType.ENERGY_RESTART_PRESCRIPTION: "能量重启处方单",
    DocumentType.DAY28_EXECUTION_STANDARD: "28天执行单",
    DocumentType.STAGE_REPORT: "阶段报告",
    DocumentType.GRADUATION_REPORT: "毕业报告",
}


def _file_record(row: "Message") -> dict[str, Any] | None:
    """把一条 file 消息压成给 LLM 的文件条目；没有 file_info 的返回 None。"""
    analysis = row.analysis_json if isinstance(row.analysis_json, dict) else {}
    info = analysis.get("file_info")
    if not isinstance(info, dict):
        return None
    status = info.get("parse_status") or "unknown"
    text = str(info.get("extracted_text") or "")
    return {
        "message_id": row.id,
        "filename": info.get("filename") or "",
        "uploaded_at": row.created_at.isoformat() if row.created_at else None,
        "sender_type": row.sender_type.value if row.sender_type else None,
        "parse_status": status,
        "readable": bool(status == "ok" and text.strip()),
        "text_length": len(text),
    }


def _document_record(row: UserDocument) -> dict[str, Any]:
    content = row.content_md or ""
    return {
        "document_id": row.id,
        "document_type": row.document_type.value,
        "document_type_label": _DOCUMENT_TYPE_LABELS.get(row.document_type, row.document_type.value),
        "title": row.title,
        "status": row.status.value,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "readable": bool(content.strip()),
        "text_length": len(content),
    }


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

    async def list_group_files(limit: int = 10) -> dict[str, Any]:
        """列出当前服务单最近上传的入站文件（含 message_id / 文件名 / 上传时间 / 解析状态）。"""
        limit = max(1, min(int(limit) if isinstance(limit, (int, float)) else 10, 20))
        async with SessionLocal() as session:
            rows = await session.scalars(
                select(Message)
                .where(Message.service_case_id == context.service_case_id)
                .where(Message.message_type == "file")
                .order_by(Message.id.desc())
                .limit(_MAX_FILE_SCAN)
            )
            files: list[dict[str, Any]] = []
            for row in rows:
                record = _file_record(row)
                if record is not None:
                    files.append(record)
                if len(files) >= limit:
                    break
            if not files:
                return {"ok": True, "count": 0, "files": [], "note": "本群最近没有可读取的上传文件。"}
            return {"ok": True, "count": len(files), "files": files}

    async def read_group_file(message_id: int | None = None, filename: str | None = None) -> dict[str, Any]:
        """读取本群上传文件的已解析正文（当前仅支持 docx）。

        定位优先级：message_id > filename 模糊匹配 > 最新文件。读不到内容时如实返回 note，不编造。
        """
        async with SessionLocal() as session:
            rows = list(
                await session.scalars(
                    select(Message)
                    .where(Message.service_case_id == context.service_case_id)
                    .where(Message.message_type == "file")
                    .order_by(Message.id.desc())
                    .limit(_MAX_FILE_SCAN)
                )
            )

            target: Message | None = None
            if message_id is not None:
                target = next((row for row in rows if row.id == message_id), None)
                if target is None:
                    return {
                        "ok": True,
                        "filename": None,
                        "text": "",
                        "note": f"没找到 message_id={message_id} 对应的文件，可先用 list_group_files 确认。",
                    }
            elif filename and filename.strip():
                needle = filename.strip().lower()
                for row in rows:
                    info = (row.analysis_json or {}).get("file_info") if isinstance(row.analysis_json, dict) else None
                    name = (info.get("filename") if isinstance(info, dict) else "") or ""
                    if needle in name.lower():
                        target = row
                        break
                if target is None:
                    return {
                        "ok": True,
                        "filename": None,
                        "text": "",
                        "note": f"没找到文件名包含“{filename}”的文件，可先用 list_group_files 看看有哪些。",
                    }
            else:
                # 默认取最新一条文件消息（如实反映“最新这份”的可读状态；有没有别的由 list_group_files 说明）
                target = rows[0] if rows else None

            if target is None:
                return {"ok": True, "filename": None, "text": "", "note": "本群最近没有可读取的上传文件。"}

            record = _file_record(target)
            if record is None:
                return {
                    "ok": True,
                    "filename": None,
                    "message_id": target.id,
                    "parse_status": "missing",
                    "text": "",
                    "note": "这份文件还没有可读取的解析正文。",
                }
            info = target.analysis_json.get("file_info", {})
            text = str(info.get("extracted_text") or "")
            if record["readable"]:
                return {
                    "ok": True,
                    "message_id": target.id,
                    "filename": record["filename"],
                    "parse_status": record["parse_status"],
                    "text": text[:_MAX_TOOL_FILE_CHARS],
                    "truncated": len(text) > _MAX_TOOL_FILE_CHARS,
                }
            # 找到文件但内容不可读（格式不支持/解析失败/空）：如实告知，别让模型瞎编。
            return {
                "ok": True,
                "message_id": target.id,
                "filename": record["filename"],
                "parse_status": record["parse_status"],
                "text": "",
                "note": "这份文件暂时读不了内容（可能不是 docx 或解析失败）。",
            }

    async def list_delivery_documents(
        document_type: DocumentType | None = None,
        limit: int = 12,
    ) -> dict[str, Any]:
        """列出当前服务单已发送给群里的交付文档，供 LLM 选择 document_id 后精确读取。"""
        limit = max(1, min(int(limit) if isinstance(limit, (int, float)) else 12, 30))
        async with SessionLocal() as session:
            stmt = (
                select(UserDocument)
                .where(
                    UserDocument.service_case_id == context.service_case_id,
                    UserDocument.status == DocumentStatus.SENT,
                )
                .order_by(UserDocument.created_at.desc(), UserDocument.id.desc())
                .limit(min(_MAX_DOCUMENT_SCAN, limit))
            )
            if document_type is not None:
                stmt = stmt.where(UserDocument.document_type == document_type)
            documents = [_document_record(row) for row in await session.scalars(stmt)]
            if not documents:
                doc_hint = (
                    f"类型为 {document_type.value} 的已发送交付文档"
                    if document_type is not None
                    else "已发送交付文档"
                )
                return {"ok": True, "count": 0, "documents": [], "note": f"当前服务单还没有{doc_hint}。"}
            return {"ok": True, "count": len(documents), "documents": documents}

    async def read_delivery_document(
        document_id: int | None = None,
        document_type: DocumentType | None = None,
    ) -> dict[str, Any]:
        """读取当前服务单已发送给群里的交付文档正文。

        定位优先级：document_id > document_type。阶段报告一类多份，优先 list_delivery_documents 后按 id 读。
        """
        async with SessionLocal() as session:
            if document_id is not None:
                document = await session.scalar(
                    select(UserDocument).where(
                        UserDocument.id == document_id,
                        UserDocument.service_case_id == context.service_case_id,
                        UserDocument.status == DocumentStatus.SENT,
                    )
                )
                if document is None:
                    return {
                        "ok": True,
                        "document_id": document_id,
                        "text": "",
                        "note": f"没找到 document_id={document_id} 对应的当前服务单已发送交付文档，可先用 list_delivery_documents 确认。",
                    }
            elif document_type is not None:
                rows = list(
                    await session.scalars(
                        select(UserDocument)
                        .where(
                            UserDocument.service_case_id == context.service_case_id,
                            UserDocument.document_type == document_type,
                            UserDocument.status == DocumentStatus.SENT,
                        )
                        .order_by(UserDocument.created_at.desc(), UserDocument.id.desc())
                        .limit(_MAX_DOCUMENT_SCAN)
                    )
                )
                if not rows:
                    return {
                        "ok": True,
                        "document_type": document_type.value,
                        "text": "",
                        "note": f"当前服务单还没有类型为 {document_type.value} 的已发送交付文档。",
                    }
                if len(rows) > 1:
                    return {
                        "ok": True,
                        "document_type": document_type.value,
                        "text": "",
                        "candidates": [_document_record(row) for row in rows],
                        "note": "这个类型下有多份文档，请先看 candidates，再用 document_id 精确读取。",
                    }
                document = rows[0]
            else:
                return {
                    "ok": True,
                    "text": "",
                    "note": "请先用 list_delivery_documents 查看当前服务单有哪些已发送交付文档，再传 document_id 精确读取。",
                }

            record = _document_record(document)
            text = document.content_md or ""
            if not text.strip():
                return {
                    "ok": True,
                    **record,
                    "text": "",
                    "note": "这份交付文档没有可读取的 Markdown 正文。",
                }
            return {
                "ok": True,
                **record,
                "text": text[:_MAX_TOOL_DOCUMENT_CHARS],
                "truncated": len(text) > _MAX_TOOL_DOCUMENT_CHARS,
            }

    async def get_sent_checkin_links() -> dict[str, Any]:
        """查本服务单已经发出去的打卡/问卷链接：哪天、几点发的、链接是什么、是否成功。
        用于用户问“昨天打卡你发了吗/打卡链接是不是你发的”时核实，别推给班主任也别猜。"""
        async with SessionLocal() as session:
            service_case = await session.scalar(
                select_active_service_cases(ServiceCase.id == context.service_case_id)
            )
            if service_case is None:
                return {"ok": False, "error": "当前服务单不存在或已删除。"}
            tz = ZoneInfo(settings.reminder_timezone)
            rows = await session.scalars(
                select(Message)
                .where(
                    Message.service_case_id == context.service_case_id,
                    Message.direction == MessageDirection.OUTBOUND,
                    Message.sender_type == MessageSenderType.SYSTEM,
                    Message.purpose == MessagePurpose.REMINDER,
                    Message.status == MessageStatus.SENT,
                    Message.content.like("%/s/%"),
                )
                .order_by(Message.created_at.asc())
                .limit(_MAX_CHECKIN_LINK_ROWS)
            )
            items: list[dict[str, Any]] = []
            for row in rows:
                link_match = _CHECKIN_LINK_RE.search(row.content or "")
                sent_at = row.created_at
                sent_local = sent_at.astimezone(tz) if sent_at else None
                day = effective_service_day(service_case, sent_local, tz) if sent_local else None
                items.append(
                    {
                        "service_day": day,
                        "service_day_text": f"第 {day} 天" if isinstance(day, int) and day >= 1 else None,
                        "sent_date": sent_local.date().isoformat() if sent_local else None,
                        "sent_time": sent_local.strftime("%H:%M") if sent_local else None,
                        "link": link_match.group(0) if link_match else None,
                    }
                )
            return {
                "ok": True,
                "count": len(items),
                "items": items,
                "note": "本服务单还没发过打卡链接。" if not items else "",
            }

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
            coroutine=get_sent_checkin_links,
            name="get_sent_checkin_links",
            description=(
                "List the check-in / questionnaire links THIS service case has already sent (these are sent "
                "automatically by you/the system, not by human staff). Returns for each: service_day, sent_date, "
                "sent_time, link. Call this when the user asks whether/when a check-in link was sent (e.g. "
                "'did you send yesterday's check-in', 'wasn't the check-in link from you'); answer from the "
                "records, do not attribute it to human staff and do not guess."
            ),
        ),
        StructuredTool.from_function(
            coroutine=list_delivery_documents,
            name="list_delivery_documents",
            description=(
                "List delivery documents already sent to the group for THIS service case, newest first. Returns document_id, "
                "document_type (strict UserDocument.document_type enum), document_type_label, title, status, "
                "timestamps, readable, and text_length. Use this before reading stage reports or when the user "
                "does not identify an exact document, because stage_report can have multiple documents. Draft, "
                "reviewing, archived, or otherwise unsent documents are intentionally hidden from the group-chat agent."
            ),
            args_schema=ListDeliveryDocumentsArgs,
        ),
        StructuredTool.from_function(
            coroutine=read_delivery_document,
            name="read_delivery_document",
            description=(
                "Read the Markdown body of an already-sent delivery document for THIS service case. Prefer document_id from "
                "list_delivery_documents for exact reads. document_type is a strict UserDocument.document_type "
                "enum and may be used only when there is a single matching document; if multiple documents match "
                "(especially stage_report), the tool returns candidates and asks for document_id. Unsent documents "
                "are not readable from this group-chat tool."
            ),
            args_schema=ReadDeliveryDocumentArgs,
        ),
        StructuredTool.from_function(
            coroutine=list_group_files,
            name="list_group_files",
            description=(
                "List recent files uploaded into the current group, newest first. Returns for each: "
                "message_id, filename, uploaded_at, sender_type, parse_status, readable, text_length. "
                "Use this first when the user references a non-latest file (e.g. 'the previous one', "
                "a filename, or 'the second file') so you can then read the right one by message_id."
            ),
            args_schema=ListFilesArgs,
        ),
        StructuredTool.from_function(
            coroutine=read_group_file,
            name="read_group_file",
            description=(
                "Read the extracted text of a file uploaded into the current group (currently docx only). "
                "Priority: message_id (exact) > filename (fuzzy, case-insensitive) > latest file when no "
                "argument is given. Use no argument for 'this/the one just sent'; use list_group_files first "
                "then pass message_id for 'the previous/second/that named file'. Returns note when the target "
                "cannot be read; never fabricate file contents."
            ),
            args_schema=ReadFileArgs,
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
