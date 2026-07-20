"""打卡追问（H5 完整替代）——外部确定性触发、每 case 每天最多一条 + 档案官关键信息快触发。

覆盖：
- 档案官触发器：含关键类目(checkin/profile_fact/outcome)且用户停说≥3min → fast 快触发；
  仅闲聊 worthy 且未到 24h → 不触发。
- compose_scheduled_followups 组织候选：五项清单(次日回顾昨天) / 矛盾核对 / 次晨空腹打卡提醒；
  各类各占时间窗与天闸、每天最多 3 条；LLM 生成提醒话术、失败或时间语义跑偏时回退模板。
- 里程碑档：服务第 6/13/20/27 天检测出 milestone；need_waist/need_weight 按腰围/体重记录决定。
- 矛盾核对：表单值≠聊天值→记矛盾→自然语言核对→标 asked(去重)+字段降级→用户新值覆盖表单。
"""

from __future__ import annotations

import inspect
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.models.checkin import Checkin
from app.models.checkin_followup_cycle import CheckinFollowupCycle
from app.models.checkin_followup_preference import CheckinFollowupPreference
from app.models.message import (
    Message,
    MessageDirection,
    MessagePurpose,
    MessageSenderType,
    MessageStatus,
)
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user import User
from app.models.user_document import DocumentStatus, DocumentType, UserDocument
from app.services.archivist.checkin_projection import (
    SOURCE_FORM,
    SOURCE_USER_CONFIRMED,
    mark_contradictions_pending_confirmation,
    upsert_checkin,
)
from app.services.archivist.followup import (
    compose_followup_message,
    compose_milestone_followup,
    detect_followup,
    compose_document_checklist_followup,
    finalize_checkin_followup_cycles,
    is_followup_paused,
    mark_followup_answers,
    mark_latest_followup_cycle_answered,
    compose_scheduled_followups,
)
from app.services.checkin_followup_preferences import set_followup_preference_states
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
# 里程碑前夜：次晨空腹打卡提醒（LLM + 兜底）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_milestone_followup_uses_injected_llm(db_session):
    case = await _case(db_session, started_days_ago=5)
    fake = _FakeAI("今晚先提醒一下，明早起床如厕后空腹量好腰围，再发群里打卡～")
    msg = await compose_milestone_followup(
        service_case=case, milestone_day=7, need_waist=True, ai_service=fake
    )
    assert msg == fake.text
    assert fake.calls and fake.calls[0]["scenario"] == "checkin_followup"
    assert "明早" in fake.calls[0]["system"]
    assert "如厕后" in fake.calls[0]["system"]
    assert "空腹" in fake.calls[0]["system"]
    assert "今晚" in fake.calls[0]["user"]


@pytest.mark.asyncio
async def test_milestone_followup_falls_back_on_llm_error(db_session):
    case = await _case(db_session, started_days_ago=5)
    msg = await compose_milestone_followup(
        service_case=case, milestone_day=7, need_waist=True, ai_service=_RaisingAI()
    )
    # 回退固定模板：晚间只提醒，测量明确放到次晨如厕后空腹状态。
    assert "腰围" in msg
    assert "明早" in msg and "如厕后" in msg and "空腹" in msg
    assert "今晚不用测" in msg
    assert "复盘" not in msg and "卡点" not in msg


@pytest.mark.asyncio
async def test_milestone_followup_rejects_copy_that_asks_for_evening_measurement(db_session):
    case = await _case(db_session, started_days_ago=5)
    fake = _FakeAI("今晚称一下体重，再说说这阶段最大的变化～")
    msg = await compose_milestone_followup(
        service_case=case,
        milestone_day=7,
        need_waist=True,
        need_weight=True,
        ai_service=fake,
    )
    assert msg != fake.text
    assert "明早" in msg and "如厕后" in msg and "空腹" in msg
    assert "今晚称" not in msg


