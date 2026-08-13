"""看板『未跟进 / 流失』口径：只按用户本人的发言算沉默。

班主任在群里说的话同样以 INBOUND 落库，若把它算成"这个群还活着"，
班主任越主动跟进、流失数反而越低，名单会漏掉真正不吭声的用户。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.models.message import (
    Message,
    MessageDirection,
    MessagePurpose,
    MessageSenderType,
    MessageStatus,
)
from app.models.reminder import (
    CHURN_RECALL_RULE_ID,
    ReminderEvent,
    ReminderEventType,
)
from app.models.service_case import ServiceCase, ServiceCaseGroupType, ServiceCaseStatus
from app.models.user import User


def _inbound(case: ServiceCase, sender_type: MessageSenderType, created_at: datetime) -> Message:
    return Message(
        service_case_id=case.id,
        group_id=case.group_id,
        direction=MessageDirection.INBOUND,
        sender_type=sender_type,
        message_type="text",
        content="占位消息",
        purpose=MessagePurpose.CHAT,
        status=MessageStatus.RECEIVED,
        created_at=created_at,
    )


async def _idle_case(
    db_session,
    *,
    name: str,
    user_days_ago: float,
    staff_days_ago: float | None = None,
) -> ServiceCase:
    now = datetime.now(timezone.utc)
    user = User(name=name)
    db_session.add(user)
    await db_session.flush()
    case = ServiceCase(
        case_no=f"TEST-IDLE-{uuid4().hex[:6].upper()}",
        user_id=user.id,
        group_id=f"idle-{uuid4().hex[:8]}",
        questionnaire_token=uuid4().hex,
        status=ServiceCaseStatus.IN_SERVICE,
        started_at=now - timedelta(days=20),
        created_at=now - timedelta(days=20),
        group_type=ServiceCaseGroupType.NEW_GROUP.value,
    )
    db_session.add(case)
    await db_session.flush()
    db_session.add(_inbound(case, MessageSenderType.USER, now - timedelta(days=user_days_ago)))
    if staff_days_ago is not None:
        db_session.add(_inbound(case, MessageSenderType.STAFF, now - timedelta(days=staff_days_ago)))
    await db_session.flush()
    return case


async def _recall_event(db_session, case: ServiceCase, *, days_ago: float) -> None:
    """伪造一条系统召回台账。"""
    stamp = datetime.now(timezone.utc) - timedelta(days=days_ago)
    db_session.add(
        ReminderEvent(
            service_case_id=case.id,
            rule_id=CHURN_RECALL_RULE_ID,
            event_date=stamp.date(),
            event_type=ReminderEventType.REMINDED,
            created_at=stamp,
        )
    )
    await db_session.flush()


@pytest.mark.asyncio
async def test_lost_cases_count_only_user_silence(db_session, admin_client, mock_ai):
    silent = await _idle_case(db_session, name="沉默用户", user_days_ago=10)
    staff_followed = await _idle_case(
        db_session, name="班主任跟进过的沉默用户", user_days_ago=10, staff_days_ago=1
    )
    talking = await _idle_case(db_session, name="仍在说话的用户", user_days_ago=1)

    response = await admin_client.get("/dashboard/lost-cases")
    assert response.status_code == 200
    by_case = {item["case"]["service_case_id"]: item for item in response.json()}

    assert silent.id in by_case
    # 关键断言：班主任昨天在群里说过话，也不能把用户从流失名单里带走。
    assert staff_followed.id in by_case
    assert by_case[staff_followed.id]["idle_days"] == 10
    assert talking.id not in by_case


@pytest.mark.asyncio
async def test_unfollowed_cases_ignore_staff_messages(db_session, admin_client, mock_ai):
    # 用户沉默 4 天（属于未跟进区间），班主任昨天说过话。
    staff_followed = await _idle_case(
        db_session, name="未跟进但班主任说过话", user_days_ago=4, staff_days_ago=1
    )

    response = await admin_client.get("/dashboard/unfollowed-cases")
    assert response.status_code == 200
    by_case = {item["case"]["service_case_id"]: item for item in response.json()}

    assert staff_followed.id in by_case
    assert by_case[staff_followed.id]["idle_days"] == 4


@pytest.mark.asyncio
async def test_user_list_lost_filter_matches_dashboard(db_session, admin_client, mock_ai):
    staff_followed = await _idle_case(
        db_session, name="列表口径对齐用户", user_days_ago=9, staff_days_ago=1
    )
    talking = await _idle_case(db_session, name="列表口径-仍在说话", user_days_ago=1)

    response = await admin_client.get("/service-cases", params={"filter": "lost"})
    assert response.status_code == 200
    case_ids = {item["id"] for item in response.json()}

    assert staff_followed.id in case_ids
    # 顺带确认 filter 真的生效了（否则全量返回会让上一条断言假通过）。
    assert talking.id not in case_ids


@pytest.mark.asyncio
async def test_active_24h_filter_counts_only_user_messages(db_session, admin_client, mock_ai):
    """活跃用户视图同理：班主任刚在群里说过话，不代表用户今天在聊。"""
    talking = await _idle_case(db_session, name="今天在聊的用户", user_days_ago=0.2)
    staff_only = await _idle_case(
        db_session, name="只有班主任说话的群", user_days_ago=9, staff_days_ago=0.2
    )

    response = await admin_client.get("/service-cases", params={"filter": "active_24h"})
    assert response.status_code == 200
    case_ids = {item["id"] for item in response.json()}

    assert talking.id in case_ids
    assert staff_only.id not in case_ids


@pytest.mark.asyncio
async def test_lost_list_shows_recalls_of_this_silence_only(
    db_session, admin_client, mock_ai
):
    """名单上的「已召回 N 次」必须与调度器同一口径：只数最后一次用户发言之后的召回。

    口径一旦两边漂移，运营看到的次数就和真实发出去的不符，而且不会报错——
    所以这里把「本轮算数」和「用户回话后归零」两种情况都钉住。
    """
    recalled = await _idle_case(db_session, name="本轮已召回过", user_days_ago=10)
    await _recall_event(db_session, recalled, days_ago=8)

    replied_since = await _idle_case(db_session, name="召回后回过话又沉默", user_days_ago=9)
    await _recall_event(db_session, replied_since, days_ago=15)  # 早于最后发言，不算本轮

    never = await _idle_case(db_session, name="还没被召回过", user_days_ago=10)

    response = await admin_client.get("/dashboard/lost-cases")
    assert response.status_code == 200
    by_case = {item["case"]["service_case_id"]: item for item in response.json()}

    assert by_case[recalled.id]["recall_count"] == 1
    assert by_case[recalled.id]["last_recall_at"] is not None
    assert by_case[replied_since.id]["recall_count"] == 0
    assert by_case[replied_since.id]["last_recall_at"] is None
    assert by_case[never.id]["recall_count"] == 0
