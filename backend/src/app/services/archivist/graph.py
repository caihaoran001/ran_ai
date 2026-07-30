"""档案官运行流水线：load_context → extract → commit。

LLM 只用于判断(extract)，落库用确定性代码(commit)。整批增量只调用一次模型，在同一个
响应里上报成效、画像、状态、FAQ、两类滚动小结和优秀案例候选；commit 将全部产物与
水位线写进同一事务。档案官是成效/画像的**唯一**抽取入口(聊天热路径已不再实时抽)。

模块路径保留为 ``archivist.graph`` 以兼容既有调用方；运行时是普通 async 流水线，不使用 LangGraph。
"""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, TypedDict
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.archivist_case_memory import ArchivistCaseMemory
from app.models.archivist_extract_trace import ArchivistExtractTrace
from app.models.archivist_progress import ArchivistProgress
from app.models.message import Message, MessagePurpose, MessageSenderType
from app.models.outcome import OutcomeEvent
from app.models.service_case import ServiceCase
from app.models.user_state_profile import UserStateProfile
from app.services.archivist.corrections import apply_reported_corrections
from app.services.archivist.extract_agent import run_extract_agent
from app.services.knowledge_evolution import (
    _existing_case_summaries,
    create_draft_candidate,
    create_qa_drafts_from_archivist,
)
from app.services.outcome_engine import apply_archived_outcome

logger = logging.getLogger(__name__)

_SH_TZ = ZoneInfo("Asia/Shanghai")

# 首次为某服务单建进度行时，把水位线播到 N 天前：只回填最近 N 天，跳过远古历史，
# 避免存量单第一次被触发就把整段聊天史一次性喂抽取（海量 LLM 调用）。稳态下增量本就很小。
INITIAL_BACKFILL_DAYS = 3


class ArchivistPipelineState(TypedDict, total=False):
    watermark_from: int | None
    watermark_to: int | None
    # {date_iso: [{"id": int, "text": str, "role": str, "sender_type": str}]}
    delta_by_date: dict[str, list[dict]]
    # 本轮推动水位线的用户/员工入站消息，以及成效 anchor 专用用户消息。
    delta_message_ids: list[int]
    delta_user_message_ids: list[int]
    archive_context: dict[str, Any]
    # 模型抽出的成效事件/稳定画像/当前状态（累计跨整批，commit 里一次性写真表）
    outcome_candidates: list[dict]
    profile_candidates: list[dict]
    state_candidates: list[dict]
    correction_candidates: list[dict]
    faq_candidates: list[dict]
    memory_candidate: dict[str, Any] | None
    case_candidates: list[dict]
    # 抽取观测留痕(R1):每批一条 {target_date, emitted, kept, dropped}
    extract_traces: list[dict]


@dataclass
class ArchivistRunResult:
    watermark_from: int | None = None
    watermark_to: int | None = None
    delta_count: int = 0
    # 真正进入本轮模型输入的聊天消息（含为班主任纠正补入的助教上下文）。
    input_message_ids: list[int] = field(default_factory=list)
    # 成效/稳定画像/当前状态直写结果（模型抽出并落库的条数）
    outcome_written: int = 0
    profile_written: int = 0
    state_written: int = 0
    personal_corrections_written: int = 0
    corrections_recorded: int = 0
    corrections_skipped: int = 0
    faq_candidates: list[dict] = field(default_factory=list)
    faq_written: int = 0
    memory_written: int = 0
    user_summary_written: int = 0
    case_material_written: int = 0
    case_written: int = 0
    emitted_count: int = 0
    dropped_count: int = 0
    # 只记录确定性写库后的业务变化，供运营按批次追溯；不参与任何业务判断。
    change_details: list[dict[str, Any]] = field(default_factory=list)


def _sh_date(dt: datetime) -> date:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_SH_TZ).date()


def _audit_text(value: Any, *, limit: int = 2000) -> str | None:
    """把变更值压成可读、可持久化的审计文本，不让运行账本无限膨胀。"""
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
    elif isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    else:
        text = str(value).strip()
    return text[:limit] or None