@pytest.mark.asyncio
async def test_milestone_fallback_skips_waist_when_not_needed(db_session):
    case = await _case(db_session, started_days_ago=5)
    msg = await compose_milestone_followup(
        service_case=case, milestone_day=14, need_waist=False, ai_service=_RaisingAI()
    )
    assert "腰围" not in msg
    assert "明早" in msg and "如厕后" in msg and "空腹" in msg


@pytest.mark.asyncio
async def test_milestone_fallback_includes_weight_when_needed(db_session):
    # 体重不再单独追问，need_weight 时由前夜提醒用户次晨空腹称重。
    case = await _case(db_session, started_days_ago=5)
    msg = await compose_milestone_followup(
        service_case=case,
        milestone_day=7,
        need_waist=True,
        need_weight=True,
        ai_service=_RaisingAI(),
    )
    assert "体重" in msg and "腰围" in msg
    assert "明早" in msg and "今晚不用测" in msg


@pytest.mark.asyncio
async def test_milestone_eve_detects_need_weight(db_session):
    # 第6天里程碑前夕、近3天无体重记录 → need_weight=True，提醒次晨空腹称重。
    case = await _case(db_session, started_days_ago=5)
    now = datetime.now(_TZ)
    detect = await detect_followup(db_session, service_case=case, now=now, tz=_TZ)
    assert detect["milestone"]["need_weight"] is True


# ---------------------------------------------------------------------------
# 退役校验
# ---------------------------------------------------------------------------


def test_check_once_retired_from_run_loop():
    from app.services.reminder_service import ReminderService

    src = inspect.getsource(ReminderService._run_loop)
    assert "self.check_once(" not in src, "check_once 已退役，不应再被 _run_loop 调度"
    assert "check_stage_reports_once" in src  # 阶段报告仍在调度


def test_followup_v2_scheduler_has_cycle_cap_and_silence_brake():
    from app.services.reminder_service import ReminderService

    src = inspect.getsource(ReminderService.check_checkin_followup_v2_once)
    assert "compose_scheduled_followups" in src
    assert "is_followup_paused" in src
    assert "len(sent_kinds) >= 3" in src
    assert "checkin_followup:" in src


async def _sent_execution_document(db_session, case: ServiceCase) -> UserDocument:
    document = UserDocument(
        service_case_id=case.id,
        document_type=DocumentType.DAY28_EXECUTION_STANDARD,
        title="28天执行标准与打卡要求",
        content_md="test",
        content_json={
            "blocks": [
                {"key": "daily_checkin", "items": [
                    "早C/午B/晚A是否全部完成",
                    "今天是否至少吃到2拳蔬菜",
                    "今天是否完成计划内快走",
                    "睡眠是否按计划收口",
                    "请给今天的情绪稳定度和饭后困打分（1-5）",
                ]}
            ]
        },
        status=DocumentStatus.SENT,
        sent_at=datetime.now(timezone.utc),
    )
    db_session.add(document)
    await db_session.flush()
    return document


@pytest.mark.asyncio
async def test_document_checklist_reviews_yesterday_and_only_missing_items(db_session):
    case = await _case(db_session)
    document = await _sent_execution_document(db_session, case)
    yesterday = date.today() - timedelta(days=1)
    checkin = Checkin(
        service_case_id=case.id, group_id=case.group_id, user_id=case.user_id,
        checkin_date=yesterday, content="已答一项",
        parsed_json={
            "product_schedule": {"morning_c": "已喝", "noon_b": "已喝", "night_a": "已喝"},
            "custom_checkin": {f"document:{document.id}:2": {"answer": "完成"}},
        },
    )
    db_session.add(checkin)
    await db_session.flush()
    followup = await compose_document_checklist_followup(
        db_session, service_case=case, target_date=yesterday, ai_service=_RaisingAI()
    )
    assert followup is not None
    assert followup.target_date == yesterday
    assert [item["id"] for item in followup.expected_items] == [
        f"document:{document.id}:3", f"document:{document.id}:4", f"document:{document.id}:5"
    ]
    assert "早C" not in followup.text
    assert "今天" not in followup.text and "今日" not in followup.text
    assert "昨天" in followup.text
    assert "回我一句，我记上，后面好帮你调整～" in followup.text

    fake = _FakeAI("早上好，补一下昨天的运动和睡眠～回我一句，我记上，后面好帮你调整～")
    await compose_document_checklist_followup(
        db_session, service_case=case, target_date=yesterday, ai_service=fake
    )
    assert "助教会记上并据此帮助后续调整" in fake.calls[0]["system"]


