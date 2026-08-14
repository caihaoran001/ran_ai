"""平台期检测：验证 7 天窗口统计主判据、用户自述旁证与数据不足不误报。

客户口径：平台期 = 一星期体重没动静，或用户主动说类似「体重卡住了」。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.models.checkin import Checkin
from app.models.outcome import OutcomeEvent
from app.models.service_case import ServiceCase, ServiceCaseGroupType, ServiceCaseStatus
from app.models.user import User
from app.services.plateau_detector import (
    MIN_VALID_WEIGHT_ROWS,
    PLATEAU_MAX_DELTA_KG,
    STATUS_INSUFFICIENT_DATA,
    STATUS_NORMAL,
    STATUS_PLATEAU,
    _evaluate_plateau,
    scan_plateaus,
)

TZ = ZoneInfo("Asia/Shanghai")


def _checkin(day: date, weight: str) -> Checkin:
    return Checkin(
        service_case_id=1,
        group_id="g",
        checkin_date=day,
        content="打卡",
        parsed_json={"weight": weight},
    )


def _window(now: date) -> tuple[date, date]:
    return now - timedelta(days=6), now


class TestEvaluatePlateau:
    def test_flat_week_is_plateau(self):
        now = date(2026, 8, 14)
        start, end = _window(now)
        rows = [_checkin(start + timedelta(days=i), "70.0") for i in range(0, 7, 2)]
        result = _evaluate_plateau(
            rows, baseline_kg=70.0, declared_evidence=[], window_start=start, window_end=end
        )
        assert result.status == STATUS_PLATEAU
        assert result.evidence["basis"] == ["statistical"]
        assert result.evidence["delta_kg"] == 0.0

    def test_small_fluctuation_is_still_plateau(self):
        now = date(2026, 8, 14)
        start, end = _window(now)
        rows = [
            _checkin(start, "70.0"),
            _checkin(start + timedelta(days=3), "70.3"),
            _checkin(end, "70.1"),
        ]
        result = _evaluate_plateau(
            rows, baseline_kg=70.0, declared_evidence=[], window_start=start, window_end=end
        )
        assert result.status == STATUS_PLATEAU
        assert abs(result.evidence["delta_kg"]) <= PLATEAU_MAX_DELTA_KG

    def test_weight_loss_over_week_is_not_plateau(self):
        now = date(2026, 8, 14)
        start, end = _window(now)
        rows = [
            _checkin(start, "70.0"),
            _checkin(start + timedelta(days=3), "69.5"),
            _checkin(end, "68.5"),
        ]
        result = _evaluate_plateau(
            rows, baseline_kg=70.0, declared_evidence=[], window_start=start, window_end=end
        )
        assert result.status == STATUS_NORMAL
        assert result.evidence["delta_kg"] == -1.5

    def test_insufficient_data_without_user_declared(self):
        now = date(2026, 8, 14)
        start, end = _window(now)
        rows = [_checkin(start, "70.0")]
        result = _evaluate_plateau(
            rows, baseline_kg=70.0, declared_evidence=[], window_start=start, window_end=end
        )
        assert result.status == STATUS_INSUFFICIENT_DATA
        assert result.evidence["basis"] == []

    def test_user_declared_wins_even_without_statistics(self):
        now = date(2026, 8, 14)
        start, end = _window(now)
        rows: list[Checkin] = []
        result = _evaluate_plateau(
            rows,
            baseline_kg=70.0,
            declared_evidence=["最近体重一直卡着不动"],
            window_start=start,
            window_end=end,
        )
        assert result.status == STATUS_PLATEAU
        assert "user_declared" in result.evidence["basis"]
        assert result.evidence["declared_evidence"] == ["最近体重一直卡着不动"]

    def test_out_of_window_rows_do_not_count(self):
        now = date(2026, 8, 14)
        start, end = _window(now)
        outside = _checkin(start - timedelta(days=10), "70.0")
        result = _evaluate_plateau(
            [outside],
            baseline_kg=70.0,
            declared_evidence=[],
            window_start=start,
            window_end=end,
        )
        assert result.status == STATUS_INSUFFICIENT_DATA

    def test_implausible_unit_mix_does_not_break_judgment(self):
        now = date(2026, 8, 14)
        start, end = _window(now)
        rows = [
            _checkin(start, "70公斤"),
            _checkin(start + timedelta(days=1), "140斤"),
            _checkin(start + timedelta(days=3), "70.1"),
            _checkin(end, "70.0"),
        ]
        result = _evaluate_plateau(
            rows, baseline_kg=70.0, declared_evidence=[], window_start=start, window_end=end
        )
        assert result.status == STATUS_PLATEAU

    def test_two_days_apart_is_not_plateau(self):
        now = date(2026, 8, 14)
        start, end = _window(now)
        rows = [_checkin(start, "70.0"), _checkin(start + timedelta(days=1), "70.1")]
        result = _evaluate_plateau(
            rows, baseline_kg=70.0, declared_evidence=[], window_start=start, window_end=end
        )
        assert result.status == STATUS_INSUFFICIENT_DATA
        assert "未隔满一周" in result.evidence["note"]

    def test_three_days_apart_is_not_plateau(self):
        now = date(2026, 8, 14)
        start, end = _window(now)
        rows = [_checkin(start, "70.0"), _checkin(start + timedelta(days=3), "70.1")]
        result = _evaluate_plateau(
            rows, baseline_kg=70.0, declared_evidence=[], window_start=start, window_end=end
        )
        assert result.status == STATUS_INSUFFICIENT_DATA

    def test_exactly_six_days_apart_is_plateau(self):
        now = date(2026, 8, 14)
        start, end = _window(now)
        rows = [_checkin(start, "70.0"), _checkin(start + timedelta(days=6), "70.1")]
        result = _evaluate_plateau(
            rows, baseline_kg=70.0, declared_evidence=[], window_start=start, window_end=end
        )
        assert result.status == STATUS_PLATEAU
        assert result.evidence["basis"] == ["statistical"]


async def _make_in_service_case(db_session) -> ServiceCase:
    user = User(name="平台期测试用户")
    db_session.add(user)
    await db_session.flush()
    now = datetime.now(timezone.utc)
    case = ServiceCase(
        case_no=f"TEST-PLATEAU-{uuid4().hex[:6].upper()}",
        user_id=user.id,
        group_id=f"plateau-{uuid4().hex[:8]}",
        questionnaire_token=uuid4().hex,
        status=ServiceCaseStatus.IN_SERVICE,
        started_at=now - timedelta(days=10),
        group_type=ServiceCaseGroupType.NEW_GROUP.value,
    )
    db_session.add(case)
    await db_session.flush()
    return case


class TestScanPlateaus:
    async def test_scan_marks_plateau_and_insufficient(self, db_session):
        today = datetime.now(TZ).date()
        plateau_case = await _make_in_service_case(db_session)
        for offset, weight in [(0, "70.0"), (3, "70.2"), (6, "70.1")]:
            db_session.add(
                Checkin(
                    service_case_id=plateau_case.id,
                    group_id=plateau_case.group_id,
                    checkin_date=today - timedelta(days=6 - offset),
                    content="打卡",
                    parsed_json={"weight": weight},
                )
            )
        no_data_case = await _make_in_service_case(db_session)
        db_session.add(
            Checkin(
                service_case_id=no_data_case.id,
                group_id=no_data_case.group_id,
                checkin_date=today - timedelta(days=6),
                content="打卡",
                parsed_json={"weight": "70.0"},
            )
        )
        await db_session.flush()

        stats = await scan_plateaus(db_session)

        assert stats.scanned == 2
        assert stats.plateau == 1
        assert stats.insufficient == 1
        await db_session.refresh(plateau_case)
        await db_session.refresh(no_data_case)
        assert plateau_case.plateau_status == STATUS_PLATEAU
        assert plateau_case.plateau_evidence["basis"] == ["statistical"]
        assert no_data_case.plateau_status == STATUS_INSUFFICIENT_DATA
        assert plateau_case.plateau_updated_at is not None

    async def test_user_declared_event_drives_plateau(self, db_session):
        today = datetime.now(TZ).date()
        case = await _make_in_service_case(db_session)
        db_session.add(
            OutcomeEvent(
                service_case_id=case.id,
                message_id=None,
                milestone="day7",
                field_key="weight_change",
                event_type="unchanged",
                value_text="体重没动",
                evidence_text="最近体重一直卡着不动",
                occurred_at=datetime.now(timezone.utc),
            )
        )
        await db_session.flush()

        stats = await scan_plateaus(db_session)

        assert stats.scanned == 1
        assert stats.plateau == 1
        assert stats.declared == 1
        await db_session.refresh(case)
        assert "user_declared" in case.plateau_evidence["basis"]
