"""体重/腰围分窗口统计：验证四个窗口的取数口径和脏数据不再污染结果。

背景：线上看板原来只有一个「28天平均变化」，起点取"这个人第一次称的体重"，
脏数据也没清洗，结果 47 个体重样本里 5 个是单位混填造成的 ±70 公斤，
8 个腰围样本里两个脏值把均值推成 +9.1cm（方向还是"变大"）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from app.models.checkin import Checkin
from app.models.questionnaire import QuestionnaireSubmission
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user import User
from app.services.body_change_stats import (
    EXCLUDE_IMPLAUSIBLE,
    EXCLUDE_NO_ENDPOINT,
    INCLUDED,
    compute_case_window_changes,
)
from app.services.questionnaire_template import get_active_questionnaire_fields


async def _case_with_checkins(
    db_session,
    *,
    checkins: dict[int, dict[str, str]],
    service_days: int = 28,
    intake_weight: str | None = None,
    intake_waist: str | None = None,
) -> ServiceCase:
    """建一个服务单，``checkins`` 是 {服务第几天: {"weight": ..., "waist": ...}}。"""

    user = User(name="体征统计用户")
    db_session.add(user)
    await db_session.flush()
    now = datetime.now(timezone.utc)
    case = ServiceCase(
        case_no=f"TEST-BODY-{uuid4().hex[:6].upper()}",
        user_id=user.id,
        group_id=f"body-{uuid4().hex[:8]}",
        questionnaire_token=uuid4().hex,
        status=ServiceCaseStatus.IN_SERVICE,
        started_at=now - timedelta(days=service_days - 1),
    )
    db_session.add(case)
    await db_session.flush()

    if intake_weight or intake_waist:
        fields = await get_active_questionnaire_fields(db_session, "intake")
        answers: dict[str, str] = {}
        for field in fields:
            title = str(field.get("title") or "")
            key = str(field.get("key") or field.get("id") or "")
            if intake_weight and "当前体重" in title:
                answers[key] = intake_weight
            elif intake_waist and "腰围" in title and "体脂" not in title:
                answers[key] = intake_waist
        db_session.add(
            QuestionnaireSubmission(
                service_case_id=case.id,
                template_key="intake",
                answers_json=answers,
            )
        )

    # 服务天数按北京日期算（生产口径），测试里的"第几天"必须用同一时区推日期，
    # 否则 UTC 16:00 之后建的用例会整体偏一天。
    first_day = case.started_at.astimezone(ZoneInfo("Asia/Shanghai")).date()
    for day, payload in checkins.items():
        db_session.add(
            Checkin(
                service_case_id=case.id,
                group_id=case.group_id,
                checkin_date=first_day + timedelta(days=day - 1),
                content=f"第{day}天打卡",
                parsed_json=payload,
            )
        )
    await db_session.flush()
    return case


async def _changes_for(db_session, case_id: int) -> dict[int, object]:
    changes, _ = await compute_case_window_changes(db_session, now=datetime.now(timezone.utc))
    return {item.window_day: item for item in changes if item.service_case_id == case_id}


@pytest.mark.asyncio
async def test_each_window_takes_its_own_endpoint(db_session):
    """3/7/14/28 天各取自己窗口末的记录，起点统一是入营问卷值。"""

    case = await _case_with_checkins(
        db_session,
        intake_weight="70",
        intake_waist="90",
        checkins={
            1: {"weight": "70", "waist": "90"},
            3: {"weight": "69.5", "waist": "89"},
            7: {"weight": "69", "waist": "88"},
            14: {"weight": "68", "waist": "86"},
            28: {"weight": "66", "waist": "83"},
        },
    )
    windows = await _changes_for(db_session, case.id)

    assert windows[3].weight.delta == -0.5
    assert windows[7].weight.delta == -1.0
    assert windows[14].weight.delta == -2.0
    assert windows[28].weight.delta == -4.0
    assert windows[28].waist.delta == -7.0
    assert windows[28].weight.baseline_source == "questionnaire"


@pytest.mark.asyncio
async def test_late_first_weighing_does_not_shrink_the_window(db_session):
    """第 10 天才第一次称的人，起点仍是入营问卷值，不是"他第一次称的那天"。

    原算法拿首次打卡当起点，这个人的"28天变化"实际只覆盖 18 天，和别人不可比。
    """

    case = await _case_with_checkins(
        db_session,
        intake_weight="80",
        checkins={10: {"weight": "78"}, 28: {"weight": "76"}},
    )
    windows = await _changes_for(db_session, case.id)

    assert windows[28].weight.delta == -4.0  # 80 → 76，而不是 78 → 76
    assert windows[28].weight.baseline_source == "questionnaire"


@pytest.mark.asyncio
async def test_jin_entries_are_rescued_by_the_questionnaire_baseline(db_session):
    """线上 case 29 全程填斤（151.6/150.8）。符号对、幅度错一倍，只有基线能识别。"""

    case = await _case_with_checkins(
        db_session,
        intake_weight="75.8",
        checkins={1: {"weight": "151.6"}, 28: {"weight": "148"}},
    )
    windows = await _changes_for(db_session, case.id)

    assert windows[28].weight.delta == -1.8  # 75.8 → 74.0，不是 -3.6，也不是被整条丢掉
    assert windows[28].weight.status == INCLUDED


@pytest.mark.asyncio
async def test_mixed_units_do_not_produce_a_seventy_kilo_drop(db_session):
    """线上 case 25：['140.2斤','70.7kg']。原算法算成 28 天掉了 70 公斤。"""

    case = await _case_with_checkins(
        db_session,
        intake_weight="70.1",
        checkins={1: {"weight": "140.2斤"}, 28: {"weight": "70.7kg"}},
    )
    windows = await _changes_for(db_session, case.id)

    assert windows[28].weight.delta == 0.6  # 70.1 → 70.7，如实反映微涨


@pytest.mark.asyncio
async def test_zero_waist_is_not_a_measurement(db_session):
    """线上有人连填 12 天腰围 0，被当真实围度后算出「从 0 长到 68 厘米」。"""

    case = await _case_with_checkins(
        db_session,
        intake_waist="90",
        checkins={1: {"waist": "90"}, 27: {"waist": "0"}, 28: {"waist": "0"}},
    )
    windows = await _changes_for(db_session, case.id)

    assert windows[28].waist.delta is None
    assert windows[28].waist.status == EXCLUDE_NO_ENDPOINT


@pytest.mark.asyncio
async def test_chi_written_waist_is_converted(db_session):
    """22.5（2尺2.5）= 2.25 尺 = 75 厘米，不是 22.5 厘米。"""

    case = await _case_with_checkins(
        db_session,
        intake_waist="80",
        checkins={1: {"waist": "80"}, 28: {"waist": "22.5（2尺2.5）"}},
    )
    windows = await _changes_for(db_session, case.id)

    assert windows[28].waist.delta == pytest.approx(-5.0, abs=0.1)


@pytest.mark.asyncio
async def test_impossible_delta_is_excluded_not_averaged_in(db_session):
    """基线和终点各自都像正常数字、但差值不可能时，剔除而不是让它进均值。"""

    case = await _case_with_checkins(
        db_session,
        checkins={1: {"weight": "100"}, 28: {"weight": "60"}},
    )
    windows = await _changes_for(db_session, case.id)

    assert windows[28].weight.delta is None
    assert windows[28].weight.status == EXCLUDE_IMPLAUSIBLE


@pytest.mark.asyncio
async def test_case_not_yet_at_the_window_is_not_counted(db_session):
    """第 10 天的用户不出现在 14/28 天窗口里——"还没到时间"不等于"没效果"。"""

    case = await _case_with_checkins(
        db_session,
        service_days=10,
        intake_weight="70",
        checkins={1: {"weight": "70"}, 7: {"weight": "69"}},
    )
    windows = await _changes_for(db_session, case.id)

    assert set(windows) == {3, 7}
    assert windows[7].weight.delta == -1.0


@pytest.mark.asyncio
async def test_checkin_rate_reflects_days_inside_the_window(db_session):
    case = await _case_with_checkins(
        db_session,
        intake_weight="70",
        checkins={day: {"weight": "70"} for day in range(1, 22)},
    )
    windows = await _changes_for(db_session, case.id)

    assert windows[7].checkin_days == 7
    assert windows[7].checkin_rate == 1.0
    assert windows[28].checkin_days == 21
    assert windows[28].checkin_rate == pytest.approx(0.75, abs=0.001)


@pytest.mark.asyncio
async def test_endpoint_uses_reading_closest_to_the_window_end(db_session):
    """窗口末容差 ±2 天：不是所有人都恰好在第 28 天称重，取最接近的一次。"""

    case = await _case_with_checkins(
        db_session,
        intake_weight="70",
        checkins={1: {"weight": "70"}, 26: {"weight": "68"}, 27: {"weight": "67.5"}},
    )
    windows = await _changes_for(db_session, case.id)

    assert windows[28].weight.delta == -2.5  # 取第 27 天，不是第 26 天


@pytest.mark.asyncio
async def test_endpoint_reports_samples_and_exclusion_reasons(db_session, admin_client):
    """详情页要能如实说明样本量和剔除原因，不让样本悄悄变小。"""

    await _case_with_checkins(
        db_session,
        intake_weight="70",
        intake_waist="90",
        checkins={1: {"weight": "70", "waist": "90"}, 28: {"weight": "68", "waist": "87"}},
    )
    await _case_with_checkins(
        db_session,
        intake_weight="80",
        checkins={1: {"weight": "80"}},  # 窗口末没有记录
    )

    response = await admin_client.get("/dashboard/body-change")
    assert response.status_code == 200
    payload = response.json()

    assert [item["window_day"] for item in payload["windows"]] == [3, 7, 14, 28]
    day28 = next(item for item in payload["windows"] if item["window_day"] == 28)
    assert day28["weight"]["stat"]["sample_count"] >= 1
    assert day28["weight"]["eligible_cases"] >= 2
    assert day28["weight"]["excluded"].get(EXCLUDE_NO_ENDPOINT, 0) >= 1
    # 分层维度按客户要求给年龄段、减重带、客户类型
    assert [item["key"] for item in day28["weight"]["segments"]] == [
        "age",
        "weight_loss_band",
        "customer_type",
    ]


@pytest.mark.asyncio
async def test_checkin_rate_threshold_filters_low_adherence(db_session, admin_client):
    """打卡率门槛（客户提的 60%）把只打过两天卡的人移出样本，并记为一条剔除原因。"""

    await _case_with_checkins(
        db_session,
        intake_weight="70",
        checkins={1: {"weight": "70"}, 28: {"weight": "68"}},
    )

    loose = (await admin_client.get("/dashboard/body-change")).json()
    strict = (await admin_client.get("/dashboard/body-change?min_checkin_rate=0.6")).json()

    loose28 = next(item for item in loose["windows"] if item["window_day"] == 28)
    strict28 = next(item for item in strict["windows"] if item["window_day"] == 28)
    assert loose28["weight"]["stat"]["sample_count"] >= 1
    assert strict28["weight"]["stat"]["sample_count"] == 0
    assert strict28["weight"]["excluded"].get("below_checkin_rate", 0) >= 1
    # 分母不变，方便看出门槛滤掉了多少人
    assert strict28["weight"]["eligible_cases"] == loose28["weight"]["eligible_cases"]