@pytest.mark.asyncio
async def test_document_checklist_rebases_today_and_rejects_llm_time_drift(db_session):
    """回归 RAI-20260622-502EB2：旧题面含“今天”时不得混入次日追问。"""
    case = await _case(db_session)
    await _sent_execution_document(db_session, case)
    yesterday = date.today() - timedelta(days=1)
    bad_copy = (
        "昨天晚A吃了吗？午餐蔬菜够2拳不？"
        "还有今天的情绪稳定度和饭后困按1-5打分～"
    )
    fake = _FakeAI(bad_copy)

    followup = await compose_document_checklist_followup(
        db_session,
        service_case=case,
        target_date=yesterday,
        reference_date=date.today(),
        ai_service=fake,
    )

    assert followup is not None
    assert followup.text != bad_copy
    assert "昨天" in followup.text
    assert "今天" not in followup.text and "今日" not in followup.text
    # 模型看到的题面已换算，而审计/答案匹配仍保留原题。
    assert "今天" not in fake.calls[0]["user"]
    assert "昨天的情绪稳定度和饭后困" in fake.calls[0]["user"]
    assert any("请给今天的情绪稳定度" in item["label"] for item in followup.expected_items)


@pytest.mark.asyncio
async def test_document_checklist_first_item_only_asks_missing_products(db_session):
    """第1项三合一：只报了早C时，追问只列午B/晚A，不再把已报的早C拖着重问。"""
    case = await _case(db_session)
    document = await _sent_execution_document(db_session, case)
    yesterday = date.today() - timedelta(days=1)
    checkin = Checkin(
        service_case_id=case.id, group_id=case.group_id, user_id=case.user_id,
        checkin_date=yesterday, content="只报了早C",
        parsed_json={
            "product_schedule": {"morning_c": "已喝"},  # 仅早C，午B/晚A 未报
            "custom_checkin": {
                f"document:{document.id}:{i}": {"answer": "完成"} for i in (2, 3, 4, 5)
            },
        },
    )
    db_session.add(checkin)
    await db_session.flush()
    followup = await compose_document_checklist_followup(
        db_session, service_case=case, target_date=yesterday, ai_service=_RaisingAI()
    )
    assert followup is not None
    # 只剩第1项，且 label 只含午B/晚A、不含早C
    assert [item["id"] for item in followup.expected_items] == [f"document:{document.id}:1"]
    assert followup.expected_items[0]["label"] == "午B、晚A"
    assert "早C" not in followup.text
    assert "午B" in followup.text and "晚A" in followup.text


@pytest.mark.asyncio
async def test_two_unanswered_cycles_pause_and_valid_answer_reopens(db_session):
    # The shared developer test database may lag Alembic head; CI applies migrations.
    connection = await db_session.connection()
    await connection.run_sync(
        lambda sync_connection: CheckinFollowupCycle.__table__.create(sync_connection, checkfirst=True)
    )
    case = await _case(db_session)
    today = date.today()
    for offset in (2, 1):
        db_session.add(CheckinFollowupCycle(service_case_id=case.id, cycle_date=today - timedelta(days=offset), status="open"))
    await db_session.flush()
    await finalize_checkin_followup_cycles(db_session, today=today)
    assert await is_followup_paused(db_session, service_case_id=case.id) is True
    await mark_followup_answers(
        db_session, service_case_id=case.id,
        cycle_dates_and_items=[(today - timedelta(days=1), "weight")],
    )
    assert await is_followup_paused(db_session, service_case_id=case.id) is False


