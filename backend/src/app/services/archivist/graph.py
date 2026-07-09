"""档案官运行图：load_context → extract → classify → guardrail → commit。

设计 §5：顺序焊死的多节点流水线，LLM 只用于判断(extract)，落库用确定性代码(commit)。
P0 只接打卡投影一件事；classify/guardrail 为后续阶段(成效/画像/知识沉淀)预留的轻量节点。
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
from app.models.message import Message, MessageSenderType
from app.models.service_case import ServiceCase
from app.services.archivist.checkin_projection import CheckinUpsertResult, upsert_checkin
from app.services.archivist.extract_agent import run_extract_agent
from app.services.archivist.extractor import extract_checkin_fields
from app.services.archivist.shadow import reconcile_outcome

logger = logging.getLogger(__name__)

_SH_TZ = ZoneInfo("Asia/Shanghai")

# 首次为某服务单建进度行时，把水位线播到 N 天前：只回填最近 N 天，跳过远古历史，
# 避免存量单第一次被触发就把整段聊天史一次性喂抽取（海量 LLM 调用）。稳态下增量本就很小。
INITIAL_BACKFILL_DAYS = 3


class ArchivistState(TypedDict, total=False):
    watermark_from: int | None
    watermark_to: int | None
    # {date_iso: [{"id": int, "text": str}]}
    delta_by_date: dict[str, list[dict]]
    # 本轮增量涉及的全部用户消息 id（扁平，供影子对账按 message_id 取热路径已写事件）
    delta_message_ids: list[int]
    # {date_iso: {"fields": {...}, "evidence_ids": [int]}}
    day_candidates: dict[str, dict]
    # 影子模式：Agent 抽出的成效事件/画像信号（累计跨天，只对账不落库）
    shadow_outcome: list[dict]
    shadow_signals: list[dict]
    committed: list[dict]


@dataclass
class ArchivistRunResult:
    watermark_from: int | None = None
    watermark_to: int | None = None
    delta_count: int = 0
    days_processed: int = 0
    upserts: list[dict] = field(default_factory=list)  # [{date, filled, contradictions...}]
    contradictions: list[dict] = field(default_factory=list)
    # 影子对账（仅 archivist_outcome_shadow_enabled 时有值）
    shadow_recorded: bool = False
    shadow_events: list[dict] = field(default_factory=list)
    shadow_signals: list[dict] = field(default_factory=list)
    shadow_diff: dict = field(default_factory=dict)


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
    ) -> None:
        self.session = session
        self.service_case = service_case
        self.progress = progress
        # chat_model 可注入（测试用 FakeToolCallingModel）；None 时 extract_agent 内部建真实模型。
        self.chat_model = chat_model
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
        """读水位线，拉水位线之后的用户增量消息，按上海自然日分组。"""
        watermark = self.progress.last_archived_message_id or 0
        rows = (
            await self.session.execute(
                select(Message)
                .where(
                    Message.service_case_id == self.service_case.id,
                    Message.id > watermark,
                    Message.sender_type == MessageSenderType.USER,
                )
                .order_by(Message.id.asc())
            )
        ).scalars().all()

        delta_by_date: dict[str, list[dict]] = {}
        delta_message_ids: list[int] = []
        watermark_to = watermark
        for m in rows:
            watermark_to = max(watermark_to, m.id)
            if not m.created_at:
                continue
            aj = m.analysis_json if isinstance(m.analysis_json, dict) else {}
            # 图片解析失败的消息内容只是占位符（"用户发来一张图片（图片解析失败）。"），
            # 无信息量，跳过不喂抽取。成功解析的图片描述已写进 content，正常纳入。
            if aj.get("image_parse_status") == "failed":
                continue
            text = (m.content or "").strip()
            if not text:
                continue
            key = _sh_date(m.created_at).isoformat()
            delta_by_date.setdefault(key, []).append({"id": m.id, "text": text})
            delta_message_ids.append(m.id)

        self.result.watermark_from = watermark or None
        self.result.watermark_to = watermark_to or None
        self.result.delta_count = len(rows)
        return {
            "watermark_from": watermark or None,
            "watermark_to": watermark_to or None,
            "delta_by_date": delta_by_date,
            "delta_message_ids": delta_message_ids,
        }

    async def _extract(self, state: ArchivistState) -> dict[str, Any]:
        """对每个自然日的聊天原文抽取打卡字段（LLM 判断）。

        - 影子关（默认）：走 P0 的 extract_checkin_projection 纯 JSON 抽取，行为不变。
        - 影子开：走 Agent 形态（决策 A），额外累计成效/画像信号供 commit 对账（只抽不落库）。
        """
        delta_by_date = state.get("delta_by_date") or {}
        day_candidates: dict[str, dict] = {}
        shadow = self.settings.archivist_outcome_shadow_enabled
        shadow_outcome: list[dict] = []
        shadow_signals: list[dict] = []

        for date_iso, msgs in delta_by_date.items():
            content = "\n".join(f"- {m['text']}" for m in msgs)
            if shadow:
                collector = await run_extract_agent(
                    content=content,
                    service_case_id=self.service_case.id,
                    enabled_kinds=["checkin", "outcome", "profile"],
                    chat_model=self.chat_model,
                    settings=self.settings,
                )
                fields = collector.get("checkin") or {}
                shadow_outcome.extend(collector.get("outcome") or [])
                shadow_signals.extend(collector.get("profile") or [])
            else:
                fields = await extract_checkin_fields(
                    content=content, service_case_id=self.service_case.id
                )
            if fields:
                day_candidates[date_iso] = {
                    "fields": fields,
                    "evidence_ids": [m["id"] for m in msgs],
                }
        return {
            "day_candidates": day_candidates,
            "shadow_outcome": shadow_outcome,
            "shadow_signals": shadow_signals,
        }

    async def _classify(self, state: ArchivistState) -> dict[str, Any]:
        """P0 预留：全局知识 vs 个人处方分流、矛盾检测在后续阶段接入。当前透传。"""
        return {}

    async def _guardrail(self, state: ArchivistState) -> dict[str, Any]:
        """P0：打卡字段无受保护字段风险，仅剔除空候选（受保护字段/知识发布护栏后续阶段接入）。"""
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

        # 影子对账（P0.5）：只在开关开时做，且**不写** outcome_events/user_state，只落审计表 + 日志。
        if self.settings.archivist_outcome_shadow_enabled:
            shadow_outcome = state.get("shadow_outcome") or []
            shadow_signals = state.get("shadow_signals") or []
            row = await reconcile_outcome(
                self.session,
                service_case_id=self.service_case.id,
                delta_message_ids=state.get("delta_message_ids") or [],
                shadow_events=shadow_outcome,
                shadow_signals=shadow_signals,
                watermark_from=state.get("watermark_from"),
                watermark_to=state.get("watermark_to"),
            )
            self.result.shadow_events = shadow_outcome
            self.result.shadow_signals = shadow_signals
            if row is not None:
                self.result.shadow_recorded = True
                self.result.shadow_diff = row.diff or {}

        # 推进水位线（即使本轮没抽到打卡也要推进，避免下轮重复处理同一批增量）
        watermark_to = state.get("watermark_to")
        if watermark_to:
            self.progress.last_archived_message_id = watermark_to
        self.progress.last_archived_at = datetime.now(timezone.utc)
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
                Message.sender_type == MessageSenderType.USER,
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
) -> ArchivistRunResult:
    """跑一次档案官（不在此 commit，由调用方在持锁事务内提交）。

    chat_model 可注入（测试用）；生产路径（trigger.run_archivist_scan）不传，走真实模型。
    """
    graph = ArchivistGraph(
        session, service_case=service_case, progress=progress, chat_model=chat_model
    )
    return await graph.run()
