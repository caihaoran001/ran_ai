"""档案官运行图：load_context → extract → classify → guardrail → commit。

设计 §5：顺序焊死的多节点流水线，LLM 只用于判断(extract)，落库用确定性代码(commit)。
extract 用 Agent 形态一次性抽出打卡+成效+画像；commit 里打卡按天 upsert、成效/画像经
apply_archived_outcome 写真表(outcome_events/user_state)。档案官是成效/画像的**唯一**抽取
入口(聊天热路径已不再实时抽)。classify/guardrail 为知识沉淀等后续阶段预留的轻量节点。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, TypedDict
from zoneinfo import ZoneInfo

from langgraph.graph import END, START, StateGraph
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.archivist_progress import ArchivistProgress
from app.models.message import Message, MessagePurpose, MessageSenderType
from app.models.service_case import ServiceCase
from app.services.archivist.checkin_projection import CheckinUpsertResult, upsert_checkin
from app.services.archivist.corrections import apply_reported_corrections
from app.services.archivist.extract_agent import run_extract_agent
from app.services.outcome_engine import apply_archived_outcome

logger = logging.getLogger(__name__)

_SH_TZ = ZoneInfo("Asia/Shanghai")

# 首次为某服务单建进度行时，把水位线播到 N 天前：只回填最近 N 天，跳过远古历史，
# 避免存量单第一次被触发就把整段聊天史一次性喂抽取（海量 LLM 调用）。稳态下增量本就很小。
INITIAL_BACKFILL_DAYS = 3


class ArchivistState(TypedDict, total=False):
    watermark_from: int | None
    watermark_to: int | None
    # {date_iso: [{"id": int, "text": str, "role": str, "sender_type": str}]}
    delta_by_date: dict[str, list[dict]]
    # 本轮推动水位线的用户/员工入站消息，以及成效 anchor 专用用户消息。
    delta_message_ids: list[int]
    delta_user_message_ids: list[int]
    # {date_iso: {"fields": {...}, "evidence_ids": [int]}}
    day_candidates: dict[str, dict]
    # Agent 抽出的成效事件/画像信号（累计跨整批，commit 里一次性写真表）
    outcome_candidates: list[dict]
    profile_candidates: list[dict]
    correction_candidates: list[dict]
    faq_candidates: list[dict]
    committed: list[dict]


@dataclass
class ArchivistRunResult:
    watermark_from: int | None = None
    watermark_to: int | None = None
    delta_count: int = 0
    days_processed: int = 0
    upserts: list[dict] = field(default_factory=list)  # [{date, filled, contradictions...}]
    contradictions: list[dict] = field(default_factory=list)
    # 成效/画像直写真表结果（Agent 抽出并落库的条数）
    outcome_written: int = 0
    profile_written: int = 0
    personal_corrections_written: int = 0
    qa_drafts_created: int = 0
    corrections_skipped: int = 0
    # FAQ 候选（LLM 在 delta 抽取中顺手产出）；不在归档事务里落库，
    # 由 trigger 在归档提交成功后best-effort转成待审核 qa 草稿（走 knowledge_evolution 管线）。
    faq_candidates: list[dict] = field(default_factory=list)


def _sh_date(dt: datetime) -> date:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_SH_TZ).date()


class ArchivistGraph:
    def __init__(
        self,
        session: AsyncSession,
        *,
        service_case: ServiceCase,
        progress: ArchivistProgress,
        chat_model: object | None = None,
        batch_cap: int | None = None,
    ) -> None:
        self.session = session
        self.service_case = service_case
        self.progress = progress
        # chat_model 可注入（测试用 FakeToolCallingModel）；None 时 extract_agent 内部建真实模型。
        self.chat_model = chat_model
        # batch_cap：只处理最老 N 条增量（None=全量）。恢复期缩到 1 条以定位毒消息，见 trigger 的隔离逻辑。
        self.batch_cap = batch_cap
        self.settings = get_settings()
        self.result = ArchivistRunResult()
        self.graph = self._compile()

    def _compile(self):
        builder = StateGraph(ArchivistState)
        builder.add_node("load_context", self._load_context)
        builder.add_node("extract", self._extract)
        builder.add_node("classify", self._classify)
        builder.add_node("guardrail", self._guardrail)
        builder.add_node("commit", self._commit)
        builder.add_edge(START, "load_context")
        builder.add_edge("load_context", "extract")
        builder.add_edge("extract", "classify")
        builder.add_edge("classify", "guardrail")
        builder.add_edge("guardrail", "commit")
        builder.add_edge("commit", END)
        return builder.compile()

    async def run(self) -> ArchivistRunResult:
        await self.graph.ainvoke({})
        return self.result

    # --- 节点 ---

    async def _load_context(self, state: ArchivistState) -> dict[str, Any]:
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
        return {
            "watermark_from": watermark or None,
            "watermark_to": watermark_to or None,
            "delta_by_date": delta_by_date,
            "delta_message_ids": delta_message_ids,
            "delta_user_message_ids": delta_user_message_ids,
        }

    async def _extract(self, state: ArchivistState) -> dict[str, Any]:
        """对每个自然日的聊天原文用 Agent 一次性抽出打卡+成效+画像（LLM 判断，不落库）。

        打卡按天归入 day_candidates（commit 里按天 upsert）；成效/画像累计跨整批，commit 里
        经 apply_archived_outcome 一次性写真表。档案官是成效/画像的唯一抽取入口。
        """
        delta_by_date = state.get("delta_by_date") or {}
        day_candidates: dict[str, dict] = {}
        outcome_candidates: list[dict] = []
        profile_candidates: list[dict] = []
        correction_candidates: list[dict] = []
        faq_candidates: list[dict] = []

        for date_iso, msgs in delta_by_date.items():
            content = "\n".join(f"[{m['id']}] {m['role']}：{m['text']}" for m in msgs)
            # extract 是成效/画像/打卡的唯一权威，恒抽全类：这里写死全部 5 类，
            # **故意不采信入营层 archive_plan.kinds**。archive_plan 只是档案官触发闸门
            # （trigger.py 用它判本单要不要跑/跑多快），刻意宁滥勿缺（gate 提示词明说"抽不到也无妨"）；
            # 若让 extract 照 gate 的 kinds 裁剪，就会把可靠的真抽绑死在不可靠的闸门上、gate 漏标即漏抽，
            # 违反"禁止软硬并行、要能定位"。两处 worthy 判断本就不必一致——这是分工，不是 bug。
            collector = await run_extract_agent(
                content=content,
                service_case_id=self.service_case.id,
                enabled_kinds=["checkin", "outcome", "profile", "correction", "faq"],
                chat_model=self.chat_model,
                settings=self.settings,
            )
            fields = collector.get("checkin") or {}
            outcome_candidates.extend(collector.get("outcome") or [])
            profile_candidates.extend(collector.get("profile") or [])
            correction_candidates.extend(collector.get("correction") or [])
            faq_candidates.extend(collector.get("faq") or [])
            if fields:
                day_candidates[date_iso] = {
                    "fields": fields,
                    "evidence_ids": [m["id"] for m in msgs if m["sender_type"] == "user"],
                }
        return {
            "day_candidates": day_candidates,
            "outcome_candidates": outcome_candidates,
            "profile_candidates": profile_candidates,
            "correction_candidates": correction_candidates,
            "faq_candidates": faq_candidates,
        }

    async def _classify(self, state: ArchivistState) -> dict[str, Any]:
        """候选类型由 report 工具 schema 收紧；更复杂的跨候选分类仍预留在此节点。"""
        return {}

    async def _guardrail(self, state: ArchivistState) -> dict[str, Any]:
        """剔除空打卡；纠正候选在 commit 中校验消息角色、服务单归属和置信度。"""
        candidates = state.get("day_candidates") or {}
        kept = {k: v for k, v in candidates.items() if v.get("fields")}
        return {"day_candidates": kept}

    async def _commit(self, state: ArchivistState) -> dict[str, Any]:
        """确定性落库：按天 upsert 打卡；推进水位线 + last_archived_at。不在此 commit 事务。"""
        candidates = state.get("day_candidates") or {}
        committed: list[dict] = []
        for date_iso, payload in sorted(candidates.items()):
            checkin_date = date.fromisoformat(date_iso)
            res: CheckinUpsertResult = await upsert_checkin(
                self.session,
                service_case=self.service_case,
                checkin_date=checkin_date,
                fields=payload["fields"],
                evidence_msg_ids=payload.get("evidence_ids"),
            )
            entry = {
                "date": date_iso,
                "created": res.created,
                "filled": res.filled,
                "kept_form": res.kept_form,
                "contradictions": res.contradictions,
            }
            committed.append(entry)
            self.result.upserts.append(entry)
            self.result.contradictions.extend(res.contradictions)
        self.result.days_processed = len(committed)

        # 成效/画像直写真表：用本批最后一条用户消息作 anchor（提供 message_id/occurred_at）。
        # **不再吞异常**：写库失败必须抛出，让 trigger 层回滚事务、不推进水位线、下轮重试，
        # 否则成效/画像会随水位线前移而永久丢失（幂等由 apply_archived_outcome 去重保证重跑安全）。
        outcome_candidates = state.get("outcome_candidates") or []
        profile_candidates = state.get("profile_candidates") or []
        delta_user_message_ids = state.get("delta_user_message_ids") or []
        if (outcome_candidates or profile_candidates) and delta_user_message_ids:
            anchor = await self.session.get(Message, max(delta_user_message_ids))
            if anchor is not None:
                result = await apply_archived_outcome(
                    self.session,
                    service_case=self.service_case,
                    anchor_message=anchor,
                    outcome_events=outcome_candidates,
                    profile_signals=profile_candidates,
                )
                self.result.outcome_written = len(result.get("created_events") or [])
                self.result.profile_written = len(result.get("profile_signals") or [])

        correction_result = await apply_reported_corrections(
            self.session,
            service_case=self.service_case,
            corrections=state.get("correction_candidates") or [],
        )
        self.result.personal_corrections_written = correction_result.personal_written
        self.result.qa_drafts_created = correction_result.qa_drafts_created
        self.result.corrections_skipped = correction_result.skipped

        # FAQ 候选不在归档事务落库：交给 result，trigger 在归档提交成功后 best-effort 转 qa 草稿。
        self.result.faq_candidates = state.get("faq_candidates") or []

        # 走到这里说明打卡 upsert、成效/画像写库、纠正落库全部成功（任一步抛错都不会到此，
        # 由 trigger 层回滚、水位线保持不动）。此时才推进水位线并清零失败计数。
        # 即使本轮没抽到打卡也要推进，避免下轮重复处理同一批增量。
        watermark_to = state.get("watermark_to")
        if watermark_to:
            self.progress.last_archived_message_id = watermark_to
        self.progress.last_archived_at = datetime.now(timezone.utc)
        self.progress.consecutive_failures = 0
        self.progress.last_failure_reason = None
        return {"committed": committed}


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
) -> ArchivistRunResult:
    """跑一次档案官（不在此 commit，由调用方在持锁事务内提交）。

    chat_model 可注入（测试用）；生产路径（trigger.run_archivist_scan）不传，走真实模型。
    batch_cap：恢复期只处理最老 N 条增量以定位毒消息（None=全量）。
    """
    graph = ArchivistGraph(
        session,
        service_case=service_case,
        progress=progress,
        chat_model=chat_model,
        batch_cap=batch_cap,
    )
    return await graph.run()