@pytest.mark.asyncio
async def test_normal_chat_checkin_reopens_latest_paused_cycle(db_session):
    connection = await db_session.connection()
    await connection.run_sync(
        lambda sync_connection: CheckinFollowupCycle.__table__.create(sync_connection, checkfirst=True)
    )
    case = await _case(db_session)
    today = date.today()
    for offset in (2, 1):
        db_session.add(
            CheckinFollowupCycle(
                service_case_id=case.id,
                cycle_date=today - timedelta(days=offset),
                status="unanswered",
            )
        )
    await db_session.flush()
    assert await is_followup_paused(db_session, service_case_id=case.id) is True

    await mark_latest_followup_cycle_answered(
        db_session,
        service_case_id=case.id,
        item_ids=["document:99:2"],
    )

    assert await is_followup_paused(db_session, service_case_id=case.id) is False
    latest = await db_session.scalar(
        select(CheckinFollowupCycle)
        .where(CheckinFollowupCycle.service_case_id == case.id)
        .order_by(CheckinFollowupCycle.cycle_date.desc())
    )
    assert latest.status == "answered"
    assert latest.answered_item_ids == ["document:99:2"]


@pytest.mark.asyncio
async def test_followup_preferences_filter_proactive_topics(db_session):
    connection = await db_session.connection()
    await connection.run_sync(
        lambda sync_connection: CheckinFollowupPreference.__table__.create(
            sync_connection, checkfirst=True
        )
    )
    now = datetime.now(_TZ)
    case = await _case(db_session, started_days_ago=5)  # service day 6 → milestone day 7

    await set_followup_preference_states(
        db_session,
        service_case_id=case.id,
        topics=["daily_checklist", "weight", "waist", "milestone_review", "contradiction"],
        enabled=False,
        source_message_id=None,
    )
    assert await compose_scheduled_followups(
        db_session, service_case=case, now=now, tz=_TZ, ai_service=_RaisingAI()
    ) == []

    await set_followup_preference_states(
        db_session,
        service_case_id=case.id,
        topics=["milestone_review"],
        enabled=True,
        source_message_id=None,
    )
    candidates = await compose_scheduled_followups(
        db_session, service_case=case, now=now, tz=_TZ, ai_service=_RaisingAI()
    )
    assert [item.kind for item in candidates] == ["milestone"]
    assert "腰围" not in candidates[0].text
    assert candidates[0].target_date == now.date() + timedelta(days=1)
    assert candidates[0].expected_items == [
        {"id": "milestone_morning_checkin", "label": "次晨空腹打卡"}
    ]


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
async def test_trigger_accepts_terse_reply_after_structured_followup(db_session):
    case = await _case(db_session)
    base = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    db_session.add(
        Message(
            service_case_id=case.id,
            group_id=case.group_id,
            direction=MessageDirection.OUTBOUND,
            sender_type=MessageSenderType.SYSTEM,
            content="补一下昨天的打卡",
            purpose=MessagePurpose.REMINDER,
            status=MessageStatus.SENT,
            analysis_json={"checkin_followup": {"items": []}},
            created_at=base,
        )
    )
    await db_session.flush()
    await _user_msg(
        db_session, case,
        analysis_json={"archive_plan": {"worthy": False, "kinds": []}},
        created_at=base + timedelta(minutes=1),
    )
    should, reason = await _evaluate(
        db_session, service_case_id=case.id, watermark=0, last_archived_at=None,
        now=base + timedelta(minutes=1, seconds=KEY_INFO_SETTLE_SECONDS + 10),
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
