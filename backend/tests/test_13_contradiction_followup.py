"""打卡矛盾走"追问用户确认"而非丢给运营（不发链接）。

验证闭环：表单值≠聊天值→记矛盾→晚间自然语言追问→标 asked(去重)+字段降级→
用户随后报的新值覆盖原表单值（用户确认覆盖表单）。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

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
from app.services.archivist.followup import compose_followup_message, detect_followup


async def _case(db_session):
    user = User(name="矛盾追问测试")
    db_session.add(user)
    await db_session.flush()
    case = ServiceCase(
        case_no=f"TEST-CONF-{uuid4().hex[:6].upper()}",
        user_id=user.id,
        group_id=f"conf-{uuid4().hex[:8]}",
        questionnaire_token=uuid4().hex,
        status=ServiceCaseStatus.IN_SERVICE,
        started_at=datetime.now(timezone.utc) - timedelta(days=3),
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

    detect = await detect_followup(db_session, service_case_id=case.id, today=today)
    assert len(detect["confirmations"]) == 1
    assert detect["confirmations"][0]["label"] == "体重"

    msg = compose_followup_message(
        missing_slots=[], ask_weight=False, confirmations=detect["confirmations"]
    )
    assert msg and "体重" in msg and "更新" in msg

    # 标记已问后：不再重复追问（去重）。
    mark_contradictions_pending_confirmation(checkin, ["weight"])
    await db_session.flush()
    detect2 = await detect_followup(db_session, service_case_id=case.id, today=today)
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
