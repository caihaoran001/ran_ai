"""阶段报告触发时机回归测试。

阶段报告（day3/7/14/21/28）固定在「服务第 N 天当地 23:00」集中生成，
方便运营当晚集中审核、用户次日早上看到。核心判断抽成纯函数 reminder_service._due_stage_reports，
这里覆盖它的全部分支（不碰 DB/LLM）。
"""
from datetime import date, datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.core.config import get_settings
from app.models.service_case import ServiceCaseStatus
from app.services.reminder_service import _due_stage_reports
from app.services.service_case_queries import service_phase_end_date, service_window_ended

TZ = ZoneInfo(get_settings().reminder_timezone)


def _case(
    case_id=1,
    plan: date | None = None,
    started_at: datetime | None = None,
    service_continued_at: datetime | None = None,
    status: ServiceCaseStatus = ServiceCaseStatus.IN_SERVICE,
):
    # 模拟 ServiceCase：_due_stage_reports 只用到 id / service_plan_start_date / started_at
    return SimpleNamespace(
        id=case_id,
        service_plan_start_date=plan,
        started_at=started_at,
        service_continued_at=service_continued_at,
        status=status,
    )


def _at(y, m, d, hour=23):
    return datetime(y, m, d, hour, 0, 0, tzinfo=TZ)


def test_day3_generated_at_23h_of_third_day():
    # 计划开始日 6/1 = 第1天 → 第3天是 6/3，当晚23点应出 day3（且仅 day3）
    case = _case(plan=date(2026, 6, 1))
    assert _due_stage_reports(case, _at(2026, 6, 3, 23), TZ, set()) == ["day3"]


def test_not_generated_before_23h():
    # 第3天但还没到 23 点（22点）→ 不产出
    case = _case(plan=date(2026, 6, 1))
    assert _due_stage_reports(case, _at(2026, 6, 3, 22), TZ, set()) == []


def test_no_duplicate_when_already_exists():
    # 第3天23点，但 day3 已生成过 → 去重，不重复
    case = _case(plan=date(2026, 6, 1))
    existing = {(1, "day3")}
    assert _due_stage_reports(case, _at(2026, 6, 3, 23), TZ, existing) == []


def test_future_plan_start_not_due():
    # 计划开始日在未来（effective_service_day <= 0）→ 不产出
    case = _case(plan=date(2026, 6, 30))
    assert _due_stage_reports(case, _at(2026, 6, 3, 23), TZ, set()) == []


def test_day28_outputs_only_day28_stage_report():
    # 第28天23点，前面里程碑都已生成 → 只新增 day28 阶段报告
    case = _case(plan=date(2026, 6, 1))
    existing = {(1, "day3"), (1, "day7"), (1, "day14"), (1, "day21")}
    due = _due_stage_reports(case, _at(2026, 6, 28, 23), TZ, existing)
    assert due == ["day28"]


def test_late_activation_backfills_missed_milestones():
    # 补偿性：若一路漏扫，第28天23点会把所有未生成的里程碑补齐（用 >= 而非 ==）
    case = _case(plan=date(2026, 6, 1))
    due = _due_stage_reports(case, _at(2026, 6, 28, 23), TZ, set())
    assert due == ["day3", "day7", "day14", "day21", "day28"]


def test_falls_back_to_started_at_when_no_plan():
    # 存量案例未填计划开始日 → 回退 started_at 当天=第1天。6/1开始 → 6/3 为第3天，23点出 day3
    case = _case(plan=None, started_at=datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc))
    assert _due_stage_reports(case, _at(2026, 6, 3, 23), TZ, set()) == ["day3"]


def test_service_phase_ends_after_day28_not_on_day28():
    case = _case(plan=date(2026, 6, 1))
    assert service_phase_end_date(case, TZ) == date(2026, 6, 28)
    assert service_window_ended(case, _at(2026, 6, 28, 23), TZ) is False
    assert service_window_ended(case, _at(2026, 6, 29, 0), TZ) is True


def test_continued_service_does_not_end_again_or_regenerate_standard_reports():
    continued_at = _at(2026, 7, 5, 10)
    case = _case(plan=date(2026, 6, 1), service_continued_at=continued_at)

    assert service_window_ended(case, _at(2026, 7, 5, 23), TZ) is False
    assert _due_stage_reports(case, _at(2026, 7, 5, 23), TZ, set()) == []
