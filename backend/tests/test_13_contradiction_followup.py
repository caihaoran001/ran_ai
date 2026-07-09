"""打卡追问：日常矛盾核对 + 里程碑复盘追问（H5 完整替代）。

覆盖两档：
- 日常档：表单值≠聊天值→记矛盾→晚间自然语言追问→标 asked(去重)+字段降级→用户新值覆盖表单；
  以及模板变体按 case_id 轮换。
- 里程碑档：服务第 6/13/20/27 天(里程碑前一晚)检测出 milestone；LLM 生成复盘追问、失败回退模板；
  need_waist 按最近是否已记录腰围决定。
- 退役校验：check_once 不再被 _run_loop 调度。
"""

from __future__ import annotations

import inspect
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.models.checkin import Checkin
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user import User
from app.services.archivist.checkin_projection import (
    SOURCE_FORM,
    SOURCE_USER_CONFIRMED,
    mark_contradictions_pending_confirmation,
    upsert_checkin,
)
from app.services.archivist.followup import (
    allowed_after_backoff,
    compose_followup_message,
    compose_milestone_followup,
    detect_followup,
)

_TZ = ZoneInfo("Asia/Shanghai")


async def _case(db_session, *, started_days_ago: int = 3) -> ServiceCase:
    user = User(name="矛盾追问测试")
    db_session.add(user)
    await db_session.flush()
    now = datetime.now(_TZ)
    case = ServiceCase(
        case_no=f"TEST-CONF-{uuid4().hex[:6].upper()}",
        user_id=user.id,
        group_id=f"conf-{uuid4().hex[:8]}",
        questionnaire_token=uuid4().hex,
        status=ServiceCaseStatus.IN_SERVICE,
        started_at=now - timedelta(days=started_days_ago),
    )
    db_session.add(case)
    await db_session.flush()
    return case


async def _form_checkin_with_weight(db_session, case, day: date, weight: str) -> Checkin:
    """造一条"表单权威"的当天打卡：weight 来源标 form。"""
    checkin = Checkin(
        service_case_id=case.id,
        group_id=case.group_id,
        user_id=case.user_id,
        checkin_date=day,
        content="（表单打卡）",
        parsed_json={"weight": weight, "_meta": {"sources": {"weight": SOURCE_FORM}, "contradictions": []}},
    )
    db_session.add(checkin)
    await db_session.flush()
    return checkin


class _FakeAI:
    """注入用假 AIService：generate_text 返回预设串并记录调用。"""

    def __init__(self, text: str = "假的复盘追问～"):
        self.text = text
        self.calls: list[dict] = []

    async def generate_text(self, *, system_prompt, user_prompt, **kwargs):
        self.calls.append({"system": system_prompt, "user": user_prompt, **kwargs})
        return self.text


class _RaisingAI:
    async def generate_text(self, *, system_prompt, user_prompt, **kwargs):
        raise RuntimeError("LLM 挂了")


# ---------------------------------------------------------------------------
# 日常档：矛盾核对
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_form_vs_chat_contradiction_is_kept_and_flagged(db_session):
    case = await _case(db_session)
    today = date.today()
    await _form_checkin_with_weight(db_session, case, today, "100斤")

    # 聊天说 168 → 与表单 100 冲突：不覆盖、记矛盾。
    res = await upsert_checkin(
        db_session, service_case=case, checkin_date=today, fields={"weight": "168斤"}
    )
    assert res.contradictions and res.contradictions[0]["field"] == "weight"

    row = await db_session.scalar(
        select(Checkin).where(Checkin.service_case_id == case.id, Checkin.deleted_at.is_(None))
    )
    assert row.parsed_json["weight"] == "100斤"  # 表单权威未被覆盖


