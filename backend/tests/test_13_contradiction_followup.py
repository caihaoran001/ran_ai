"""打卡追问（H5 完整替代）——混合架构：日常搭便车 + 里程碑/矛盾单发线 + 档案官关键信息快触发。

覆盖：
- 档案官触发器：含关键类目(checkin/profile_fact/outcome)且用户停说≥3min → fast 快触发；
  仅闲聊 worthy 且未到 24h → 不触发。
- 日常搭便车 compute_gap_hint：算出今日缺口→喂聊天机器人「今日待补」；退避(每天最多提一次、
  连提 3 天不报即停)并落 surface 事件；无缺口/被退避→空串。
- 里程碑档：服务第 6/13/20/27 天检测出 milestone；LLM 生成复盘追问、失败回退模板；need_waist 按腰围记录决定。
- 矛盾核对：表单值≠聊天值→记矛盾→单发线自然语言核对→标 asked(去重)+字段降级→用户新值覆盖表单。
- 门控/退役校验：checkin_followup_enabled 关时搭便车不注入；check_once 不再被 _run_loop 调度；
  单发线源码不再含日常槽位分支。
"""

from __future__ import annotations

import inspect
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models.checkin import Checkin
from app.models.message import (
    Message,
    MessageDirection,
    MessagePurpose,
    MessageSenderType,
    MessageStatus,
)
from app.models.reminder import ReminderEvent, ReminderEventType
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user import User
from app.services.agent.graph import RanrenAgentGraph
from app.services.agent.state import AgentRunContext
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
    compute_gap_hint,
    detect_followup,
)
from app.services.archivist.trigger import (
    KEY_INFO_SETTLE_SECONDS,
    _evaluate,
    _has_key_kind,
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

    msg = compose_followup_message(confirmations=detect["confirmations"])
    assert msg and "体重" in msg and "改" in msg

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
# 退避纯函数（compute_gap_hint 与旧单发线共用）
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


def test_single_line_no_longer_sends_daily_slots():
    """单发线源码不应再含日常槽位/字段级退避分支；里程碑 + 矛盾核对仍在。"""
    from app.services.reminder_service import ReminderService

    src = inspect.getsource(ReminderService.check_checkin_followup_once)
    assert "missing_slots" not in src, "日常槽位追问应已移交搭便车"
    assert "allowed_after_backoff" not in src, "字段级退避应已移出单发线"
    assert "compose_milestone_followup" in src  # 里程碑仍在
    assert "mark_contradictions_pending_confirmation" in src  # 矛盾核对状态机仍在


# ---------------------------------------------------------------------------
# 档案官触发器：关键信息快触发
# ---------------------------------------------------------------------------


def test_has_key_kind():
    assert _has_key_kind({"archive_plan": {"kinds": ["checkin"]}}) is True
    assert _has_key_kind({"archive_plan": {"kinds": ["profile_fact", "knowledge_qa"]}}) is True
    assert _has_key_kind({"archive_plan": {"kinds": ["knowledge_qa"]}}) is False
    assert _has_key_kind({"archive_plan": {"kinds": []}}) is False
    assert _has_key_kind({"image_parse_status": "ok"}) is True  # 餐食/体重秤图比照关键
    assert _has_key_kind({}) is False
    assert _has_key_kind(None) is False


async def _user_msg(db_session, case, *, analysis_json: dict, created_at: datetime) -> Message:
    msg = Message(
        service_case_id=case.id,
        group_id=case.group_id,
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.USER,
        content="（用户消息）",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
        analysis_json=analysis_json,
        created_at=created_at,
    )
    db_session.add(msg)
    await db_session.flush()
    return msg


@pytest.mark.asyncio
async def test_trigger_fast_on_key_info_after_settle(db_session):
    case = await _case(db_session)
    base = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    await _user_msg(
        db_session, case, analysis_json={"archive_plan": {"worthy": True, "kinds": ["checkin"]}}, created_at=base
    )
    now = base + timedelta(seconds=KEY_INFO_SETTLE_SECONDS + 60)  # 停说超过去抖窗口
    should, reason = await _evaluate(
        db_session, service_case_id=case.id, watermark=0, last_archived_at=None, now=now
    )
    assert should is True
    assert "fast_key" in reason


@pytest.mark.asyncio
async def test_trigger_holds_key_info_within_settle(db_session):
    case = await _case(db_session)
    base = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    await _user_msg(
        db_session, case, analysis_json={"archive_plan": {"worthy": True, "kinds": ["checkin"]}}, created_at=base
    )
    now = base + timedelta(seconds=60)  # 刚说完 1 分钟 <3 分钟去抖
    should, reason = await _evaluate(
        db_session, service_case_id=case.id, watermark=0, last_archived_at=None, now=now
    )
    assert should is False
    assert reason == "not_due"


@pytest.mark.asyncio
async def test_trigger_chatty_worthy_waits_24h(db_session):
    case = await _case(db_session)
    base = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    # worthy 但无关键类目（纯闲聊可归档）→ 不走 fast，只能等 24h 兜底。
    await _user_msg(
        db_session, case, analysis_json={"archive_plan": {"worthy": True, "kinds": []}}, created_at=base
    )
    should_early, reason_early = await _evaluate(
        db_session, service_case_id=case.id, watermark=0, last_archived_at=None,
        now=base + timedelta(hours=1),
    )
    assert should_early is False and reason_early == "not_due"
    should_late, reason_late = await _evaluate(
        db_session, service_case_id=case.id, watermark=0, last_archived_at=None,
        now=base + timedelta(hours=25),
    )
    assert should_late is True and "B_latency" in reason_late


@pytest.mark.asyncio
async def test_trigger_gate_no_worthy(db_session):
    case = await _case(db_session)
    base = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    await _user_msg(
        db_session, case, analysis_json={"archive_plan": {"worthy": False, "kinds": []}}, created_at=base
    )
    should, reason = await _evaluate(
        db_session, service_case_id=case.id, watermark=0, last_archived_at=None,
        now=base + timedelta(hours=25),
    )
    assert should is False and reason == "gate_no_worthy"


# ---------------------------------------------------------------------------
# 日常搭便车：compute_gap_hint（今日待补 + 退避）
# ---------------------------------------------------------------------------


async def _surface_events(db_session, case) -> list[str]:
    rows = (
        await db_session.scalars(
            select(ReminderEvent).where(
                ReminderEvent.service_case_id == case.id,
                ReminderEvent.rule_id.like("checkin_followup:%"),
            )
        )
    ).all()
    return sorted(e.rule_id for e in rows)


@pytest.mark.asyncio
async def test_gap_hint_reports_missing_and_records_events(db_session):
    case = await _case(db_session)
    today = date.today()
    # 今天没有任何打卡 → 早C/午B/晚A + 体重全缺。
    hint = await compute_gap_hint(db_session, service_case_id=case.id, today=today)
    assert "早C" in hint and "午B" in hint and "晚A" in hint and "体重" in hint
    events = await _surface_events(db_session, case)
    assert "checkin_followup:morning_c" in events
    assert "checkin_followup:weight" in events


@pytest.mark.asyncio
async def test_gap_hint_backoff_skips_same_day(db_session):
    case = await _case(db_session)
    today = date.today()
    first = await compute_gap_hint(db_session, service_case_id=case.id, today=today)
    assert first  # 首次有提示
    # 同一天再问：所有字段昨/今已提示，backoff_days=1 冷却→全跳过→空串。
    second = await compute_gap_hint(db_session, service_case_id=case.id, today=today)
    assert second == ""


@pytest.mark.asyncio
async def test_gap_hint_empty_when_all_reported(db_session):
    case = await _case(db_session)
    today = date.today()
    checkin = Checkin(
        service_case_id=case.id,
        group_id=case.group_id,
        user_id=case.user_id,
        checkin_date=today,
        content="（全打卡）",
        parsed_json={
            "weight": "68kg",
            "product_schedule": {"morning_c": "已喝", "noon_b": "已喝", "night_a": "已喝"},
        },
    )
    db_session.add(checkin)
    await db_session.flush()
    hint = await compute_gap_hint(db_session, service_case_id=case.id, today=today)
    assert hint == ""


def test_ride_along_backoff_daily_then_stop():
    today = date(2026, 7, 20)
    # 连续 3 天各提示一次(达 max_asks=3) → 今天永久放弃。
    hist3 = [("weight", date(2026, 7, 17)), ("weight", date(2026, 7, 18)), ("weight", date(2026, 7, 19))]
    assert allowed_after_backoff(["weight"], hist3, today, backoff_days=1, max_asks=3) == []
    # 仅昨天提示过(距 1 天，未达上限) → backoff_days=1 下今天可再提。
    hist1 = [("weight", date(2026, 7, 19))]
    assert allowed_after_backoff(["weight"], hist1, today, backoff_days=1, max_asks=3) == ["weight"]


# ---------------------------------------------------------------------------
# 搭便车门控：checkin_followup_enabled / 班主任发言
# ---------------------------------------------------------------------------


def _graph(case, *, enabled: bool) -> RanrenAgentGraph:
    settings = get_settings().model_copy(update={"checkin_followup_enabled": enabled})
    return RanrenAgentGraph(
        context=AgentRunContext(service_case_id=case.id),
        settings=settings,
        chat_model=object(),  # 不 run，仅测门控；object 无 bind_tools 直接用作 model
    )


@pytest.mark.asyncio
async def test_gap_hint_gated_off_when_disabled(db_session):
    case = await _case(db_session)
    graph = _graph(case, enabled=False)
    assert await graph._build_checkin_gap_hint(query_speaker="用户") == ""


@pytest.mark.asyncio
async def test_gap_hint_skipped_for_staff(db_session):
    case = await _case(db_session)
    graph = _graph(case, enabled=True)
    # 班主任发言不触发打卡追问（不拆班主任台）。
    assert await graph._build_checkin_gap_hint(query_speaker="班主任") == ""
