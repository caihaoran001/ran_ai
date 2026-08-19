"""看板「今日新增用户」的口径：今天交了入营问卷才算一个新客户。

不能按「今天建了单」数：建单只代表群里有人说了话。一个群可以先后服务多位客户，
班主任在空出来的老群里说一句「这个群先留着」就会自动开一张新单，那时候一个新客户
都还没进来——按建单数就会天天虚增。
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.models.questionnaire import QuestionnaireSubmission
from app.models.service_case import ServiceCase, ServiceCaseGroupType, ServiceCaseStatus
from app.models.user import User


def _today_start() -> datetime:
    china_tz = ZoneInfo("Asia/Shanghai")
    today = datetime.now(china_tz).date()
    return datetime.combine(today, time.min, tzinfo=china_tz).astimezone(timezone.utc)


async def _case(db_session, *, name: str, created_at: datetime | None = None) -> ServiceCase:
    user = User(name=name)
    db_session.add(user)
    await db_session.flush()
    case = ServiceCase(
        case_no=f"TEST-NEW-{uuid4().hex[:6].upper()}",
        user_id=user.id,
        group_id=f"newmetric-{uuid4().hex[:8]}",
        questionnaire_token=uuid4().hex,
        status=ServiceCaseStatus.CREATED,
        group_type=ServiceCaseGroupType.NEW_GROUP.value,
    )
    if created_at is not None:
        case.created_at = created_at
    db_session.add(case)
    await db_session.flush()
    return case


async def _submit_intake(db_session, case: ServiceCase, at: datetime) -> None:
    submission = QuestionnaireSubmission(
        service_case_id=case.id,
        template_key="intake",
        answers_json={"name": "测试"},
    )
    submission.created_at = at
    db_session.add(submission)
    await db_session.flush()


async def test_case_without_questionnaire_is_not_a_new_user(admin_client, db_session):
    """班主任在空群里说句话就建的单：不算新客户，卡片和点开的名单都不该出现它。"""
    before = (await admin_client.get("/dashboard/summary")).json()["new_users_today"]
    before_list = (await admin_client.get("/service-cases?filter=new_today")).json()

    await _case(db_session, name="只有员工说过话的空群")
    await db_session.commit()

    after = (await admin_client.get("/dashboard/summary")).json()["new_users_today"]
    after_list = (await admin_client.get("/service-cases?filter=new_today")).json()
    assert after == before, "没有入营问卷就不是新客户"
    assert len(after_list) == len(before_list), "卡片数字和点开的名单必须是同一批人"


async def test_intake_submitted_today_counts_even_if_the_group_was_prepared_earlier(
    admin_client, db_session
):
    """班主任昨天先把群铺好、客户今天才填问卷：算今天的新客户（按问卷时间，不按建单时间）。"""
    before = (await admin_client.get("/dashboard/summary")).json()["new_users_today"]

    case = await _case(db_session, name="今天入营", created_at=_today_start() - timedelta(days=1))
    await _submit_intake(db_session, case, _today_start() + timedelta(hours=9))
    await db_session.commit()

    summary = (await admin_client.get("/dashboard/summary")).json()
    assert summary["new_users_today"] == before + 1
    listed = (await admin_client.get("/service-cases?filter=new_today")).json()
    assert case.id in [row["id"] for row in listed]


async def test_yesterday_intake_does_not_count_today(admin_client, db_session):
    """昨天入营的客户不能天天重复计入今天。"""
    before = (await admin_client.get("/dashboard/summary")).json()["new_users_today"]

    case = await _case(db_session, name="昨天入营")
    await _submit_intake(db_session, case, _today_start() - timedelta(hours=3))
    await db_session.commit()

    after = (await admin_client.get("/dashboard/summary")).json()["new_users_today"]
    assert after == before