@pytest.mark.asyncio
async def test_detect_and_compose_confirmation(db_session):
    case = await _case(db_session)
    today = date.today()
    checkin = await _form_checkin_with_weight(db_session, case, today, "100斤")
    await upsert_checkin(
        db_session, service_case=case, checkin_date=today, fields={"weight": "168斤"}
    )

    now = datetime.now(_TZ)
    detect = await detect_followup(db_session, service_case=case, now=now, tz=_TZ)
    assert len(detect["confirmations"]) == 1
    assert detect["confirmations"][0]["label"] == "体重"

    msg = compose_followup_message(
        missing_slots=[], ask_weight=False, confirmations=detect["confirmations"]
    )
    assert msg and "体重" in msg and "更新" in msg

    # 标记已问后：不再重复追问（去重）。
    mark_contradictions_pending_confirmation(checkin, ["weight"])
    await db_session.flush()
    detect2 = await detect_followup(db_session, service_case=case, now=now, tz=_TZ)
    assert detect2["confirmations"] == []


@pytest.mark.asyncio
async def test_user_confirmed_value_overrides_form(db_session):
    case = await _case(db_session)
    today = date.today()
    checkin = await _form_checkin_with_weight(db_session, case, today, "100斤")
    await upsert_checkin(
        db_session, service_case=case, checkin_date=today, fields={"weight": "168斤"}
    )

    # 追问 → 降级 weight 来源为 user_confirmed。
    mark_contradictions_pending_confirmation(checkin, ["weight"])
    await db_session.flush()
    row = await db_session.scalar(
        select(Checkin).where(Checkin.service_case_id == case.id, Checkin.deleted_at.is_(None))
    )
    assert row.parsed_json["_meta"]["sources"]["weight"] == SOURCE_USER_CONFIRMED
    assert row.parsed_json["_meta"]["contradictions"][0]["asked"] is True

    # 用户答复"就是168" → 新聊天值覆盖原表单值，不再记新矛盾。
    res = await upsert_checkin(
        db_session, service_case=case, checkin_date=today, fields={"weight": "168斤"}
    )
    assert not res.contradictions
    row2 = await db_session.scalar(
        select(Checkin).where(Checkin.service_case_id == case.id, Checkin.deleted_at.is_(None))
    )
    assert row2.parsed_json["weight"] == "168斤"  # 用户确认覆盖表单


# ---------------------------------------------------------------------------
# 日常档：模板变体
# ---------------------------------------------------------------------------


def test_daily_slot_variants_rotate_by_case_id():
    # 不同 case_id 可能取不同变体；同一 case_id 稳定。
    m0a = compose_followup_message(missing_slots=["早C"], ask_weight=False, case_id=0)
    m0b = compose_followup_message(missing_slots=["早C"], ask_weight=False, case_id=0)
    m1 = compose_followup_message(missing_slots=["早C"], ask_weight=False, case_id=1)
    assert m0a == m0b  # 同 id 稳定
    assert "早C" in m0a
    # 至少存在一对不同 id 产生不同话术（3 个变体）
    variants = {
        compose_followup_message(missing_slots=["早C"], ask_weight=False, case_id=i)
        for i in range(3)
    }
    assert len(variants) >= 2


# ---------------------------------------------------------------------------
# 日常档：退避降频（避免天天问同一件事）
# ---------------------------------------------------------------------------


def test_backoff_allows_first_ask_when_no_history():
    today = date(2026, 7, 10)
    assert allowed_after_backoff(["morning_c", "weight"], [], today) == ["morning_c", "weight"]


def test_backoff_skips_within_cooldown():
    today = date(2026, 7, 10)
    # 昨天刚追过 morning_c（冷却 3 天内）→ 本次跳过；weight 没追过 → 放行。
    history = [("morning_c", date(2026, 7, 9))]
    assert allowed_after_backoff(["morning_c", "weight"], history, today) == ["weight"]


def test_backoff_reallows_after_cooldown():
    today = date(2026, 7, 10)
    # 4 天前追过（超过冷却 3 天）→ 重新放行。
    history = [("morning_c", date(2026, 7, 6))]
    assert allowed_after_backoff(["morning_c"], history, today) == ["morning_c"]