def _state_change_details(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """只展示最终投影中真正新增或改变的 current 事实。"""
    before_domains = (
        before.get("domains")
        if isinstance(before, dict) and isinstance(before.get("domains"), dict)
        else {}
    )
    after_domains = (
        after.get("domains")
        if isinstance(after, dict) and isinstance(after.get("domains"), dict)
        else {}
    )
    changes: list[dict[str, Any]] = []
    for domain_key, domain in after_domains.items():
        if not isinstance(domain, dict):
            continue
        previous_domain = before_domains.get(domain_key)
        previous_current = (
            previous_domain.get("current")
            if isinstance(previous_domain, dict)
            and isinstance(previous_domain.get("current"), dict)
            else {}
        )
        current = domain.get("current")
        if not isinstance(current, dict):
            continue
        for field_key, item in current.items():
            if not isinstance(item, dict) or previous_current.get(field_key) == item:
                continue
            source = str(item.get("source") or "")
            # 成效本身已用 OutcomeEvent 的精确创建结果展示，避免投影后重复出现一次。
            if source == "archivist":
                continue
            if "profile_signal" in source or "profile_guardrail" in source:
                kind = "profile_fact"
            elif "state_signal" in source:
                kind = "state_fact"
            elif "staff_correction" in source or "staff_guidance" in source:
                kind = "personal_guidance"
            else:
                kind = "user_state"
            previous_item = previous_current.get(field_key)
            changes.append(
                {
                    "kind": kind,
                    "action": "updated" if previous_item else "added",
                    "title": f"{domain.get('label') or domain_key} · {field_key}",
                    "before": _audit_text(
                        previous_item.get("value")
                        if isinstance(previous_item, dict)
                        else None
                    ),
                    "after": _audit_text(item.get("value")),
                    "evidence": _audit_text(item.get("evidence")),
                }
            )
    return changes


class ArchivistPipeline:
    def __init__(
        self,
        session: AsyncSession,
        *,
        service_case: ServiceCase,
        progress: ArchivistProgress,
        chat_model: object | None = None,
        batch_cap: int | None = None,
        agent_run_id: int | None = None,
    ) -> None:
        self.session = session
        self.service_case = service_case
        self.progress = progress
        # chat_model 可注入（测试用 FakeToolCallingModel）；None 时 extract_agent 内部建真实模型。
        self.chat_model = chat_model
        # batch_cap：只处理最老 N 条增量（None=全量）。恢复期缩到 1 条以定位毒消息，见 trigger 的隔离逻辑。
        self.batch_cap = batch_cap
        self.agent_run_id = agent_run_id
        self.settings = get_settings()
        self.result = ArchivistRunResult()

    async def run(self) -> ArchivistRunResult:
        state: ArchivistPipelineState = {}
        state.update(await self._load_context())
        state.update(await self._extract(state))
        await self._commit(state)
        return self.result

    # --- 流水线阶段 ---

    async def _load_context(self) -> dict[str, Any]:
        """读水位线，拉用户/员工增量，并带上其间助教消息作为纠正上下文。"""
        watermark = self.progress.last_archived_message_id or 0
        delta_stmt = (
            select(Message)
            .where(
                Message.service_case_id == self.service_case.id,
                Message.id > watermark,
                Message.sender_type.in_([MessageSenderType.USER, MessageSenderType.STAFF]),
                Message.purpose == MessagePurpose.CHAT,
            )
            .order_by(Message.id.asc())
        )
        # 恢复期缩批：只取最老 N 条增量，把失败范围收敛到单条以定位毒消息（正常路径 batch_cap=None）。
        if self.batch_cap is not None:
            delta_stmt = delta_stmt.limit(self.batch_cap)
        delta_rows = (await self.session.execute(delta_stmt)).scalars().all()

        watermark_to = max((m.id for m in delta_rows), default=watermark)
        if watermark_to > watermark:
            rows = (
                await self.session.execute(
                    select(Message)
                    .where(
                        Message.service_case_id == self.service_case.id,
                        Message.id > watermark,
                        Message.id <= watermark_to,
                        Message.sender_type.in_(
                            [MessageSenderType.USER, MessageSenderType.STAFF, MessageSenderType.BOT]
                        ),
                        Message.purpose == MessagePurpose.CHAT,
                    )
                    .order_by(Message.id.asc())
                )
            ).scalars().all()
        else:
            rows = []

        # 员工消息可能晚于上一轮水位线，而被纠正的助教回复位于水位线之前。
        # 仅补最近 48 小时内、员工消息之前的最后一条助教回复作为有界上下文；是否构成纠正仍由模型判断。
        staff_rows = [m for m in delta_rows if m.sender_type == MessageSenderType.STAFF]
        if staff_rows:
            first_staff = min(staff_rows, key=lambda item: item.id)
            if not any(m.sender_type == MessageSenderType.BOT and m.id < first_staff.id for m in rows):
                staff_at = first_staff.created_at or datetime.now(timezone.utc)
                context_bot = await self.session.scalar(
                    select(Message)
                    .where(
                        Message.service_case_id == self.service_case.id,
                        Message.id < first_staff.id,
                        Message.sender_type == MessageSenderType.BOT,
                        Message.purpose == MessagePurpose.CHAT,
                        Message.created_at >= staff_at - timedelta(hours=48),
                    )
                    .order_by(Message.id.desc())
                    .limit(1)
                )
                if context_bot is not None:
                    rows.append(context_bot)
                    rows.sort(key=lambda item: item.id)

        delta_by_date: dict[str, list[dict]] = {}
        delta_message_ids = [m.id for m in delta_rows]
        delta_user_message_ids = [m.id for m in delta_rows if m.sender_type == MessageSenderType.USER]
        role_labels = {
            MessageSenderType.USER: "用户",
            MessageSenderType.STAFF: "班主任",
            MessageSenderType.BOT: "助教",
        }
        for m in rows:
            if not m.created_at:
                continue
            aj = m.analysis_json if isinstance(m.analysis_json, dict) else {}
            # 图片解析失败的消息内容只是占位符（"用户发来一张图片（图片解析失败）。"），
            # 无信息量，跳过不喂抽取。成功解析的图片描述已写进 content，正常纳入。
            if m.sender_type == MessageSenderType.USER and aj.get("image_parse_status") == "failed":
                continue
            text = (m.content or "").strip()
            if not text:
                continue
            key = _sh_date(m.created_at).isoformat()
            delta_by_date.setdefault(key, []).append(
                {
                    "id": m.id,
                    "text": text,
                    "role": role_labels[m.sender_type],
                    "sender_type": m.sender_type.value,
                }
            )

        # 跨午夜的“助教回复→班主任纠正”仍需在同一次 extract 输入中出现。
        bot_rows = [m for m in rows if m.sender_type == MessageSenderType.BOT]
        for staff in staff_rows:
            preceding = [bot for bot in bot_rows if bot.id < staff.id]
            if not preceding or not staff.created_at:
                continue
            bot = max(preceding, key=lambda item: item.id)
            staff_key = _sh_date(staff.created_at).isoformat()
            if any(item["id"] == bot.id for item in delta_by_date.get(staff_key, [])):
                continue
            delta_by_date.setdefault(staff_key, []).insert(
                0,
                {
                    "id": bot.id,
                    "text": (bot.content or "").strip(),
                    "role": "助教",
                    "sender_type": bot.sender_type.value,
                },
            )

        self.result.watermark_from = watermark or None
        self.result.watermark_to = watermark_to or None
        self.result.delta_count = len(delta_rows)
        self.result.input_message_ids = sorted(
            {
                int(item["id"])
                for messages in delta_by_date.values()
                for item in messages
                if item.get("id")
            }
        )
        archive_context = await self._load_archive_context()
        return {
            "watermark_from": watermark or None,
            "watermark_to": watermark_to or None,
            "delta_by_date": delta_by_date,
            "delta_message_ids": delta_message_ids,
            "delta_user_message_ids": delta_user_message_ids,
            "archive_context": archive_context,
        }

    async def _load_archive_context(self) -> dict[str, Any]:
        """加载已抽象的历史，不把已归档聊天原文重复喂给模型。"""
        memory = await self.session.scalar(
            select(ArchivistCaseMemory).where(
                ArchivistCaseMemory.service_case_id == self.service_case.id
            )
        )
        events = list(
            await self.session.scalars(
                select(OutcomeEvent)
                .where(OutcomeEvent.service_case_id == self.service_case.id)
                .order_by(OutcomeEvent.id.desc())
                .limit(40)
            )
        )
        events.reverse()
        existing_cases = await _existing_case_summaries(
            self.session,
            service_case_id=self.service_case.id,
        )
        return {
            "existing_user_summary": memory.summary if memory else None,
            "existing_case_material_summary": (
                memory.case_material_summary if memory else None
            ),
            # 历史消息若已抽成 outcome，只传结构化事实和来源 ID，不再传 evidence_text/原话。
            "outcome_timeline": [
                {
                    "field_key": item.field_key,
                    "event_type": item.event_type,
                    "value": item.value_text,
                    "milestone": item.milestone,
                    "occurred_at": (
                        item.occurred_at.isoformat() if item.occurred_at else None
                    ),
                    "source_message_id": item.message_id,
                }
                for item in events
            ],
            "existing_case_summaries": [
                {
                    "title": item.get("title"),
                    "category": item.get("category"),
                    "answer_summary": item.get("answer_summary"),
                }
                for item in existing_cases
            ],
        }

    async def _extract(self, state: ArchivistPipelineState) -> dict[str, Any]:
        """把整批增量交给模型一次，收集全部 report_* 产物。"""
        delta_by_date = state.get("delta_by_date") or {}
        extract_traces: list[dict] = []
        if not delta_by_date:
            return {
                "outcome_candidates": [],
                "profile_candidates": [],
                "state_candidates": [],
                "correction_candidates": [],
                "faq_candidates": [],
                "memory_candidate": None,
                "case_candidates": [],
                "extract_traces": [],
            }

        dates = sorted(delta_by_date)
        content_parts: list[str] = []
        for date_iso in dates:
            msgs = delta_by_date[date_iso]
            content_parts.append(f"【日期 {date_iso}】")
            content_parts.extend(
                f"[{item['id']}] {item['role']}：{item['text']}" for item in msgs
            )
        # extract 是唯一权威，恒抽全类；archive_plan 只负责上游触发，不裁剪本轮工具。
        collector = await run_extract_agent(
            content="\n".join(content_parts),
            service_case_id=self.service_case.id,
            enabled_kinds=[
                "outcome",
                "profile",
                "state",
                "correction",
                "faq",
                "memory",
                "case",
            ],
            chat_model=self.chat_model,
            settings=self.settings,
            archive_context=state.get("archive_context") or {},
            agent_run_id=self.agent_run_id,
            usage_message_id=state.get("watermark_to"),
        )
        trace = collector.get("_trace") or {}
        self.result.emitted_count = len(trace.get("emitted") or [])
        self.result.dropped_count = len(trace.get("dropped") or [])
        if trace.get("emitted") or trace.get("dropped"):
            extract_traces.append({"target_date": dates[-1], **trace})

        return {
            "outcome_candidates": collector.get("outcome") or [],
            "profile_candidates": collector.get("profile") or [],
            "state_candidates": collector.get("state") or [],
            "correction_candidates": collector.get("correction") or [],
            "faq_candidates": collector.get("faq") or [],
            "memory_candidate": collector.get("memory"),
            "case_candidates": collector.get("case") or [],
            "extract_traces": extract_traces,
        }

    async def _commit(self, state: ArchivistPipelineState) -> None:
        """确定性落库其他档案产物并推进水位线；不在此 commit 事务。"""
        existing_state_profile = await self.session.scalar(
            select(UserStateProfile).where(
                UserStateProfile.service_case_id == self.service_case.id
            )
        )
        state_before = deepcopy(
            existing_state_profile.state_json
            if existing_state_profile is not None
            else None
        )
        # 成效/稳定画像/当前状态直写：用本批最后一条用户消息作 anchor。
        # **不再吞异常**：写库失败必须抛出，让 trigger 层回滚事务、不推进水位线、下轮重试，
        # 否则成效/画像会随水位线前移而永久丢失（幂等由 apply_archived_outcome 去重保证重跑安全）。
        outcome_candidates = state.get("outcome_candidates") or []
        profile_candidates = state.get("profile_candidates") or []
        state_candidates = state.get("state_candidates") or []
        delta_user_message_ids = state.get("delta_user_message_ids") or []
        if (outcome_candidates or profile_candidates or state_candidates) and delta_user_message_ids:
            anchor = await self.session.get(Message, max(delta_user_message_ids))
            if anchor is not None:
                result = await apply_archived_outcome(
                    self.session,
                    service_case=self.service_case,
                    anchor_message=anchor,
                    outcome_events=outcome_candidates,
                    profile_signals=profile_candidates,
                    state_signals=state_candidates,
                )
                created_events = result.get("created_events") or []
                self.result.outcome_written = len(created_events)
                self.result.profile_written = len(result.get("profile_signals") or [])
                self.result.state_written = len(result.get("state_signals") or [])
                self.result.change_details.extend(
                    {
                        "kind": "outcome",
                        "action": "added",
                        "title": str(item.get("field_key") or "阶段成效"),
                        "after": _audit_text(item.get("value")),
                        "evidence": _audit_text(item.get("evidence_text")),
                    }
                    for item in created_events
                )

        correction_result = await apply_reported_corrections(
            self.session,
            service_case=self.service_case,
            corrections=state.get("correction_candidates") or [],
        )
        self.result.personal_corrections_written = correction_result.personal_written
        self.result.corrections_recorded = correction_result.recorded
        self.result.corrections_skipped = correction_result.skipped
        self.result.change_details.extend(
            {
                "kind": "correction",
                "action": "recorded",
                "title": "班主任通用纠错",
                "after": _audit_text(item.get("summary")),
                "evidence": _audit_text(item.get("applicability")),
            }
            for item in correction_result.general_items
        )

        # FAQ、两类滚动小结、优秀案例候选都属于本轮档案官的正式输出，
        # 与核心事实和水位线在同一事务写入；任一步失败都让整批回滚重做。
        self.result.faq_candidates = state.get("faq_candidates") or []
        faq_drafts = []
        self.result.faq_written = await create_qa_drafts_from_archivist(
            self.session,
            service_case_id=self.service_case.id,
            candidates=self.result.faq_candidates,
            created_drafts=faq_drafts,
        )
        self.result.change_details.extend(
            {
                "kind": "faq_draft",
                "action": "created",
                "title": draft.title,
                "after": _audit_text(
                    {
                        "question": draft.question,
                        "answer": draft.answer,
                    }
                ),
            }
            for draft in faq_drafts
        )

        memory_candidate = state.get("memory_candidate")
        if isinstance(memory_candidate, dict):
            user_summary = str(memory_candidate.get("user_summary") or "").strip()
            case_material_summary = str(
                memory_candidate.get("case_material_summary") or ""
            ).strip()
            if user_summary or case_material_summary:
                memory = await self.session.scalar(
                    select(ArchivistCaseMemory).where(
                        ArchivistCaseMemory.service_case_id == self.service_case.id
                    )
                )
                if memory is None:
                    memory = ArchivistCaseMemory(service_case_id=self.service_case.id)
                    self.session.add(memory)
                previous_user_summary = memory.summary
                previous_case_material = memory.case_material_summary
                if user_summary:
                    next_user_summary = user_summary[:800]
                    if next_user_summary != previous_user_summary:
                        memory.summary = next_user_summary
                        self.result.user_summary_written = 1
                        self.result.change_details.append(
                            {
                                "kind": "user_summary",
                                "action": (
                                    "created"
                                    if not previous_user_summary
                                    else "updated"
                                ),
                                "title": "用户历程小结",
                                "before": previous_user_summary,
                                "after": next_user_summary,
                            }
                        )
                if case_material_summary:
                    next_case_material = case_material_summary[:1600]
                    if next_case_material != previous_case_material:
                        memory.case_material_summary = next_case_material
                        self.result.case_material_written = 1
                        self.result.change_details.append(
                            {
                                "kind": "case_material",
                                "action": (
                                    "created"
                                    if not previous_case_material
                                    else "updated"
                                ),
                                "title": "优秀案例素材小结",
                                "before": previous_case_material,
                                "after": next_case_material,
                            }
                        )
                memory.summary_watermark_message_id = state.get("watermark_to")
                self.result.memory_written = int(
                    bool(
                        self.result.user_summary_written
                        or self.result.case_material_written
                    )
                )

        case_candidates = state.get("case_candidates") or []
        if self.result.outcome_written and case_candidates:
            candidate = case_candidates[0]
            archive_context = state.get("archive_context") or {}
            allowed_message_ids = set(state.get("delta_user_message_ids") or [])
            allowed_message_ids.update(
                int(item["source_message_id"])
                for item in (archive_context.get("outcome_timeline") or [])
                if item.get("source_message_id")
            )
            source_message_ids = []
            for value in candidate.get("source_message_ids") or []:
                try:
                    message_id = int(value)
                except (TypeError, ValueError):
                    continue
                if message_id in allowed_message_ids and message_id not in source_message_ids:
                    source_message_ids.append(message_id)
            if source_message_ids:
                material_summary = ""
                if isinstance(memory_candidate, dict):
                    material_summary = str(
                        memory_candidate.get("case_material_summary") or ""
                    ).strip()
                material_summary = material_summary or str(
                    archive_context.get("existing_case_material_summary") or ""
                ).strip()
                draft = await create_draft_candidate(
                    self.session,
                    draft_type="case",
                    title=str(candidate.get("title") or "")[:200],
                    question=str(candidate.get("question") or ""),
                    answer=str(candidate.get("answer") or ""),
                    category=str(candidate.get("category") or "")[:100] or None,
                    tags=candidate.get("tags") or [],
                    reason=str(candidate.get("reason") or "") or None,
                    evidence=str(candidate.get("evidence") or "") or None,
                    source_message_ids=source_message_ids[:20],
                    content_json={
                        **candidate,
                        "scan_type": "archivist_outcome_stable",
                        "service_case_id": self.service_case.id,
                        "watermark_to": state.get("watermark_to"),
                        "case_material_summary": material_summary or None,
                    },
                )
                self.result.case_written = 1 if draft is not None else 0
                if draft is not None:
                    self.result.change_details.append(
                        {
                            "kind": "case_draft",
                            "action": "created",
                            "title": draft.title,
                            "after": _audit_text(
                                {
                                    "background": draft.question,
                                    "case": draft.answer,
                                }
                            ),
                        }
                    )

        current_state_profile = await self.session.scalar(
            select(UserStateProfile).where(
                UserStateProfile.service_case_id == self.service_case.id
            )
        )
        self.result.change_details.extend(
            _state_change_details(
                state_before,
                (
                    current_state_profile.state_json
                    if current_state_profile is not None
                    else None
                ),
            )
        )

        # 抽取观测留痕(R1)：与档案产物/水位线同一事务落库，痕迹始终与已提交状态强一致。
        watermark_to = state.get("watermark_to")
        for tr in state.get("extract_traces") or []:
            self.session.add(
                ArchivistExtractTrace(
                    agent_run_id=self.agent_run_id,
                    service_case_id=self.service_case.id,
                    target_date=date.fromisoformat(str(tr["target_date"])),
                    watermark_to=watermark_to,
                    emitted_json=tr.get("emitted") or [],
                    kept_json=tr.get("kept") or {},
                    dropped_json=tr.get("dropped") or [],
                )
            )

        # 走到这里说明成效/画像、纠正、FAQ、小结和案例写库全部成功（任一步抛错都不会到此，
        # 由 trigger 层回滚、水位线保持不动）。此时才推进水位线并清零失败计数。
        # 即使本轮没抽到产物也要推进，避免下轮重复处理同一批增量。
        if watermark_to:
            self.progress.last_archived_message_id = watermark_to
        self.progress.last_archived_at = datetime.now(timezone.utc)
        self.progress.consecutive_failures = 0
        self.progress.last_failure_reason = None
        self.progress.last_failure_kind = None
        self.progress.last_failure_fingerprint = None
        self.progress.quarantine_candidate_failures = 0
        self.progress.quarantine_candidate_fingerprint = None


async def get_or_create_progress(
    session: AsyncSession, *, service_case_id: int
) -> ArchivistProgress:
    progress = await session.scalar(
        select(ArchivistProgress).where(
            ArchivistProgress.service_case_id == service_case_id
        )
    )
    if progress is None:
        # 播种水位线到 N 天前的最后一条用户消息，只回填最近窗口、跳过远古历史。
        cutoff = datetime.now(timezone.utc) - timedelta(days=INITIAL_BACKFILL_DAYS)
        seed = await session.scalar(
            select(func.max(Message.id)).where(
                Message.service_case_id == service_case_id,
                Message.sender_type.in_([MessageSenderType.USER, MessageSenderType.STAFF]),
                Message.created_at < cutoff,
            )
        )
        progress = ArchivistProgress(
            service_case_id=service_case_id, last_archived_message_id=seed
        )
        session.add(progress)
        await session.flush()
    return progress


async def run_archivist(
    session: AsyncSession,
    *,
    service_case: ServiceCase,
    progress: ArchivistProgress,
    chat_model: object | None = None,
    batch_cap: int | None = None,
    agent_run_id: int | None = None,
) -> ArchivistRunResult:
    """跑一次档案官（不在此 commit，由调用方在持锁事务内提交）。

    chat_model 可注入（测试用）；生产路径（trigger.run_archivist_scan）不传，走真实模型。
    batch_cap：恢复期只处理最老 N 条增量以定位毒消息（None=全量）。
    """
    pipeline = ArchivistPipeline(
        session,
        service_case=service_case,
        progress=progress,
        chat_model=chat_model,
        batch_cap=batch_cap,
        agent_run_id=agent_run_id,
    )
    return await pipeline.run()
