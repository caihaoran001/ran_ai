"""Isolated archivist duplicate-eval harness (REAL pipeline, no real sends).

Runs the REAL archivist pipeline (``run_archivist`` with ``chat_model=None`` in
``--real`` mode, deterministic FakeToolCallingModel otherwise) against the
isolated test database (``SessionLocal`` only, unique ``group_id`` per
group-run, cleanup afterwards). Real message sending is always disabled: the
harness aborts if a real QiWe channel is active, and the archivist pipeline
itself never sends messages.

Per batch it captures:
  - rendered model input content (the full HumanMessage string the code built,
    including the previous-5-history context -- never hand-stitched),
  - raw tool_calls emitted / kept / dropped (from ArchivistExtractTrace),
  - DB diffs before/after (checkins parsed_json, outcome_events,
    user_state_profiles, archivist_case_memory, knowledge_drafts),
  - ai_usage_logs tokens/cost for scenario ``archivist_extract``.

Evidence: JSONL + summary JSON under ``docs/reviews/artifacts/``.
Re-runnable: every run uses fresh uuid group_ids and timestamped outputs.

Usage (from backend/, zero-cost default -- FakeToolCallingModel, no LLM):
  uv run python scripts/run_archivist_dedup_eval.py --dry-run   # validate all 13 G-specs, no DB, no LLM
  uv run python scripts/run_archivist_dedup_eval.py --suite dedup12 --only G01,G06 --repeats 1
  uv run python scripts/run_archivist_dedup_eval.py --suite generic --groups 2 --repeats 3
  REAL_ARCHIVIST_EVAL=1 uv run python scripts/run_archivist_dedup_eval.py --real \\
      --only G01 --repeats 1   # real LLM; AI_* read from env at runtime
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR / "src"))

from sqlalchemy import delete, select  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.ai_usage import AIUsageLog  # noqa: E402
from app.models.archivist_case_memory import ArchivistCaseMemory  # noqa: E402
from app.models.archivist_extract_trace import ArchivistExtractTrace  # noqa: E402
from app.models.archivist_progress import ArchivistProgress  # noqa: E402
from app.models.checkin import Checkin  # noqa: E402
from app.models.checkin_followup_cycle import CheckinFollowupCycle  # noqa: E402
from app.models.knowledge_draft import KnowledgeDraft  # noqa: E402
from app.models.message import (  # noqa: E402
    Message,
    MessageDirection,
    MessagePurpose,
    MessageSenderType,
    MessageStatus,
)
from app.models.outcome import OutcomeEvent, OutcomeStageState  # noqa: E402
from app.models.service_case import ServiceCase, ServiceCaseStatus  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.user_state_profile import UserStateProfile  # noqa: E402
from app.services.archivist.graph import (  # noqa: E402
    BUSINESS_DAY_START_HOUR,
    get_or_create_progress,
    run_archivist,
)

SH = ZoneInfo("Asia/Shanghai")
ARTIFACTS_DIR = BACKEND_DIR.parents[0] / "docs" / "reviews" / "artifacts"
# When backend/ is checked out at repo root, parents[0] of backend == repo root.
if not ARTIFACTS_DIR.exists():
    ARTIFACTS_DIR = BACKEND_DIR / ".." / "docs" / "reviews" / "artifacts"
    ARTIFACTS_DIR = ARTIFACTS_DIR.resolve()

_ALLOWED_SENDERS = {"user", "staff", "bot", "system"}


# --------------------------------------------------------------------------
# Group spec (pure data, no DB, no LLM -- safe for --dry-run and unit tests)
# --------------------------------------------------------------------------

def _sh_utc(day_offset: int, hour: int, minute: int) -> datetime:
    """Business datetime: Shanghai today+offset at H:M, stored as UTC."""
    base = datetime.now(SH).replace(hour=0, minute=0, second=0, microsecond=0)
    local = base + timedelta(days=day_offset, hours=hour, minutes=minute)
    return local.astimezone(timezone.utc)


def _sh_abs(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    """Absolute business datetime: Shanghai y-m-d H:M, stored as UTC.

    Frozen spec dates (2026-09-01 etc., G06 2026-07-30 01:50) go through here;
    the 03:00 business-day rule is applied by the pipeline's own ``_sh_date``.
    """
    local = datetime(year, month, day, hour, minute, tzinfo=SH)
    return local.astimezone(timezone.utc)


def _msg_at(created_at: datetime) -> list[int]:
    local = created_at.astimezone(SH)
    return [local.year, local.month, local.day, local.hour, local.minute]


def _batch_target_date_iso(rows: list) -> str | None:
    """Business date (ISO) of the first USER/STAFF row, mirroring _sh_date."""
    for row in rows:
        if row.sender_type not in (MessageSenderType.USER, MessageSenderType.STAFF):
            continue
        created_at = row.created_at
        if created_at is None:
            continue
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        local = created_at.astimezone(SH)
        if local.hour < BUSINESS_DAY_START_HOUR:
            return (local - timedelta(days=1)).date().isoformat()
        return local.date().isoformat()
    return None


def build_group_spec(group_index: int, repeat_index: int) -> dict:
    """One duplicate-probe group: 3 sequential batches over 2 business days.

    batch1 (day -1): user checkin + outcome signal.
    batch2 (day 0): near-duplicate user checkin (dedup probe) + staff answer (faq probe).
    batch3 (day 0, later): system reminder (context-only) + short user ack.
    """
    gid = f"dedup-eval-{uuid.uuid4().hex[:10]}"
    return {
        "group_id": gid,
        "group_index": group_index,
        "repeat_index": repeat_index,
        "case_no": f"EVAL-DEDUP-{uuid.uuid4().hex[:6].upper()}",
        "batches": [
            {
                "day_offset": -1,
                "messages": [
                    {"sender": "user", "content": "今天早C喝了，体重168斤", "hour": 12, "minute": 5},
                    {"sender": "user", "content": "这周瘦了2斤，睡眠也变好了", "hour": 12, "minute": 6},
                ],
            },
            {
                "day_offset": 0,
                "messages": [
                    {"sender": "user", "content": "今天早C喝了，体重168斤", "hour": 8, "minute": 10},
                    {"sender": "staff", "content": "土豆可以当主食，今天不额外吃米饭就行。", "hour": 8, "minute": 40},
                ],
            },
            {
                "day_offset": 0,
                "messages": [
                    {"sender": "system", "content": "今日打卡提醒：记得提交今日行动。", "hour": 20, "minute": 0},
                    {"sender": "user", "content": "收到，今天晚餐按计划吃", "hour": 20, "minute": 15},
                ],
            },
        ],
    }


# --------------------------------------------------------------------------
# Frozen dedup12 suite (G01-G13) from backend/tests/archivist_dedup_cases_spec.md.
# Exact Chinese messages + absolute Shanghai datetimes; expectations frozen.
# Each message uses "at": [y, mo, d, H, M] (Asia/Shanghai) -> UTC created_at.
# --------------------------------------------------------------------------

def _m(sender: str, content: str, at: list[int]) -> dict:
    return {"sender": sender, "content": content, "at": at}


DEDUP12_GROUPS: dict[str, dict] = {
    "G01": {
        "batches": [
            {"label": "B1 2026-09-01", "messages": [
                _m("user", "今天早C喝了，午B也喝了，晚A还没喝；喝水大概2升；睡了6小时", [2026, 9, 1, 20, 5]),
                _m("user", "最近这两天都睡不好，半夜总醒，昨天称体重168斤", [2026, 9, 1, 20, 6]),
            ]},
            {"label": "B2 2026-09-02", "messages": [
                _m("user", "谢谢老师，收到啦，我会继续坚持的", [2026, 9, 2, 9, 10]),
            ]},
        ],
    },
    "G02": {
        "batches": [
            {"label": "B1 2026-09-03", "messages": [
                _m("user", "早上称体重172斤，心态有点崩", [2026, 9, 3, 8, 10]),
                _m("staff", "别慌，单次波动很正常，先按计划执行", [2026, 9, 3, 8, 12]),
            ]},
            {"label": "B2 2026-09-03", "messages": [
                _m("user", "刚发现早上秤没放平，实际是168斤，虚惊一场", [2026, 9, 3, 21, 40]),
            ]},
        ],
    },
    "G03": {
        "batches": [
            {"label": "B1 cross-day 2026-09-04/05", "messages": [
                _m("user", "今天三餐都按计划吃的，运动快走30分钟，睡了7小时", [2026, 9, 4, 22, 30]),
                _m("user", "昨天忘报体重了，昨天早上170斤；今天早C已喝", [2026, 9, 5, 8, 20]),
                _m("user", "今天喝水目前1升", [2026, 9, 5, 8, 25]),
            ]},
        ],
    },
    "G04": {
        "batches": [
            {"label": "B1 2026-09-06", "messages": [
                _m("user", "早上空腹168斤，昨晚睡得不错，7小时", [2026, 9, 6, 8, 5]),
            ]},
            {"label": "B2 2026-09-07", "messages": [
                _m("user", "早上空腹还是168斤，睡眠也还行", [2026, 9, 7, 8, 3]),
            ]},
        ],
    },
    "G05": {
        "batches": [
            {"label": "B1 2026-09-08", "messages": [
                _m("bot", "今天睡了几个小时呀？直接回数字就行", [2026, 9, 8, 21, 0]),
                _m("user", "6", [2026, 9, 8, 21, 30]),
                _m("staff", "6小时也不错，早点休息", [2026, 9, 8, 21, 32]),
            ]},
            {"label": "B2 2026-09-09", "messages": [
                _m("bot", "今天喝水几升呀？", [2026, 9, 9, 21, 0]),
                _m("user", "6", [2026, 9, 9, 21, 15]),
            ]},
        ],
    },
    "G06": {
        "batches": [
            {"label": "B1 business 2026-07-29", "messages": [
                _m("user", "补一下，7月29日打卡5项：早C午B晚A都喝了，运动30分钟，睡了6小时", [2026, 7, 30, 1, 50]),
                _m("user", "体重168斤也是昨天的", [2026, 7, 30, 1, 52]),
            ]},
            {"label": "B2 2026-07-30", "messages": [
                _m("user", "今天早C喝了，谢谢老师提醒", [2026, 7, 30, 20, 0]),
            ]},
        ],
    },
    "G07": {
        "preconditions": {"form_checkin": {"date": "2026-09-10", "weight": "100"}},
        "batches": [
            {"label": "B1 2026-09-10", "messages": [
                _m("user", "刚称了168斤，今天晚A还没喝", [2026, 9, 10, 19, 0]),
                _m("staff", "以秤为准，明天早上再复称一次看看", [2026, 9, 10, 19, 5]),
            ]},
            {"label": "B2 2026-09-11", "messages": [
                _m("user", "早上复称了，确实是168斤", [2026, 9, 11, 8, 0]),
            ]},
        ],
    },
    "G08": {
        "batches": [
            {"label": "B1 2026-09-12", "messages": [
                _m("user", "我平时喝水很少，一天就一杯，喜欢重口味，晚上要带娃所以只能早起运动", [2026, 9, 12, 12, 0]),
                _m("staff", "那咱们把喝水小目标先定起来", [2026, 9, 12, 12, 5]),
            ]},
            {"label": "B2 2026-09-13", "messages": [
                _m("user", "谢谢老师，喝水今天多喝了一杯，有进步吧", [2026, 9, 13, 9, 0]),
            ]},
        ],
    },
    "G09": {
        "batches": [
            {"label": "B1 2026-09-14", "messages": [
                _m("user", "这两天胃一直不舒服，吃完就胀，晚A都没敢喝", [2026, 9, 14, 22, 0]),
            ]},
            {"label": "B2 2026-09-16", "messages": [
                _m("user", "胃不胀了，昨天晚A正常喝了，谢谢老师", [2026, 9, 16, 9, 0]),
            ]},
            {"label": "B3 2026-09-17", "messages": [
                _m("user", "一切正常，继续坚持", [2026, 9, 17, 9, 0]),
            ]},
        ],
    },
    "G10": {
        "batches": [
            {"label": "B1 2026-09-18", "messages": [
                _m("user", "晚A能用冰水冲吗？", [2026, 9, 18, 10, 0]),
                _m("bot", "可以的，怎么方便怎么来", [2026, 9, 18, 10, 1]),
                _m("staff", "纠正一下，晚A要用约300ml温水冲泡，不要用冰水，不适先停", [2026, 9, 18, 10, 5]),
            ]},
            {"label": "B2 2026-09-19", "messages": [
                _m("user", "昨天按温水冲的，谢谢老师纠正", [2026, 9, 19, 10, 0]),
            ]},
        ],
    },
    "G11": {
        "batches": [
            {"label": "B1 2026-09-20", "messages": [
                _m("user", "午B可以随餐一起喝吗？还是必须饭前？", [2026, 9, 20, 15, 0]),
                _m("bot", "随餐喝就行", [2026, 9, 20, 15, 1]),
                _m("staff", "午B随餐喝就可以，用温水送服，吃完半小时内喝完就行", [2026, 9, 20, 15, 5]),
            ]},
            {"label": "B2 2026-09-21", "messages": [
                _m("user", "我明天要出差，只能早上在家，中午在高铁上，这种情况午B怎么安排？", [2026, 9, 21, 15, 0]),
                _m("staff", "你这种情况出差就早上把早C喝好，午B带上车用保温杯温水喝，不用勉强饭点", [2026, 9, 21, 15, 6]),
            ]},
            {"label": "B3 2026-09-22", "messages": [
                _m("user", "收到，谢谢老师", [2026, 9, 22, 9, 0]),
            ]},
        ],
    },
    "G12": {
        "batches": [
            {"label": "B1 2026-09-23", "messages": [
                _m("user", "坚持两周了，瘦了3斤，裤腰松了一格，睡眠也比以前稳了，谢谢老师们", [2026, 9, 23, 8, 0]),
                _m("staff", "太棒了，继续保持这个节奏", [2026, 9, 23, 8, 5]),
            ]},
            {"label": "B2 2026-09-24", "messages": [
                _m("user", "谢谢老师，我会继续坚持的", [2026, 9, 24, 8, 0]),
            ]},
            {"label": "B3 2026-09-28", "messages": [
                _m("user", "又坚持了一周，一共瘦了5斤，昨天去跑了3公里，精神特别好", [2026, 9, 28, 8, 0]),
            ]},
        ],
    },
    "G13": {
        "batches": [
            {"label": "B1 2026-09-30", "messages": [
                _m("user", "太困了先睡了，晚安", [2026, 9, 30, 23, 40]),
            ]},
            {"label": "B2 2026-10-01", "messages": [
                _m("user", "昨晚11点睡的，睡得还行", [2026, 10, 1, 7, 30]),
            ]},
            {"label": "B3 2026-10-02", "messages": [
                _m("user", "昨晚23点睡的", [2026, 10, 2, 7, 30]),
            ]},
        ],
    },
}

DEDUP12_CODES = ["G%02d" % i for i in range(1, 14)]


def build_dedup12_spec(group_code: str, repeat_index: int) -> dict:
    """Frozen G01-G12 spec: exact messages, absolute Shanghai datetimes, preconditions."""
    if group_code not in DEDUP12_GROUPS:
        raise ValueError(f"unknown dedup12 group {group_code!r}, want one of {DEDUP12_CODES}")
    frozen = DEDUP12_GROUPS[group_code]
    gid = f"dedup12-{group_code.lower()}-{uuid.uuid4().hex[:8]}"
    batches = [
        {
            "label": b.get("label", f"B{i + 1}"),
            "messages": [dict(m) for m in b["messages"]],
        }
        for i, b in enumerate(frozen["batches"])
    ]
    spec: dict = {
        "group_code": group_code,
        "group_id": gid,
        "group_index": DEDUP12_CODES.index(group_code),
        "repeat_index": repeat_index,
        "case_no": f"EVAL-{group_code}-{uuid.uuid4().hex[:6].upper()}",
        "batches": batches,
    }
    if frozen.get("preconditions"):
        spec["preconditions"] = dict(frozen["preconditions"])
    return spec


def validate_group_spec(spec: dict) -> dict:
    """Pure validation of a group spec (no DB, no LLM). Raises ValueError."""
    batches = spec.get("batches") or []
    if not batches:
        raise ValueError("spec has no batches")
    total = 0
    prev_key = None
    for bi, batch in enumerate(batches):
        msgs = batch.get("messages") or []
        if not msgs:
            raise ValueError(f"batch {bi} has no messages")
        batch_times: list[tuple] = []
        for mi, msg in enumerate(msgs):
            sender = msg.get("sender")
            if sender not in _ALLOWED_SENDERS:
                raise ValueError(f"batch {bi} msg {mi}: bad sender {sender!r}")
            if not str(msg.get("content") or "").strip():
                raise ValueError(f"batch {bi} msg {mi}: empty content")
            if "at" in msg:
                at = msg["at"]
                if len(at) != 5 or not all(isinstance(v, int) for v in at):
                    raise ValueError(f"batch {bi} msg {mi}: bad absolute time {at!r}")
                batch_times.append(tuple(at))
            total += 1
        if batch_times:
            key = (min(batch_times), bi)
        else:
            key = ((0, 0, 0, 0, 0), int(batch.get("day_offset", 0)), bi)
        if prev_key is not None and key < prev_key:
            raise ValueError(f"batch {bi}: batches out of chronological order")
        prev_key = key
    preconditions = spec.get("preconditions") or {}
    form = preconditions.get("form_checkin")
    if form is not None and (not form.get("date") or not str(form.get("weight") or "").strip()):
        raise ValueError("form_checkin precondition needs date + weight")
    return {
        "group_code": spec.get("group_code"),
        "group_id": spec.get("group_id"),
        "batches": len(batches),
        "messages": total,
        "preconditions": sorted(preconditions.keys()),
        "business_day_start_hour": BUSINESS_DAY_START_HOUR,
    }


# --------------------------------------------------------------------------
# Fake model (default path -- deterministic, zero-cost, no LLM)
# --------------------------------------------------------------------------

class RecordingFakeModel:
    """Deterministic FakeToolCallingModel that records the rendered input."""

    def __init__(self, responses: list):
        from langchain_core.messages import AIMessage as _AIMessage

        self._AIMessage = _AIMessage
        self.responses = list(responses)
        self.calls: list[list] = []
        self.bound_tools: list = []

    def bind_tools(self, tools):
        self.bound_tools = list(tools)
        return self

    async def ainvoke(self, messages):
        self.calls.append(list(messages))
        if not self.responses:
            return self._AIMessage(content="")
        return self.responses.pop(0)


def _fake_responses_for_batch(batch_index: int, target_date_iso: str | None = None):
    """Scripted emits: batch0 full report, batch1 duplicate outcome, batch2 empty."""
    from langchain_core.messages import AIMessage

    target = target_date_iso or _sh_utc(-1, 12, 5).astimezone(SH).date().isoformat()
    if batch_index == 0:
        return [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "report_checkin",
                        "args": {
                            "target_date": target,
                            "weight": "168斤",
                            "product_schedule": {"morning_c": "已喝"},
                        },
                        "id": "eval-c1",
                    },
                    {
                        "name": "report_outcome",
                        "args": {
                            "field_key": "weight_change",
                            "event_type": "improved",
                            "value": "瘦了2斤",
                            "evidence_text": "这周瘦了2斤",
                            "confidence": 0.9,
                        },
                        "id": "eval-o1",
                    },
                ],
            )
        ]
    if batch_index == 1:
        return [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "report_outcome",
                        "args": {
                            "field_key": "weight_change",
                            "event_type": "improved",
                            "value": "瘦了2斤",
                            "evidence_text": "这周瘦了2斤",
                            "confidence": 0.9,
                        },
                        "id": "eval-o1-dup",
                    },
                ],
            )
        ]
    return [AIMessage(content="", tool_calls=[])]


# --------------------------------------------------------------------------
# Snapshots / diffs
# --------------------------------------------------------------------------

def _jsonable(value):
    try:
        json.dumps(value, ensure_ascii=False, default=str)
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except Exception:
        return str(value)


def _draft_scope(draft) -> str:
    """KnowledgeDraft 的 general-vs-personal 口径（content_json.scope 优先， draft_type 兜底）。"""
    try:
        content = draft.content_json or {}
    except Exception:
        content = {}
    if isinstance(content, dict):
        scope = str(content.get("scope") or "").strip().lower()
        if scope in ("general", "personal"):
            return scope
    draft_type = str(getattr(draft, "draft_type", "") or "").strip().lower()
    if draft_type == "qa":
        return "general"  # 通用 FAQ 才建稿；personal 走用户档案不建稿
    return draft_type or "unknown"


def _flatten_checkin_parsed(parsed) -> dict:
    """打卡 parsed_json 展平：product_schedule 子键与 custom_checkin 答案打散成点分字段。"""
    flat: dict = {}
    if not isinstance(parsed, dict):
        return flat
    for key, val in parsed.items():
        if key == "product_schedule" and isinstance(val, dict):
            for sub, subv in val.items():
                flat[f"product_schedule.{sub}"] = _jsonable(subv)
        elif key == "custom_checkin" and isinstance(val, dict):
            for item_id, ans in val.items():
                if isinstance(ans, dict):
                    flat[f"custom_checkin.{item_id}"] = _jsonable(ans.get("answer", ans))
                else:
                    flat[f"custom_checkin.{item_id}"] = _jsonable(ans)
        elif key == "_meta":
            continue  # 来源/矛盾另行记录，不污染业务字段 diff
        else:
            flat[key] = _jsonable(val)
    return flat


def _meta_contradictions(parsed) -> list:
    try:
        meta = (parsed or {}).get("_meta") or {}
        items = meta.get("contradictions") or []
        return list(items) if isinstance(items, list) else [items]
    except Exception:
        return []


async def snapshot_case(session, case_id: int) -> dict:
    checkins = list(
        (
            await session.execute(
                select(Checkin)
                .where(Checkin.service_case_id == case_id, Checkin.deleted_at.is_(None))
                .order_by(Checkin.checkin_date.asc())
            )
        )
        .scalars()
        .all()
    )
    events = list(
        (
            await session.execute(
                select(OutcomeEvent)
                .where(OutcomeEvent.service_case_id == case_id)
                .order_by(OutcomeEvent.id.asc())
            )
        )
        .scalars()
        .all()
    )
    profile = await session.scalar(
        select(UserStateProfile).where(UserStateProfile.service_case_id == case_id)
    )
    memory = await session.scalar(
        select(ArchivistCaseMemory).where(ArchivistCaseMemory.service_case_id == case_id)
    )
    drafts = [
        d
        for d in (
            await session.execute(select(KnowledgeDraft).order_by(KnowledgeDraft.id.desc()).limit(100))
        )
        .scalars()
        .all()
        if ((d.content_json or {}).get("service_case_id") == case_id)
    ]
    traces = list(
        (
            await session.execute(
                select(ArchivistExtractTrace)
                .where(ArchivistExtractTrace.service_case_id == case_id)
                .order_by(ArchivistExtractTrace.id.asc())
            )
        )
        .scalars()
        .all()
    )
    usage = list(
        (
            await session.execute(
                select(AIUsageLog)
                .where(
                    AIUsageLog.service_case_id == case_id,
                    AIUsageLog.scenario == "archivist_extract",
                )
                .order_by(AIUsageLog.id.asc())
            )
        )
        .scalars()
        .all()
    )
    return {
        "checkins": [
            {"id": c.id, "date": c.checkin_date.isoformat(), "parsed_json": _jsonable(c.parsed_json)}
            for c in checkins
        ],
        "outcome_events": [
            {
                "id": e.id,
                "field_key": e.field_key,
                "event_type": e.event_type,
                "value_text": e.value_text,
                "milestone": e.milestone,
                "message_id": e.message_id,
            }
            for e in events
        ],
        "user_state_profile": _jsonable(profile.state_json) if profile else None,
        "archivist_case_memory": (
            {"summary": memory.summary, "case_material_summary": memory.case_material_summary}
            if memory
            else None
        ),
        "knowledge_drafts": [
            {"id": d.id, "draft_type": d.draft_type, "title": d.title,
             "question": d.question, "scope": _draft_scope(d)} for d in drafts
        ],
        "traces": [
            {
                "id": t.id,
                "target_date": t.target_date.isoformat() if t.target_date else None,
                "watermark_to": t.watermark_to,
                "emitted": _jsonable(t.emitted_json),
                "kept": _jsonable(t.kept_json),
                "dropped": _jsonable(t.dropped_json),
            }
            for t in traces
        ],
        "usage": [
            {
                "id": u.id,
                "model": u.model,
                "prompt_tokens": u.prompt_tokens,
                "completion_tokens": u.completion_tokens,
                "total_tokens": u.total_tokens,
                "estimated_cost_yuan": u.estimated_cost_yuan,
                "elapsed_ms": u.elapsed_ms,
                "success": u.success,
            }
            for u in usage
        ],
    }


def diff_snapshots(before: dict, after: dict) -> dict:
    out: dict = {}
    for key in ("checkins", "outcome_events", "knowledge_drafts", "traces", "usage"):
        before_ids = {r["id"] for r in before[key]}
        added = [r for r in after[key] if r["id"] not in before_ids]
        out[key] = {
            "before": len(before[key]),
            "after": len(after[key]),
            "added": added,
        }
    # 同 (service_case, date) 两边都存在的打卡行：逐字段 before/after（展平 parsed_json）。
    before_by_date = {r["date"]: r for r in before["checkins"]}
    after_by_date = {r["date"]: r for r in after["checkins"]}
    updated: list[dict] = []
    for date_iso in sorted(set(before_by_date) & set(after_by_date)):
        b_parsed = before_by_date[date_iso].get("parsed_json") or {}
        a_parsed = after_by_date[date_iso].get("parsed_json") or {}
        b_flat, a_flat = _flatten_checkin_parsed(b_parsed), _flatten_checkin_parsed(a_parsed)
        changes = {
            field: {"before": b_flat.get(field), "after": a_flat.get(field)}
            for field in sorted(set(b_flat) | set(a_flat))
            if _jsonable(b_flat.get(field)) != _jsonable(a_flat.get(field))
        }
        b_contra, a_contra = _meta_contradictions(b_parsed), _meta_contradictions(a_parsed)
        meta_changed = _jsonable(b_contra) != _jsonable(a_contra)
        if changes or meta_changed:
            updated.append({
                "date": date_iso,
                "id_before": before_by_date[date_iso]["id"],
                "id_after": after_by_date[date_iso]["id"],
                "changes": changes,
                "meta_changed": meta_changed,
                "contradictions_before": _jsonable(b_contra),
                "contradictions_after": _jsonable(a_contra),
                "before": b_flat,
                "after": a_flat,
            })
    out["checkins"]["updated"] = updated
    out["user_state_profile"] = {
        "changed": before["user_state_profile"] != after["user_state_profile"],
        "before": before["user_state_profile"],
        "after": after["user_state_profile"],
    }
    mem_before, mem_after = before["archivist_case_memory"], after["archivist_case_memory"]
    out["archivist_case_memory"] = {
        "changed": mem_before != mem_after,
        "before": mem_before,
        "after": mem_after,
        "summary_before_len": len(str((mem_before or {}).get("summary") or "")),
        "summary_after_len": len(str((mem_after or {}).get("summary") or "")),
        "case_material_before_len": len(str((mem_before or {}).get("case_material_summary") or "")),
        "case_material_after_len": len(str((mem_after or {}).get("case_material_summary") or "")),
    }
    return out


def _faq_scopes_from_emitted(emitted) -> list[str]:
    """每次 report_faq_candidate emit 的 scope 实参（缺省 general）。"""
    scopes: list[str] = []
    for call in emitted or []:
        if (call.get("name") or "") != "report_faq_candidate":
            continue
        args = call.get("args") or {}
        scopes.append(str(args.get("scope") or "general").strip().lower() or "general")
    return scopes


def _draft_scopes_from_added(diff: dict) -> list[str]:
    """本批新建 KnowledgeDraft 的 general-vs-personal 口径。"""
    return [
        str(r.get("scope") or "unknown")
        for r in (diff.get("knowledge_drafts") or {}).get("added", [])
    ]


# --------------------------------------------------------------------------
# Guards
# --------------------------------------------------------------------------

def assert_no_real_channel() -> None:
    """Abort if a real QiWe channel is currently registered (never send)."""
    try:
        from app.services.channels import registry as _registry

        getter = getattr(_registry, "get_current_channel", None)
        channel = getter() if callable(getter) else None
    except Exception:
        channel = None
    if channel is not None and "qiwe" in type(channel).__module__.lower():
        raise RuntimeError("refusing to run: a real QiWe channel is active")


async def _ensure_helper_tables(session) -> None:
    connection = await session.connection()
    await connection.run_sync(
        lambda sc: CheckinFollowupCycle.__table__.create(sc, checkfirst=True)
    )
    await connection.run_sync(
        lambda sc: ArchivistExtractTrace.__table__.create(sc, checkfirst=True)
    )


# --------------------------------------------------------------------------
# One group-run: setup -> sequential batches (watermark advances via code)
# --------------------------------------------------------------------------

async def _insert_batch_messages(session, case: ServiceCase, batch: dict) -> list[Message]:
    rows: list[Message] = []
    for msg in batch["messages"]:
        sender = msg["sender"]
        if "at" in msg:
            created_at = _sh_abs(*msg["at"])
        else:
            doff = msg.get("day_offset_override", batch.get("day_offset", 0))
            created_at = _sh_utc(int(doff), int(msg.get("hour", 12)), int(msg.get("minute", 0)))
        if sender == "system":
            row = Message(
                service_case_id=case.id,
                group_id=case.group_id,
                direction=MessageDirection.OUTBOUND,
                sender_type=MessageSenderType.SYSTEM,
                message_type="text",
                content=msg["content"],
                purpose=MessagePurpose.REMINDER,
                status=MessageStatus.SENT,
                created_at=created_at,
            )
        elif sender == "bot":
            # Assistant history: enters chat_context for understanding, never delta/allowed_dates.
            row = Message(
                service_case_id=case.id,
                group_id=case.group_id,
                direction=MessageDirection.OUTBOUND,
                sender_type=MessageSenderType.BOT,
                message_type="text",
                content=msg["content"],
                purpose=MessagePurpose.CHAT,
                status=MessageStatus.SENT,
                created_at=created_at,
            )
        else:
            st = MessageSenderType.USER if sender == "user" else MessageSenderType.STAFF
            row = Message(
                service_case_id=case.id,
                group_id=case.group_id,
                direction=MessageDirection.INBOUND,
                sender_type=st,
                message_type="text",
                content=msg["content"],
                purpose=MessagePurpose.CHAT,
                status=MessageStatus.RECEIVED,
                created_at=created_at,
            )
        session.add(row)
        rows.append(row)
    await session.flush()
    return rows


async def _patch_real_model_recorder(recorder: dict):
    """Wrap create_agent_chat_model so the REAL model input/output is captured."""
    import app.services.agent.model as model_mod

    orig_factory = model_mod.create_agent_chat_model

    def recording_factory(settings):
        inner = orig_factory(settings)

        class _Proxy:
            def bind_tools(self, tools):
                bound = inner.bind_tools(tools)
                recorder["bound_tool_names"] = [t.name for t in tools]

                class _Bound:
                    async def ainvoke(self, messages):
                        import time as _time

                        recorder["model_input"] = (
                            messages[1].content if len(messages) > 1 else messages[0].content
                        )
                        _started = _time.monotonic()
                        resp = await bound.ainvoke(messages)
                        recorder["raw_tool_calls"] = [
                            {"name": c.get("name"), "args": c.get("args") or {}}
                            for c in (getattr(resp, "tool_calls", None) or [])
                        ]
                        # agent_run_id=None 时 record_model_usage 直接返回，
                        # DB 里没有 usage 行；在此直接从响应采集用量，保证费用可追溯。
                        try:
                            _meta = getattr(resp, "usage_metadata", None) or {}
                            _rm = getattr(resp, "response_metadata", None) or {}
                            _tok = (_rm.get("token_usage") or {}) if isinstance(_rm, dict) else {}
                            _in = _meta.get("input_tokens", _tok.get("prompt_tokens", 0)) if isinstance(_meta, dict) else 0
                            _out = _meta.get("output_tokens", _tok.get("completion_tokens", 0)) if isinstance(_meta, dict) else 0
                            _total = _meta.get("total_tokens", _tok.get("total_tokens", (_in or 0) + (_out or 0))) if isinstance(_meta, dict) else ((_in or 0) + (_out or 0))
                            _resp_model = ""
                            if isinstance(_rm, dict):
                                _resp_model = str(_rm.get("model_name") or _rm.get("model") or "")
                            recorder["usage_metadata"] = {
                                "model": _resp_model,
                                "prompt_tokens": int(_in or 0),
                                "completion_tokens": int(_out or 0),
                                "total_tokens": int(_total or 0),
                                "elapsed_ms": int((_time.monotonic() - _started) * 1000),
                            }
                        except Exception:
                            pass
                        return resp

                return _Bound()

        return _Proxy()

    model_mod.create_agent_chat_model = recording_factory
    return orig_factory


async def _apply_form_precondition(session, case: ServiceCase, spec: dict) -> None:
    """G07: pre-insert a form-source Checkin row (form authority fixture, not chat)."""
    form = (spec.get("preconditions") or {}).get("form_checkin")
    if not form:
        return
    checkin_date = datetime.fromisoformat(str(form["date"])).date()
    session.add(
        Checkin(
            service_case_id=case.id,
            group_id=case.group_id,
            user_id=case.user_id,
            checkin_date=checkin_date,
            content="（表单打卡前置）",
            parsed_json={
                "weight": str(form["weight"]),
                "_meta": {"sources": {"weight": "form"}, "contradictions": []},
            },
        )
    )
    await session.flush()


async def run_group(spec: dict, *, real: bool, ai_settings: dict) -> dict:
    """Run all batches of one group sequentially; watermark advances via run_archivist."""
    group_result = {
        "group_code": spec.get("group_code"),
        "group_id": spec["group_id"],
        "group_index": spec["group_index"],
        "repeat_index": spec["repeat_index"],
        "ai_settings": ai_settings,
        "batches": [],
    }
    async with SessionLocal() as session:
        case_ids: list[int] = []
        user_ids: list[int] = []
        try:
            await _ensure_helper_tables(session)
            user = User(name="档案官去重评估用户")
            session.add(user)
            await session.flush()
            case = ServiceCase(
                case_no=spec["case_no"],
                user_id=user.id,
                group_id=spec["group_id"],
                questionnaire_token=uuid.uuid4().hex,
                status=ServiceCaseStatus.IN_SERVICE,
                started_at=datetime.now(timezone.utc) - timedelta(days=6),
            )
            session.add(case)
            await session.flush()
            progress = await get_or_create_progress(session, service_case_id=case.id)
            # Reset watermark to 0 so the seeded backfill window never skips eval messages.
            progress.last_archived_message_id = 0
            await session.flush()
            await _apply_form_precondition(session, case, spec)
            await session.commit()
            case_ids.append(case.id)
            user_ids.append(user.id)

            import app.services.agent.model as model_mod

            orig_factory = None
            recorder: dict = {}
            if real:
                orig_factory = await _patch_real_model_recorder(recorder)

            try:
                for bi, batch in enumerate(spec["batches"]):
                    before = await snapshot_case(session, case.id)
                    wm_before = progress.last_archived_message_id
                    rows = await _insert_batch_messages(session, case, batch)

                    fake = None
                    chat_model = None
                    if not real:
                        target_iso = _batch_target_date_iso(rows)
                        fake = RecordingFakeModel(_fake_responses_for_batch(bi, target_iso))
                        chat_model = fake
                    else:
                        recorder.clear()

                    result = await run_archivist(
                        session, service_case=case, progress=progress, chat_model=chat_model
                    )
                    await session.flush()
                    # run_archivist does not commit; commit per batch so the next
                    # batch's watermark advance is durable (mirrors trigger.py).
                    await session.commit()
                    after = await snapshot_case(session, case.id)

                    if not real:
                        model_input = (
                            fake.calls[0][1].content if fake and fake.calls else ""
                        )
                        emitted = (
                            after["traces"][-1]["emitted"]
                            if after["traces"] and len(after["traces"]) > len(before["traces"])
                            else []
                        )
                        batch_usage = after["usage"][-1:] if len(after["usage"]) > len(before["usage"]) else []
                    else:
                        model_input = recorder.get("model_input", "")
                        emitted = recorder.get("raw_tool_calls", [])
                        batch_usage = after["usage"][-1:] if len(after["usage"]) > len(before["usage"]) else []
                        if not batch_usage and isinstance(recorder.get("usage_metadata"), dict):
                            _um = recorder.get("usage_metadata") or {}
                            _pt = int(_um.get("prompt_tokens") or 0)
                            _ct = int(_um.get("completion_tokens") or 0)
                            _tt = int(_um.get("total_tokens") or (_pt + _ct))
                            batch_usage = [{
                                "model": _um.get("model") or ai_settings.get("ai_model"),
                                "prompt_tokens": _pt,
                                "completion_tokens": _ct,
                                "total_tokens": _tt,
                                "estimated_cost_yuan": round(_pt / 1e6 * 1.0 + _ct / 1e6 * 4.0, 8),
                                "elapsed_ms": _um.get("elapsed_ms"),
                                "success": True,
                                "source": "response_usage_metadata",
                            }]

                    new_traces = after["traces"][len(before["traces"]):]
                    batch_diff = diff_snapshots(before, after)
                    emitted_calls = emitted or recorder.get("raw_tool_calls", [])
                    group_result["batches"].append(
                        {
                            "batch_index": bi,
                            "batch_label": batch.get("label"),
                            "day_offset": batch.get("day_offset"),
                            "watermark_from": wm_before,
                            "watermark_to": progress.last_archived_message_id,
                            "model_input_content": model_input,
                            "model": (batch_usage[-1]["model"] if batch_usage else ai_settings.get("ai_model")),
                            "emitted_tool_calls": emitted_calls,
                            "kept": new_traces[-1]["kept"] if new_traces else {},
                            "dropped": new_traces[-1]["dropped"] if new_traces else [],
                            "faq_scopes_emitted": _faq_scopes_from_emitted(emitted_calls),
                            "faq_scopes_created": _draft_scopes_from_added(batch_diff),
                            "result": {
                                "delta_count": result.delta_count,
                                "days_processed": result.days_processed,
                                "outcome_written": result.outcome_written,
                                "profile_written": result.profile_written,
                                "state_written": result.state_written,
                                "faq_written": result.faq_written,
                                "memory_written": result.memory_written,
                                "emitted_count": result.emitted_count,
                                "dropped_count": result.dropped_count,
                            },
                            "db_diff": batch_diff,
                            "usage": batch_usage,
                        }
                    )
            finally:
                if orig_factory is not None:
                    model_mod.create_agent_chat_model = orig_factory
        finally:
            pass
        # Cleanup test rows (re-runnable, no leftovers) unless --keep.
        if not _KEEP and case_ids:
            await _cleanup_case(session, case_ids[0], user_ids[0] if user_ids else None)
            await session.commit()
    return group_result


async def _cleanup_case(session, case_id: int, user_id: int | None) -> None:
    draft_ids = [
        d.id
        for d in (
            await session.execute(select(KnowledgeDraft).order_by(KnowledgeDraft.id.desc()).limit(200))
        )
        .scalars()
        .all()
        if ((d.content_json or {}).get("service_case_id") == case_id)
    ]
    for table in (AIUsageLog, ArchivistExtractTrace, OutcomeEvent, OutcomeStageState,
                  UserStateProfile, ArchivistCaseMemory, Checkin, Message, ArchivistProgress):
        try:
            await session.execute(delete(table).where(table.service_case_id == case_id))
        except Exception:
            pass
    for did in draft_ids:
        try:
            await session.execute(delete(KnowledgeDraft).where(KnowledgeDraft.id == did))
        except Exception:
            pass
    try:
        await session.execute(delete(ServiceCase).where(ServiceCase.id == case_id))
    except Exception:
        pass
    if user_id is not None:
        try:
            await session.execute(delete(User).where(User.id == user_id))
        except Exception:
            pass


_KEEP = False


def _emitted_args(batch: dict, name: str) -> list[dict]:
    return [
        (c.get("args") or {}) for c in (batch.get("emitted_tool_calls") or [])
        if (c.get("name") or "") == name
    ]


def _outcome_added(batch: dict, field_key=None, event_type=None) -> list[dict]:
    rows = ((batch.get("db_diff") or {}).get("outcome_events") or {}).get("added", [])
    return [
        r for r in rows
        if (field_key is None or r.get("field_key") == field_key)
        and (event_type is None or r.get("event_type") == event_type)
    ]


def _checkin_added(batch: dict) -> dict:
    return {
        r.get("date"): (r.get("parsed_json") or {})
        for r in ((batch.get("db_diff") or {}).get("checkins") or {}).get("added", [])
    }


def _checkin_updated(batch: dict) -> dict:
    return {
        r.get("date"): r
        for r in ((batch.get("db_diff") or {}).get("checkins") or {}).get("updated", [])
    }


def _drafts_added(batch: dict) -> list[dict]:
    return ((batch.get("db_diff") or {}).get("knowledge_drafts") or {}).get("added", [])


def _mem_changed(batch: dict) -> bool:
    return bool(((batch.get("db_diff") or {}).get("archivist_case_memory") or {}).get("changed"))


def _profile_changed(batch: dict) -> bool:
    return bool(((batch.get("db_diff") or {}).get("user_state_profile") or {}).get("changed"))


def _wm_advanced(batch: dict) -> bool:
    try:
        return int(batch.get("watermark_to") or 0) > int(batch.get("watermark_from") or 0)
    except Exception:
        return False


def _kept_count(batch: dict, bucket: str) -> int:
    try:
        return int((batch.get("kept") or {}).get(bucket) or 0)
    except Exception:
        return 0


def _grade_g01(b):
    fails, flags = [], []
    b1, b2 = b[0], b[1]
    if "2026-09-01" not in _checkin_added(b1):
        fails.append("B1: checkins(2026-09-01) MUST_ADD 缺失")
    if not _outcome_added(b1, "sleep_change", "worsened"):
        fails.append("B1: outcome sleep_change/worsened MUST_ADD 缺失")
    if not [r for r in _outcome_added(b1, "weight_change") if "168" in str(r.get("value_text") or "")]:
        fails.append("B1: outcome weight_change 含168斤 MUST_ADD 缺失")
    if not _profile_changed(b1):
        fails.append("B1: state 画像 MUST_ADD 未落库")
    if _checkin_added(b2) or _checkin_updated(b2):
        fails.append("B2: checkins(2026-09-02) MUST_NOT_DUPLICATE 被写")
    if _outcome_added(b2):
        fails.append("B2: outcome MUST_NOT_DUPLICATE 被新增")
    if not _mem_changed(b1) and _mem_changed(b2):
        fails.append("B2: 无新事实 memory MUST_NOT_DUPLICATE 被重写")
    if len([a for a in _emitted_args(b2, "report_state_fact")
            if str(a.get("state_type") or "") == "user_commitment"]) > 1:
        fails.append("B2: user_commitment ALLOWED 0-or-1 超限")
    if not _wm_advanced(b2):
        fails.append("B2: 水位线 MUST_UPDATE 未推进")
    return fails, flags


def _grade_g02(b):
    fails, flags = [], []
    b1, b2 = b[0], b[1]
    if "2026-09-03" not in _checkin_added(b1):
        fails.append("B1: checkins(2026-09-03) weight=172斤 MUST_ADD 缺失")
    if _outcome_added(b1, "weight_change"):
        fails.append("B1: 首个无基线裸体重 MUST_NOT_DUPLICATE 报了 outcome")
    if "2026-09-03" in _checkin_added(b2):
        fails.append("B2: 同日行 MUST_UPDATE 却新增了第二行")
    upd = _checkin_updated(b2).get("2026-09-03")
    if upd is None:
        fails.append("B2: checkins(2026-09-03) MUST_UPDATE 缺失")
    elif "168" not in str((upd.get("after") or {}).get("weight") or ""):
        fails.append("B2: 当前值 MUST_NOT_MISS 仍不是168斤")
    if _outcome_added(b2, "weight_change"):
        fails.append("B2: 读数纠偏 172->168 不是身体变化 MUST_NOT_DUPLICATE")
    if not _profile_changed(b2):
        fails.append("B2: 心态 state MUST_UPDATE resolved 未落库")
    return fails, flags


def _grade_g03(b):
    fails, flags = [], []
    b1 = b[0]
    added = _checkin_added(b1)
    if set(added) != {"2026-09-04", "2026-09-05"}:
        fails.append(f"B1: 必须恰好两行 09-04/09-05，实际 {sorted(added)}")
    if "170" in str(_flatten_checkin_parsed(added.get("2026-09-05", {})).get("weight") or ""):
        fails.append("B1: 昨天体重170斤不得记到今天行")
    return fails, flags


def _grade_g04(b):
    fails, flags = [], []
    b1, b2 = b[0], b[1]
    if "2026-09-06" not in _checkin_added(b1):
        fails.append("B1: checkins(2026-09-06) MUST_ADD 缺失")
    if _outcome_added(b1, "weight_change"):
        fails.append("B1: 首个无基线裸体重 MUST_NOT_DUPLICATE 报了 outcome")
    if "2026-09-07" not in _checkin_added(b2):
        fails.append("B2: checkins(2026-09-07) 跨天新行 MUST_ADD 缺失")
    wo = _outcome_added(b2, "weight_change")
    if len(wo) > 1 or any(r.get("event_type") != "unchanged" for r in wo):
        fails.append("B2: 次日同值只允许 0-or-1 行 unchanged")
    return fails, flags


def _grade_g05(b):
    fails, flags = [], []
    b1, b2 = b[0], b[1]
    f1 = _flatten_checkin_parsed(_checkin_added(b1).get("2026-09-08", {}))
    if "6" not in str(f1.get("sleep") or ""):
        fails.append("B1: checkins(2026-09-08) sleep 含6 MUST_ADD 缺失")
    f2 = _flatten_checkin_parsed(_checkin_added(b2).get("2026-09-09", {}))
    if not str(f2.get("water") or ""):
        fails.append("B2: checkins(2026-09-09) water MUST_ADD 缺失")
    if str(f2.get("sleep") or ""):
        fails.append("B2: 不得把 B1 sleep 语义复制过来")
    if "2026-09-08" in _checkin_added(b2) or "2026-09-08" in _checkin_updated(b2):
        fails.append("B2: 不得改写 09-08 行")
    return fails, flags


def _grade_g06(b):
    fails, flags = [], []
    b1, b2 = b[0], b[1]
    a1 = _checkin_added(b1)
    if "2026-07-29" not in a1:
        fails.append("B1: 补卡 target 必须 2026-07-29 MUST_ADD 缺失")
    if "2026-07-30" in a1:
        fails.append("B1: 不得建 07-30 行")
    a2 = _checkin_added(b2)
    if not str(_flatten_checkin_parsed(a2.get("2026-07-30", {})).get("product_schedule.morning_c") or ""):
        fails.append("B2: checkins(2026-07-30) morning_c MUST_ADD 缺失")
    if "2026-07-29" in a2 or "2026-07-29" in _checkin_updated(b2):
        fails.append("B2: 不得重报 07-29 已归档事实")
    return fails, flags


def _grade_g07(b):
    fails, flags = [], []
    b1, b2 = b[0], b[1]
    upd = _checkin_updated(b1).get("2026-09-10")
    if upd is None:
        fails.append("B1: checkins(2026-09-10) 矛盾行 MUST_NOT_MISS 缺失")
    else:
        if "100" not in str((upd.get("after") or {}).get("weight") or ""):
            fails.append("B1: 表单 100 MUST_NOT_DUPLICATE 被聊天值覆盖")
        if not upd.get("contradictions_after"):
            fails.append("B1: weight 矛盾 MUST_NOT_MISS 未留痕")
        if "未喝" not in str((upd.get("after") or {}).get("product_schedule.night_a") or ""):
            fails.append("B1: night_a=未喝 MUST_ADD 缺失")
    a2 = _checkin_added(b2)
    if "168" not in str(_flatten_checkin_parsed(a2.get("2026-09-11", {})).get("weight") or ""):
        fails.append("B2: checkins(2026-09-11) weight=168斤 MUST_ADD 缺失")
    if "2026-09-10" in a2 or "2026-09-10" in _checkin_updated(b2):
        fails.append("B2: 不得改写 09-10 行")
    return fails, flags


def _grade_g08(b):
    fails, flags = [], []
    b1, b2 = b[0], b[1]
    if not _profile_changed(b1):
        fails.append("B1: profile 习惯 MUST_ADD 未落库")
    if not str(_flatten_checkin_parsed(
            _checkin_added(b2).get("2026-09-13", {})).get("water") or ""):
        fails.append("B2: checkins(2026-09-13) water MUST_ADD 缺失")
    v1 = {str(a.get("value") or "") for a in _emitted_args(b1, "report_profile_fact") if a.get("value")}
    dup = [str(a.get("value") or "") for a in _emitted_args(b2, "report_profile_fact") if str(a.get("value") or "") in v1]
    if dup:
        fails.append(f"B2: profile 重复 B1 习惯 MUST_NOT_DUPLICATE: {dup[:2]}")
    return fails, flags


def _grade_g09(b):
    fails, flags = [], []
    b1, b2, b3 = b[0], b[1], b[2]
    if not _outcome_added(b1, event_type="worsened"):
        fails.append("B1: 不适 outcome worsened MUST_ADD 缺失")
    if "未喝" not in str(_flatten_checkin_parsed(
            _checkin_added(b1).get("2026-09-14", {})).get("product_schedule.night_a") or ""):
        fails.append("B1: checkins(2026-09-14) night_a=未喝 MUST_ADD 缺失")
    if not _profile_changed(b1):
        fails.append("B1: discomfort active MUST_ADD 未落库")
    if not _profile_changed(b2):
        fails.append("B2: 同 state_type MUST_UPDATE resolved 未落库")
    added2 = _checkin_added(b2)
    night15 = str(_flatten_checkin_parsed(
            added2.get("2026-09-15", {})).get("product_schedule.night_a") or "")
    if "已喝" not in night15:
        fails.append("B2: 昨天晚A回补 09-15 night_a=已喝 MUST_ADD 缺失")
    night16 = str(_flatten_checkin_parsed(
            added2.get("2026-09-16", {})).get("product_schedule.night_a") or "")
    if "已喝" in night16:
        fails.append("B2: 昨天剂量不得记到 09-16 MUST_NOT_DUPLICATE")
    if _outcome_added(b2, event_type="worsened"):
        fails.append("B2: 胃胀 worsened MUST_NOT_DUPLICATE 被重报")
    if _outcome_added(b3) or _profile_changed(b3) or _mem_changed(b3):
        fails.append("B3: 无新信息 MUST_NOT_DUPLICATE 被新增")
    if not _wm_advanced(b3):
        fails.append("B3: 水位线 MUST_UPDATE 未推进到 903")
    return fails, flags


def _grade_g10(b):
    fails, flags = [], []
    b1, b2 = b[0], b[1]
    if not [a for a in _emitted_args(b1, "report_correction")
            if str(a.get("scope") or "") == "general"]:
        fails.append("B1: correction general MUST_ADD 缺失")
    if len(_drafts_added(b1)) > 1:
        fails.append("B1: FAQ 候选 ALLOWED 0-or-1 超限")
    if _kept_count(b2, "correction") > 0:
        fails.append("B2: correction MUST_NOT_DUPLICATE 被新建")
    if _drafts_added(b2):
        fails.append("B2: FAQ MUST_NOT_DUPLICATE 被新建")
    return fails, flags


def _grade_g11(b):
    fails, flags = [], []
    b1, b2, b3 = b[0], b[1], b[2]
    if "general" not in [str(d.get("scope") or "") for d in _drafts_added(b1)]:
        fails.append("B1: FAQ general 草稿 MUST_ADD 缺失")
    d2 = _drafts_added(b2)
    if not [d for d in d2 if str(d.get("scope") or "") in ("general", "personal")]:
        fails.append("B2: 出差问答 general/personal 任一 scope MUST_ADD 缺失")
    q1 = {str(d.get("question") or "") for d in _drafts_added(b1) if d.get("question")}
    if len(d2) > 1 or any(str(d.get("question") or "") in q1 for d in d2 if d.get("question")):
        fails.append("B2: 不得复制 B1 通用稿")
    if _drafts_added(b3):
        fails.append("B3: 无新回答 MUST_NOT_DUPLICATE 被新建")
    if not _wm_advanced(b3):
        fails.append("B3: 水位线 MUST_UPDATE 未推进到 1106")
    return fails, flags


def _grade_g12(b):
    fails, flags = [], []
    b1, b2, b3 = b[0], b[1], b[2]
    if not [r for r in _outcome_added(b1) if str(r.get("event_type") or "") in ("improved", "completed")]:
        fails.append("B1: 正向 outcome MUST_ADD 缺失")
    if not _mem_changed(b1):
        fails.append("B1: 小结 MUST_ADD 未落库")
    if len([d for d in _drafts_added(b1) if str(d.get("draft_type") or "") == "case"]) > 1:
        fails.append("B1: case 候选 ALLOWED 最多一稿")
    if _mem_changed(b2):
        fails.append("B2: 无新事实小结 MUST_NOT_DUPLICATE 被重写")
    if _outcome_added(b2) or [d for d in _drafts_added(b2) if str(d.get("draft_type") or "") == "case"]:
        fails.append("B2: 感谢批 outcome/case MUST_NOT_DUPLICATE 被新增")
    if len([a for a in _emitted_args(b2, "report_state_fact")
            if str(a.get("state_type") or "") == "user_commitment"]) > 1:
        fails.append("B2: user_commitment ALLOWED 0-or-1 超限")
    if not _outcome_added(b3):
        fails.append("B3: 新进展 outcome MUST_ADD 缺失")
    if not _mem_changed(b3):
        fails.append("B3: case_material MUST_UPDATE 未落库")
    c1 = [d for d in _drafts_added(b1) if str(d.get("draft_type") or "") == "case"]
    c3 = [d for d in _drafts_added(b3) if str(d.get("draft_type") or "") == "case"]
    if len(c3) > 1:
        fails.append("B3: case 最多一稿")
    t1 = {str(d.get("question") or d.get("title") or "") for d in c1}
    if c3 and any(str(d.get("question") or d.get("title") or "") in t1 for d in c3):
        fails.append("B3: 不得重复 B1 旧故事建稿")
    return fails, flags


def _looks_like_bedtime(text) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    if any(bad in value for bad in ("11小时", "11 小时", "11h", "11 h")):
        return False
    return any(mark in value for mark in ("23", "11点", "11:00", "晚11"))


def _grade_g13(b):
    fails, flags = [], []
    b1, b2, b3 = b[0], b[1], b[2]
    flat1 = _flatten_checkin_parsed(_checkin_added(b1).get("2026-09-30", {}))
    if str(flat1.get("sleep") or "").strip():
        fails.append("B1: 睡前宣告无时长，不得填 sleep 时长")
    if any(k.startswith("custom_checkin.") and str(v or "").strip() for k, v in flat1.items()):
        fails.append("B1: 无时长证据不得编睡眠清单答案")
    for args in _emitted_args(b1, "report_checkin"):
        if str(args.get("sleep") or "").strip() or args.get("checklist_answers"):
            fails.append("B1: 模型上报 sleep 时长/清单答案属幻觉")
            break
    sleep2 = str(_flatten_checkin_parsed(
            _checkin_added(b2).get("2026-10-01", {})).get("sleep") or "")
    if not _looks_like_bedtime(sleep2):
        fails.append(f"B2: checkins(2026-10-01) sleep 应为23点睡，实际 {sleep2!r}")
    sleep3 = str(_flatten_checkin_parsed(
            _checkin_added(b3).get("2026-10-02", {})).get("sleep") or "")
    if not _looks_like_bedtime(sleep3):
        fails.append(f"B3: checkins(2026-10-02) sleep 应为23点睡，实际 {sleep3!r}")
    if "2026-10-01" in _checkin_added(b3) or "2026-10-01" in _checkin_updated(b3):
        fails.append("B3: 不得重写 10-01 行")
    return fails, flags


_GRADERS = {
    "G01": _grade_g01, "G02": _grade_g02, "G03": _grade_g03, "G04": _grade_g04,
    "G05": _grade_g05, "G06": _grade_g06, "G07": _grade_g07, "G08": _grade_g08,
    "G09": _grade_g09, "G10": _grade_g10, "G11": _grade_g11, "G12": _grade_g12,
    "G13": _grade_g13,
}


def grade_group(group_result: dict) -> dict:
    code = str(group_result.get("group_code") or "")
    batches = group_result.get("batches") or []
    entry: dict = {
        "group_code": code,
        "repeat_index": group_result.get("repeat_index"),
        "verdict": "FLAG",
        "failed_assertions": [],
        "flags": [],
    }
    fn = _GRADERS.get(code)
    if fn is None:
        entry["failed_assertions"] = [f"未知组 {code}，无冻结期望可评"]
        return entry
    want = len((DEDUP12_GROUPS.get(code) or {}).get("batches", []))
    if len(batches) != want:
        entry["failed_assertions"] = [f"批数 {len(batches)} != 冻结 {want}"]
        return entry
    fails, flags = fn(batches)
    entry["failed_assertions"] = fails
    entry["flags"] = flags
    entry["verdict"] = "FLAG" if flags else ("FAIL" if fails else "PASS")
    return entry


def _print_grading_table(grading: list[dict]) -> None:
    print("group  repeat  verdict  issues")
    totals = {"PASS": 0, "FAIL": 0, "FLAG": 0}
    for g in grading:
        totals[str(g.get("verdict"))] = totals.get(str(g.get("verdict")), 0) + 1
        n = len(g.get("failed_assertions") or []) + len(g.get("flags") or [])
        print(f"{g.get('group_code'):5}  {g.get('repeat_index'):6}  {g.get('verdict'):7}  {n}")
    print(f"TOTAL PASS={totals.get('PASS', 0)} FAIL={totals.get('FAIL', 0)} FLAG={totals.get('FLAG', 0)}")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=["dedup12", "generic"], default="dedup12")
    parser.add_argument("--groups", type=int, default=None, help="dedup12 下截取前 N 组（默认全 13 组）；generic 下为组数（默认 2）")
    parser.add_argument("--only", default=None, help="仅跑指定组，逗号分隔，如 G01,G04（仅 dedup12）")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true", help="只校验 group spec，不碰 DB、不调 LLM")
    parser.add_argument("--real", action="store_true", help="真实 LLM（需 REAL_ARCHIVIST_EVAL=1）")
    parser.add_argument("--keep", action="store_true", help="保留测试数据不清（默认清理）")
    parser.add_argument("--ai-base-url", default=None, help="覆盖 AI_BASE_URL（不写 .env）")
    parser.add_argument("--out-dir", default=str(ARTIFACTS_DIR))
    return parser.parse_args(argv)


async def amain(args: argparse.Namespace) -> int:
    global _KEEP
    _KEEP = args.keep

    if args.ai_base_url:
        os.environ["AI_BASE_URL"] = args.ai_base_url
        try:
            from app.core.config import get_settings as _gs

            if hasattr(_gs, "cache_clear"):
                _gs.cache_clear()
        except Exception:
            pass

    if args.suite == "dedup12":
        if args.only:
            codes = [c.strip().upper() for c in args.only.split(",") if c.strip()]
            unknown = [c for c in codes if c not in DEDUP12_CODES]
            if unknown:
                print(f"ERROR: unknown group codes {unknown}, want one of {DEDUP12_CODES}", file=sys.stderr)
                return 2
        else:
            codes = list(DEDUP12_CODES)
            if args.groups is not None:
                codes = codes[: args.groups]
        specs = [build_dedup12_spec(code, r) for r in range(args.repeats) for code in codes]
    else:
        n_groups = args.groups if args.groups is not None else 2
        specs = [build_group_spec(g, r) for r in range(args.repeats) for g in range(n_groups)]
        codes = []

    if args.dry_run:
        summaries = [validate_group_spec(s) for s in specs]
        print(json.dumps({"dry_run": True, "suite": args.suite, "specs": summaries}, ensure_ascii=False, indent=2))
        return 0

    assert_no_real_channel()

    ai_settings: dict = {}
    if args.real:
        if os.getenv("REAL_ARCHIVIST_EVAL") != "1":
            print("ERROR: --real requires env REAL_ARCHIVIST_EVAL=1", file=sys.stderr)
            return 2
        from app.core.config import get_settings

        settings = get_settings()  # read at runtime, never hardcoded
        if not settings.ai_api_key:
            print("ERROR: AI_API_KEY 未配置", file=sys.stderr)
            return 2
        ai_settings = {
            "ai_model": settings.ai_model,
            "ai_base_url": settings.ai_base_url,
            "ai_temperature": getattr(settings, "ai_temperature", None),
        }
        print(f"[real] model={ai_settings['ai_model']} base_url={ai_settings['ai_base_url']}")
    else:
        ai_settings = {"ai_model": "fake (no LLM)", "ai_base_url": None}

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(SH).strftime("%Y%m%d-%H%M%S")
    jsonl_path = out_dir / f"archivist-dedup-eval-{stamp}.jsonl"
    summary_path = out_dir / f"archivist-dedup-eval-summary-{stamp}.json"

    total_tokens = 0
    total_cost = 0.0
    n_batches = 0
    grading: list[dict] = []
    scope_emitted_total: dict[str, int] = {}
    scope_created_total: dict[str, int] = {}
    with open(jsonl_path, "w", encoding="utf-8") as fh:
        for spec in specs:
            result = await run_group(spec, real=args.real, ai_settings=ai_settings)
            fh.write(json.dumps(result, ensure_ascii=False, default=str) + "\n")
            fh.flush()
            if args.suite == "dedup12":
                grading.append(grade_group(result))
            for b in result["batches"]:
                n_batches += 1
                for u in b.get("usage") or []:
                    total_tokens += int(u.get("total_tokens") or 0)
                    total_cost += float(u.get("estimated_cost_yuan") or 0.0)
                for s in b.get("faq_scopes_emitted") or []:
                    scope_emitted_total[s] = scope_emitted_total.get(s, 0) + 1
                for s in b.get("faq_scopes_created") or []:
                    scope_created_total[s] = scope_created_total.get(s, 0) + 1

    summary = {
        "stamp": stamp,
        "mode": "real" if args.real else "fake",
        "suite": args.suite,
        "group_codes": codes if args.suite == "dedup12" else None,
        "groups": len(codes) if args.suite == "dedup12" else (args.groups if args.groups is not None else 2),
        "repeats": args.repeats,
        "batches": n_batches,
        "total_tokens": total_tokens,
        "total_cost_yuan": round(total_cost, 6),
        "ai_settings": ai_settings,
        "faq_scope_totals": {"emitted": scope_emitted_total, "created": scope_created_total},
        "grading": grading,
        "evidence_jsonl": str(jsonl_path),
    }
    with open(summary_path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if grading:
        _print_grading_table(grading)
    return 0


def main(argv=None) -> None:
    raise SystemExit(asyncio.run(amain(parse_args(argv))))


if __name__ == "__main__":
    main()
