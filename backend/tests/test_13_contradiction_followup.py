"""打卡追问（H5 完整替代）——外部确定性触发、每 case 每天最多一条 + 档案官关键信息快触发。

覆盖：
- 档案官触发器：含关键类目(checkin/profile_fact/outcome)且用户停说≥3min → fast 快触发；
  仅闲聊 worthy 且未到 24h → 不触发。
- compose_daily_followup 统一组织：优先级 里程碑 > 矛盾核对 > 日常缺口(早C/午B、体重合并成一句；
  晚A 睡前那顿不主动追)；无可问返回 None；LLM 生成复盘/缺口话术、失败回退模板。
- 里程碑档：服务第 6/13/20/27 天检测出 milestone；need_waist 按腰围记录决定。
- 矛盾核对：表单值≠聊天值→记矛盾→自然语言核对→标 asked(去重)+字段降级→用户新值覆盖表单。
- 不活跃兜底辅助：_deterministic_daily_time 同 case 同日恒定且落窗口内；日常缺口受傍晚窗口闸。
"""

from __future__ import annotations

import inspect
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.models.checkin import Checkin
from app.models.message import (
    Message,
    MessageDirection,
    MessagePurpose,
    MessageSenderType,
    MessageStatus,
)
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user import User
from app.services.archivist.checkin_projection import (
    SOURCE_FORM,
    SOURCE_USER_CONFIRMED,
    mark_contradictions_pending_confirmation,
    upsert_checkin,
)
from app.services.archivist.followup import (
    DailyFollowup,
    compose_daily_followup,
    compose_followup_message,
    compose_milestone_followup,
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
# compose_daily_followup 统一组织：优先级 + 合并 + None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_daily_followup_milestone_wins_over_gap(db_session):
    # 第6天（里程碑前夕）且今天全缺 → 里程碑优先，忽略日常缺口。
    case = await _case(db_session, started_days_ago=5)
    now = datetime.now(_TZ)
    fake = _FakeAI("复盘一下这周～")
    fu = await compose_daily_followup(
        db_session, service_case=case, now=now, tz=_TZ, ai_service=fake
    )
    assert isinstance(fu, DailyFollowup)
    assert fu.kind == "milestone"


@pytest.mark.asyncio
async def test_daily_followup_contradiction_wins_over_gap(db_session):
    # 非里程碑日，有矛盾 + 有日常缺口 → 矛盾优先。
    case = await _case(db_session, started_days_ago=3)
    today = date.today()
    # 持有 checkin 引用：SQLAlchemy 身份映射用弱引用，upsert 的矛盾靠原地改共享 list，
    # 不持引用可能被 GC 掉导致矛盾丢失（沿用 test_detect_and_compose_confirmation 的写法）。
    checkin = await _form_checkin_with_weight(db_session, case, today, "100斤")
    await upsert_checkin(
        db_session, service_case=case, checkin_date=today, fields={"weight": "168斤"}
    )
    now = datetime.now(_TZ)
    fu = await compose_daily_followup(db_session, service_case=case, now=now, tz=_TZ)
    assert checkin is not None  # 保持引用存活至断言之后
    assert fu is not None and fu.kind == "contradiction"
    assert fu.confirmation_fields == ["weight"]
    assert "体重" in fu.text


@pytest.mark.asyncio
async def test_daily_followup_merges_gaps_into_one(db_session):
    # 非里程碑日、无矛盾、今天全缺 → 日常缺口，合并成一句（不逐项拆）。
    case = await _case(db_session, started_days_ago=3)
    now = datetime.now(_TZ)
    fake = _FakeAI("今天早C午B晚A和体重都还没看到哈～")
    fu = await compose_daily_followup(
        db_session, service_case=case, now=now, tz=_TZ, ai_service=fake
    )
    assert fu is not None and fu.kind == "daily_gap"
    assert fu.text == fake.text
    # 只调一次 LLM（合并成一句，不按字段多次调）。
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_daily_followup_gap_fallback_on_llm_error(db_session):
    case = await _case(db_session, started_days_ago=3)
    now = datetime.now(_TZ)
    fu = await compose_daily_followup(
        db_session, service_case=case, now=now, tz=_TZ, ai_service=_RaisingAI()
    )
    assert fu is not None and fu.kind == "daily_gap"
    # 兜底模板把所有缺口揉进一句。
    assert "早C" in fu.text and "体重" in fu.text


@pytest.mark.asyncio
async def test_daily_followup_none_when_all_reported(db_session):
    case = await _case(db_session, started_days_ago=3)
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
    now = datetime.now(_TZ)
    fu = await compose_daily_followup(db_session, service_case=case, now=now, tz=_TZ)
    assert fu is None


@pytest.mark.asyncio
async def test_daily_gap_excludes_night_a(db_session):
    # 早C已报、午B/晚A未报 → 日常缺口只追午B(+体重)，晚A 睡前那顿不主动追、已报的早C不追。
    case = await _case(db_session, started_days_ago=3)
    today = date.today()
    checkin = Checkin(
        service_case_id=case.id,
        group_id=case.group_id,
        user_id=case.user_id,
        checkin_date=today,
        content="（部分打卡）",
        parsed_json={"product_schedule": {"morning_c": "已喝"}},
    )
    db_session.add(checkin)
    await db_session.flush()
    now = datetime.now(_TZ)
    fu = await compose_daily_followup(
        db_session, service_case=case, now=now, tz=_TZ, ai_service=_RaisingAI()
    )
    assert fu is not None and fu.kind == "daily_gap"
    assert "午B" in fu.text  # 未报的午B要追
    assert "晚A" not in fu.text  # 睡前那顿不主动追
    assert "早C" not in fu.text  # 已报的不追


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


def test_fallback_line_composition_and_window_gate():
    """不活跃兜底：统一 compose、随机时刻、日常缺口受傍晚窗口闸；不再单独跳过活跃用户(靠天闸去重)。"""
    from app.services.reminder_service import ReminderService

    src = inspect.getsource(ReminderService.check_checkin_followup_once)
    assert "compose_daily_followup" in src  # 统一组织内容
    assert "_user_spoke_today" not in src  # 不再单独跳过活跃用户，靠 checkin_followup 天闸去重
    assert '"daily_gap"' in src  # 日常缺口受傍晚窗口闸（里程碑/矛盾不受约束）
    assert "_deterministic_daily_time" in src  # 随机时刻替代固定 20:30
    assert "checkin_followup_at" not in src  # 固定定点已移除
    assert "reminder_weekdays" not in src  # 工作日限制已移除
    assert "mark_contradictions_pending_confirmation" in src  # 矛盾核对状态机仍在


def test_active_followup_hooked_in_engine():
    """活跃触发：engine 回复成功后调 _maybe_followup_after_reply。"""
    from app.services.ranren_ai.engine import RanrenAIEngine

    src = inspect.getsource(RanrenAIEngine._reply_with_ai)
    assert "_maybe_followup_after_reply" in src
    hook = inspect.getsource(RanrenAIEngine._maybe_followup_after_reply)
    assert "compose_daily_followup" in hook
    assert "_reserve_reminder_event" in hook  # 共用天级去重闸
    assert '"daily_gap"' in hook  # 日常缺口只在傍晚窗口发，早上聊天不顺手补缺口追问


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


@pytest.mark.asyncio
async def test_trigger_staff_message_as_correction_candidate(db_session):
    case = await _case(db_session)
    base = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    staff = Message(
        service_case_id=case.id,
        group_id=case.group_id,
        direction=MessageDirection.INBOUND,
        sender_type=MessageSenderType.STAFF,
        content="这个用户不是三次，是个性化五次。",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
        analysis_json={"_qiwe_meta": {"sender_type": "staff"}},
        created_at=base,
    )
    db_session.add(staff)
    await db_session.flush()

    should, reason = await _evaluate(
        db_session,
        service_case_id=case.id,
        watermark=0,
        last_archived_at=None,
        now=base + timedelta(seconds=KEY_INFO_SETTLE_SECONDS + 1),
    )
    assert should is True
    assert "fast_key" in reason


# ---------------------------------------------------------------------------
# 不活跃兜底辅助：随机时刻
# ---------------------------------------------------------------------------


def test_deterministic_daily_time_stable_and_within_window():
    from datetime import time as dtime

    from app.services.reminder_service import _deterministic_daily_time

    day = date(2026, 7, 10)
    start, end = dtime(9, 0), dtime(21, 0)
    t1 = _deterministic_daily_time(123, day, start, end)
    t2 = _deterministic_daily_time(123, day, start, end)
    assert t1 == t2  # 同 case 同日恒定（轮询不漂移）
    assert start <= t1 < end  # 落在窗口内
    # 不同 case 分散（极大概率不同）。
    others = {_deterministic_daily_time(i, day, start, end) for i in range(20)}
    assert len(others) > 1


def test_deterministic_daily_time_degrades_when_end_le_start():
    from datetime import time as dtime

    from app.services.reminder_service import _deterministic_daily_time

    day = date(2026, 7, 10)
    t = _deterministic_daily_time(1, day, dtime(21, 0), dtime(9, 0))
    assert t == dtime(21, 0)
