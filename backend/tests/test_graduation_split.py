"""完成率拆分：毕业（服满 28 天）/ 提前结束（含退款）。

覆盖两层：
- split_terminal_outcomes 纯函数的判据边界（第 28 天恰好、提前一天、退款、
  空结束时间兜底、存量自愈单「离群时间优先于补结的结束时间」）；
- 列表筛选 graduated / ended_early 的 SQL 表达式与纯函数同构，
  以及看板汇总的三个新字段。
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user import User
from app.services.service_case_queries import split_terminal_outcomes

TZ = ZoneInfo("Asia/Shanghai")
PLAN = date(2026, 7, 1)


def _row(
    *,
    status=ServiceCaseStatus.COMPLETED,
    plan=PLAN,
    started=None,
    left=None,
    ended=None,
):
    return SimpleNamespace(
        service_plan_start_date=plan,
        started_at=started,
        member_left_at=left,
        ended_at=ended,
        status=status,
    )


def _utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


class TestSplitTerminalOutcomes:
    def test_ended_on_day_28_is_graduated(self):
        # 第 28 天当天结束（上海时区 7/28）算毕业
        graduated, early, _ = split_terminal_outcomes(
            [_row(ended=_utc(2026, 7, 28, 15, 0))], TZ
        )
        assert len(graduated) == 1 and not early

    def test_ended_before_day_28_is_early_with_day_count(self):
        graduated, early, avg = split_terminal_outcomes(
            [_row(ended=_utc(2026, 7, 20, 10, 0))], TZ
        )
        assert not graduated and len(early) == 1
        assert avg == 20.0  # 7/1 起算，7/20 结束 = 第 20 天

    def test_refunded_always_early_even_after_day_28(self):
        graduated, early, _ = split_terminal_outcomes(
            [_row(status=ServiceCaseStatus.REFUNDED, ended=_utc(2026, 8, 5))], TZ
        )
        assert not graduated and len(early) == 1

    def test_completed_without_any_timestamp_falls_back_to_graduated(self):
        graduated, early, _ = split_terminal_outcomes([_row()], TZ)
        assert len(graduated) == 1 and not early and early == []

    def test_healed_legacy_prefers_member_left_over_late_ended_at(self):
        # 存量自愈单：结束时间是补结时刻（晚于第 28 天），离群时间才是真实离开时刻。
        # 若误用结束时间会被当成毕业——这是本函数最关键的回归防线。
        graduated, early, avg = split_terminal_outcomes(
            [
                _row(
                    left=_utc(2026, 7, 11, 8, 0),
                    ended=_utc(2026, 8, 21, 3, 41),
                )
            ],
            TZ,
        )
        assert not graduated and len(early) == 1
        assert avg == 11.0  # 7/11 离开 = 第 11 天，而不是补结那天

    def test_average_skips_never_started_and_rounds_to_one_decimal(self):
        rows = [
            _row(ended=_utc(2026, 7, 20, 10, 0)),  # 第 20 天
            _row(left=_utc(2026, 7, 11, 8, 0), ended=_utc(2026, 8, 21)),  # 第 11 天
            _row(status=ServiceCaseStatus.REFUNDED),  # 从未开服：不计入均值
        ]
        _, _, avg = split_terminal_outcomes(rows, TZ)
        assert avg == 15.5


async def _seed_case(
    db_session, group_id: str, *, status, plan=None, started=None, left=None, ended=None
) -> ServiceCase:
    user = User(name=f"拆分-{group_id}", wecom_user_id=f"wecom-{group_id}")
    db_session.add(user)
    await db_session.flush()
    case = ServiceCase(
        case_no=f"GS-{group_id}",
        user_id=user.id,
        group_id=group_id,
        questionnaire_token=f"gs-token-{group_id}",
        status=status,
        service_plan_start_date=plan,
        started_at=started,
        member_left_at=left,
        ended_at=ended,
    )
    db_session.add(case)
    await db_session.commit()
    return case


@pytest.mark.asyncio
async def test_graduated_and_ended_early_filters_match_python_split(
    db_session, admin_client
):
    """SQL 筛选与纯函数必须同构：同一批造数，两个接口名单与分类结果一致。"""
    from sqlalchemy import select

    plan = PLAN
    g1 = await _seed_case(
        db_session, "G-SPLIT-G1",
        status=ServiceCaseStatus.COMPLETED, plan=plan, ended=_utc(2026, 7, 28, 15, 0),
    )
    e1 = await _seed_case(
        db_session, "G-SPLIT-E1",
        status=ServiceCaseStatus.COMPLETED, plan=plan, ended=_utc(2026, 7, 20, 10, 0),
    )
    e2 = await _seed_case(
        db_session, "G-SPLIT-E2", status=ServiceCaseStatus.REFUNDED, plan=plan,
    )

    grad_resp = await admin_client.get("/service-cases", params={"filter": "graduated"})
    early_resp = await admin_client.get("/service-cases", params={"filter": "ended_early"})
    assert grad_resp.status_code == 200 and early_resp.status_code == 200

    grad_ids = {item["id"] for item in grad_resp.json()}
    early_ids = {item["id"] for item in early_resp.json()}
    assert g1.id in grad_ids and e1.id not in grad_ids
    assert e1.id in early_ids and e2.id in early_ids and g1.id not in early_ids

    # 与纯函数互证：同批单喂给 split_terminal_outcomes，分桶必须一致
    rows = (
        await db_session.execute(
            select(
                ServiceCase.service_plan_start_date,
                ServiceCase.started_at,
                ServiceCase.member_left_at,
                ServiceCase.ended_at,
                ServiceCase.status,
            ).where(ServiceCase.group_id.like("G-SPLIT-%"))
        )
    ).all()
    graduated, early, _ = split_terminal_outcomes(rows, TZ)
    assert len(graduated) == 1 and len(early) == 2


@pytest.mark.asyncio
async def test_dashboard_summary_exposes_split_fields(db_session, admin_client):
    # 本地库可能残留历史终态单，断言用「造数前后差值」，对既有数据免疫
    before = (await admin_client.get("/dashboard/summary")).json()

    await _seed_case(
        db_session, "G-SPLIT-S1",
        status=ServiceCaseStatus.COMPLETED, plan=PLAN, ended=_utc(2026, 7, 28, 15, 0),
    )
    await _seed_case(
        db_session, "G-SPLIT-S2",
        status=ServiceCaseStatus.COMPLETED, plan=PLAN,
        left=_utc(2026, 7, 11, 8, 0), ended=_utc(2026, 8, 21, 3, 41),
    )

    resp = await admin_client.get("/dashboard/summary")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["service_cases_graduated"] == before["service_cases_graduated"] + 1
    assert payload["service_cases_ended_early"] == before["service_cases_ended_early"] + 1
    assert payload["ended_early_avg_service_day"] is not None
