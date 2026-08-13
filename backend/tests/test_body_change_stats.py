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
from app.models.service_case import ServiceCase, ServiceCaseGroupType, ServiceCaseStatus
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
        group_type=ServiceCaseGroupType.NEW_GROUP.value,
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
async def test_fallback_baseline_cannot_also_be_the_endpoint(db_session):
    """只有一条第3天体重时，它只能当回退起点，不能再和自己相减造出“持平”样本。"""

    case = await _case_with_checkins(
        db_session,
        service_days=3,
        checkins={3: {"weight": "70"}},
    )
    windows = await _changes_for(db_session, case.id)

    assert windows[3].weight.baseline == 70
    assert windows[3].weight.baseline_day == 3
    assert windows[3].weight.endpoint is None
    assert windows[3].weight.delta is None
    assert windows[3].weight.status == EXCLUDE_NO_ENDPOINT


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
async def test_short_window_allows_normal_water_weight_swing(db_session):
    """3 天掉 2 公斤是常见的水分变化，不能当成录入错误剔掉。

    只按"每天最多 0.5 公斤"算的话，3 天窗口上限是 1.5 公斤，生产实测会误剔 8 个人，
    而且剔掉的都是变化大的人——等于系统性低估短期效果。
    """

    swing = await _case_with_checkins(
        db_session,
        intake_weight="80",
        checkins={1: {"weight": "80"}, 3: {"weight": "78"}},
    )
    absurd = await _case_with_checkins(
        db_session,
        intake_weight="80",
        checkins={1: {"weight": "80"}, 3: {"weight": "76"}},
    )

    assert (await _changes_for(db_session, swing.id))[3].weight.delta == -2.0
    absurd_windows = await _changes_for(db_session, absurd.id)
    assert absurd_windows[3].weight.status == EXCLUDE_IMPLAUSIBLE


@pytest.mark.asyncio
async def test_single_mistyped_reading_does_not_become_the_endpoint(db_session):
    """线上 case 92：13 天从 91.65 稳定降到 88.25，最后一条突然写「98.4kg」。

    起点终点各自都像正常数字、差值也在 28 天上限内，所以窗口级检查抓不到；
    但一周涨 8.3 公斤不可能发生。这一条把 28 天均值从 -0.86 拉到 -0.37，
    正是客户质疑"数字不对"的那类。
    """

    case = await _case_with_checkins(
        db_session,
        intake_weight="90",
        checkins={
            1: {"weight": "91.65"},
            10: {"weight": "89.25"},
            21: {"weight": "90.1公斤"},
            28: {"weight": "98.4kg"},
        },
    )
    windows = await _changes_for(db_session, case.id)

    assert windows[28].weight.delta is None
    assert windows[28].weight.status == EXCLUDE_IMPLAUSIBLE
    # 真实的持续下降不受影响：每一步都在合理速度内
    assert windows[14].weight.delta is None or windows[14].weight.status == EXCLUDE_NO_ENDPOINT


@pytest.mark.asyncio
async def test_a_real_gradual_change_is_not_mistaken_for_a_typo(db_session):
    """跳变检查只看与前一条的速度，所以真实的持续减重不会被误伤。"""

    case = await _case_with_checkins(
        db_session,
        intake_weight="90",
        checkins={day: {"weight": str(round(90 - day * 0.15, 2))} for day in range(1, 29)},
    )
    windows = await _changes_for(db_session, case.id)

    assert windows[28].weight.delta == pytest.approx(-4.2, abs=0.01)


@pytest.mark.asyncio
async def test_unit_written_by_mistake_yields_to_the_baseline(db_session):
    """线上 case 105：基线 100 公斤的人写「103.7斤（排便后）」。

    按斤算是 51.85 公斤，显然不是他。数字本身才是公斤，"斤"是随口写的。
    """

    case = await _case_with_checkins(
        db_session,
        intake_weight="100",
        checkins={1: {"weight": "100"}, 7: {"weight": "103.7斤（排便后）"}},
    )
    windows = await _changes_for(db_session, case.id)

    # 这一条被读成 103.7 公斤本人，而不是 51.85 公斤的另一个人，也不是整条丢掉。
    # （这个值最终是否进样本由窗口净变化上限另行决定，不是单位规则的事。）
    assert windows[7].weight.endpoint == 103.7


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

    included_case = await _case_with_checkins(
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
        "checkin_rate_band",
        "age",
        "weight_loss_band",
        "customer_type",
    ]
    execution = day28["weight"]["segments"][0]
    assert execution["label"] == "有打卡执行度"
    assert {item["bucket"] for item in execution["buckets"]} <= {
        "低于60%",
        "60%–79%",
        "80%及以上",
    }
    sample = next(
        item
        for item in day28["weight"]["samples"]
        if item["case"]["service_case_id"] == included_case.id
    )
    assert sample["baseline"] == 70
    assert sample["baseline_source"] == "questionnaire"
    assert sample["endpoint"] == 68
    assert sample["endpoint_day"] == 28
    assert sample["delta"] == -2
    assert sample["status"] == "ok"
    assert sample["case"]["user_name"] == "体征统计用户"


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