def test_backoff_stops_after_max_asks():
    today = date(2026, 7, 20)
    # 累计追满 3 次（默认上限）→ 永久停追，即使早已过冷却期。
    history = [
        ("morning_c", date(2026, 7, 5)),
        ("morning_c", date(2026, 7, 9)),
        ("morning_c", date(2026, 7, 13)),
    ]
    assert allowed_after_backoff(["morning_c"], history, today) == []


# ---------------------------------------------------------------------------
# 里程碑档：检测
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_milestone_eve_detected_on_day6(db_session):
    case = await _case(db_session, started_days_ago=5)  # 计划开始当天=第1天 → 今天第6天
    now = datetime.now(_TZ)
    detect = await detect_followup(db_session, service_case=case, now=now, tz=_TZ)
    assert detect["milestone"] is not None
    assert detect["milestone"]["milestone_day"] == 7
    assert detect["milestone"]["need_waist"] is True  # 无腰围记录


@pytest.mark.asyncio
async def test_non_milestone_day_has_no_milestone(db_session):
    case = await _case(db_session, started_days_ago=3)  # 第4天，非里程碑前夕
    now = datetime.now(_TZ)
    detect = await detect_followup(db_session, service_case=case, now=now, tz=_TZ)
    assert detect["milestone"] is None


@pytest.mark.asyncio
async def test_milestone_need_waist_false_when_recent_waist_exists(db_session):
    case = await _case(db_session, started_days_ago=5)  # 第6天
    today = date.today()
    checkin = Checkin(
        service_case_id=case.id,
        group_id=case.group_id,
        user_id=case.user_id,
        checkin_date=today,
        content="（腰围）",
        parsed_json={"waist": "80cm"},
    )
    db_session.add(checkin)
    await db_session.flush()
    now = datetime.now(_TZ)
    detect = await detect_followup(db_session, service_case=case, now=now, tz=_TZ)
    assert detect["milestone"]["need_waist"] is False


# ---------------------------------------------------------------------------
# 里程碑档：话术生成（LLM + 兜底）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_milestone_followup_uses_injected_llm(db_session):
    case = await _case(db_session, started_days_ago=5)
    fake = _FakeAI("你这周辛苦啦，腰围量了吗？最大的变化和卡点跟我聊聊～")
    msg = await compose_milestone_followup(
        service_case=case, milestone_day=7, need_waist=True, ai_service=fake
    )
    assert msg == fake.text
    assert fake.calls and fake.calls[0]["scenario"] == "checkin_followup"


@pytest.mark.asyncio
async def test_milestone_followup_falls_back_on_llm_error(db_session):
    case = await _case(db_session, started_days_ago=5)
    msg = await compose_milestone_followup(
        service_case=case, milestone_day=7, need_waist=True, ai_service=_RaisingAI()
    )
    # 回退固定模板：含腰围提醒 + 复盘要素(变化/卡点)。
    assert "腰围" in msg
    assert "变化" in msg and "卡" in msg
    # 不反问用户"下阶段重点/想解决什么"(交给阶段报告生成)。
    assert "下阶段" not in msg


@pytest.mark.asyncio
async def test_milestone_fallback_skips_waist_when_not_needed(db_session):
    case = await _case(db_session, started_days_ago=5)
    msg = await compose_milestone_followup(
        service_case=case, milestone_day=14, need_waist=False, ai_service=_RaisingAI()
    )
    assert "腰围" not in msg
    assert "变化" in msg


# ---------------------------------------------------------------------------
# 退役校验
# ---------------------------------------------------------------------------


def test_check_once_retired_from_run_loop():
    from app.services.reminder_service import ReminderService

    src = inspect.getsource(ReminderService._run_loop)
    assert "self.check_once(" not in src, "check_once 已退役，不应再被 _run_loop 调度"
    assert "check_stage_reports_once" in src  # 阶段报告仍在调度
