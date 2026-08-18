"""平台期检测：验证「锚在最后一次称重 + 数据够就以数据为准」的判定口径。

客户口径：平台期 = 一星期体重没动静，或用户主动说类似「体重卡住了」。

判定顺序：先看窗口内体重够不够（至少 4 天、无连续 3 天空缺、最后一次称重不超过 2 天前），
够就由数据下结论；不够才轮到用户自述，并在证据里写明数据情况。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.core.config import Settings
from app.models.checkin import Checkin
from app.models.outcome import OutcomeEvent
from app.models.service_case import ServiceCase, ServiceCaseGroupType, ServiceCaseStatus
from app.models.user import User
from app.services.plateau_detector import (
    MIN_WEIGHT_DAYS_IN_WINDOW,
    PLATEAU_MAX_DELTA_KG,
    STATUS_INSUFFICIENT_DATA,
    STATUS_NORMAL,
    STATUS_PLATEAU,
    STATUS_REBOUND,
    ScanStats,
    WeightOutcomeSignal,
    _evaluate_plateau,
    plateau_followup_done_condition,
    plateau_followup_pending_condition,
    scan_plateaus,
)
from app.services.reminder_service import ReminderService

TZ = ZoneInfo("Asia/Shanghai")
TODAY = date(2026, 8, 17)


def _checkin(day: date, weight: str) -> Checkin:
    return Checkin(
        service_case_id=1,
        group_id="g",
        checkin_date=day,
        content="打卡",
        parsed_json={"weight": weight},
    )


def _daily(start: date, weights: list[str]) -> list[Checkin]:
    return [_checkin(start + timedelta(days=i), w) for i, w in enumerate(weights)]


def _signal(day: date, event_type: str, evidence: str) -> WeightOutcomeSignal:
    return WeightOutcomeSignal(
        event_type=event_type,
        evidence_text=evidence,
        occurred_at=datetime.combine(day, datetime.min.time(), tzinfo=TZ),
    )


def _evaluate(rows, *, signals=None, baseline_kg: float | None = 70.0, today: date = TODAY):
    return _evaluate_plateau(
        rows,
        baseline_kg=baseline_kg,
        outcome_signals=signals or [],
        today=today,
    )


class TestWeightDataDecides:
    """窗口内体重够用时，结论由数据说了算。"""

    def test_flat_week_is_plateau(self):
        rows = _daily(date(2026, 8, 11), ["70.0", "70.2", "69.9", "70.1", "70.0", "70.2", "70.1"])
        result = _evaluate(rows)
        assert result.status == STATUS_PLATEAU
        assert result.evidence["basis"] == ["statistical"]
        assert result.evidence["data_sufficient"] is True
        assert abs(result.evidence["delta_kg"]) <= PLATEAU_MAX_DELTA_KG
        assert result.evidence["window_start"] == "2026-08-11"
        assert result.evidence["window_end"] == "2026-08-17"

    def test_missing_today_is_still_plateau(self):
        """今天还没上秤不影响判定——窗口锚在最后一次称重，不钉死在今天。"""
        rows = _daily(date(2026, 8, 11), ["70.0", "70.2", "69.9", "70.1", "70.0", "70.2"])
        result = _evaluate(rows)
        assert result.status == STATUS_PLATEAU
        assert result.evidence["last_weight_date"] == "2026-08-16"
        assert result.evidence["days_since_last_weight"] == 1
        # 判定区间跟着最后一次称重往前推，不是「今天往前 7 天」
        assert result.evidence["window_start"] == "2026-08-10"
        assert result.evidence["window_end"] == "2026-08-16"

    def test_every_other_day_four_weigh_ins_is_plateau(self):
        rows = [
            _checkin(date(2026, 8, 11), "70.0"),
            _checkin(date(2026, 8, 13), "70.2"),
            _checkin(date(2026, 8, 15), "69.9"),
            _checkin(date(2026, 8, 17), "70.1"),
        ]
        result = _evaluate(rows)
        assert result.status == STATUS_PLATEAU
        assert result.evidence["valid_weight_rows"] == MIN_WEIGHT_DAYS_IN_WINDOW
        assert result.evidence["max_gap_days"] == 1

    def test_weight_loss_over_week_is_normal(self):
        rows = _daily(date(2026, 8, 11), ["70.0", "69.8", "69.6", "69.4", "69.2", "68.9", "68.5"])
        result = _evaluate(rows)
        assert result.status == STATUS_NORMAL
        assert result.evidence["delta_kg"] == -1.5

    def test_swing_within_flat_week_is_not_plateau(self):
        """首尾持平但一周里掉 4 斤又涨回来——是波动不是卡住。"""
        rows = [
            _checkin(date(2026, 8, 11), "80.0"),
            _checkin(date(2026, 8, 13), "77.8"),
            _checkin(date(2026, 8, 15), "78.2"),
            _checkin(date(2026, 8, 17), "80.0"),
        ]
        result = _evaluate(rows, baseline_kg=80.0)
        assert result.status == STATUS_NORMAL
        assert result.evidence["delta_kg"] == 0.0  # 首尾相减看不出来
        assert result.evidence["spread_kg"] == 2.2
        assert result.evidence["attention"] == "swing"
        assert "属波动而非平台期" in result.evidence["note"]

    def test_dip_then_climb_back_is_flagged_as_turning(self):
        """线上真实案例：前三天掉 1.1kg、后两天涨回 0.8kg，趋势被抵消成 -0.13。

        整周确实"没动"，但方向已经反了。状态仍是平台期（几次称重不足以改判反弹），
        必须额外把掉头说出来——否则运营当成"只是停住"，去谈调方案而不是先问原因。
        """

        rows = _daily(
            date(2026, 8, 11), ["65.4", "64.7", "64.3", "64.4", "64.3", "64.8", "65.1"]
        )
        result = _evaluate(rows, baseline_kg=66.0)
        assert result.status == STATUS_PLATEAU
        assert result.evidence["trend_kg"] == -0.15  # 一条直线看不出来
        assert result.evidence["attention"] == "regain"
        # 最低点 64.3 在 8/13 和 8/15 并列，取最晚那次：算的是最近这轮掉头
        assert result.evidence["regain_from_low"] == {
            "low_date": "2026-08-15",
            "low_kg": 64.3,
            "rise_kg": 0.8,
            "days": 2,
        }
        # 提示语只出现在前端那条红字里：note 讲这一周的数据，两处重复会让卡片啰嗦
        assert "回涨" not in result.evidence["note"]

    def test_turning_point_length_follows_the_data_not_a_fixed_window(self):
        """四天前掉头就报四天，两天前掉头就报两天（对照上一个用例）。

        写死"看最后四天"两头不讨好：两天就涨回来的人会被前面还在正常下降的日子冲淡
        幅度，四天前掉头的人又会被截断。
        """

        rows = _daily(date(2026, 8, 11), ["65.0", "64.6", "64.2", "64.4", "64.6", "64.8", "65.0"])
        result = _evaluate(rows, baseline_kg=66.0)
        assert result.status == STATUS_PLATEAU
        assert result.evidence["regain_from_low"]["low_date"] == "2026-08-13"
        assert result.evidence["regain_from_low"]["days"] == 4
        assert result.evidence["regain_from_low"]["rise_kg"] == 0.8

    def test_single_high_last_reading_is_not_a_turn(self):
        """只有最后一天虚高（前一晚吃咸）不算掉头：最低点后面得有两次称重。"""

        rows = _daily(date(2026, 8, 11), ["64.5", "64.4", "64.3", "64.3", "64.4", "64.2", "64.9"])
        result = _evaluate(rows, baseline_kg=65.0)
        assert result.status == STATUS_PLATEAU
        assert result.evidence["regain_from_low"] is None
        assert result.evidence["attention"] is None
        assert "回涨" not in result.evidence["note"]

    def test_flat_week_ending_at_its_low_is_not_a_turn(self):
        """真平台期：最低点就是最后一次称重，后面没得涨，安静判平台期。"""

        rows = [
            _checkin(date(2026, 8, 12), "77.5"),
            _checkin(date(2026, 8, 13), "77.2"),
            _checkin(date(2026, 8, 16), "77.5"),
            _checkin(date(2026, 8, 17), "77.2"),
        ]
        result = _evaluate(rows, baseline_kg=78.0)
        assert result.status == STATUS_PLATEAU
        assert result.evidence["regain_from_low"] is None

    def test_small_bounce_below_daily_noise_is_ignored(self):
        """触底后只回涨 0.3kg——喝水吃饭的日常浮动，不惊动运营。"""

        rows = _daily(date(2026, 8, 11), ["64.6", "64.5", "64.4", "64.3", "64.2", "64.4", "64.5"])
        result = _evaluate(rows, baseline_kg=65.0)
        assert result.status == STATUS_PLATEAU
        assert result.evidence["regain_from_low"] is None

    def test_steady_gain_is_rebound(self):
        rows = _daily(
            date(2026, 8, 11), ["78.0", "78.3", "78.6", "79.0", "79.2", "79.5", "79.8"]
        )
        result = _evaluate(rows, baseline_kg=78.0)
        assert result.status == STATUS_REBOUND
        assert result.evidence["trend_kg"] == 1.8

    def test_single_high_reading_does_not_trigger_rebound(self):
        """六天稳在 80.0、最后一天 81.2：趋势只抬了 0.77，一天涨 1.2kg 不可能是长肉。"""
        rows = _daily(
            date(2026, 8, 11), ["80.0", "80.1", "80.0", "79.9", "80.0", "80.1", "81.2"]
        )
        result = _evaluate(rows, baseline_kg=80.0)
        assert result.status == STATUS_NORMAL
        assert result.evidence["delta_kg"] == 1.2  # 首尾相减会误报反弹
        assert result.evidence["trend_kg"] == 0.77
        # 结论是正常，但详情页仍要露出来提醒观察下一次称重
        assert result.evidence["attention"] == "slight_gain"
        assert "略有上浮但未到反弹门槛" in result.evidence["note"]

    def test_steady_loss_has_no_attention_flag(self):
        rows = _daily(date(2026, 8, 11), ["70.0", "69.8", "69.6", "69.4", "69.2", "68.9", "68.5"])
        result = _evaluate(rows)
        assert result.status == STATUS_NORMAL
        assert result.evidence["attention"] is None

    def test_rebound_with_declaration_records_conflict(self):
        rows = _daily(
            date(2026, 8, 11), ["78.0", "78.3", "78.6", "79.0", "79.2", "79.5", "79.8"]
        )
        result = _evaluate(
            rows,
            baseline_kg=78.0,
            signals=[_signal(date(2026, 8, 17), "unchanged", "体重卡住了")],
        )
        assert result.status == STATUS_REBOUND
        assert result.evidence["declaration_conflicts_data"] is True

    def test_implausible_jump_is_dropped_without_breaking_judgment(self):
        rows = _daily(date(2026, 8, 11), ["70.0", "70.1", "76.5", "70.0", "70.2", "70.1", "70.0"])
        result = _evaluate(rows)
        assert result.status == STATUS_PLATEAU
        # 跳变那条被剔除，其余 6 天仍然够用
        assert result.evidence["valid_weight_rows"] == 6


class TestDataTooThin:
    """数据不够时不下统计结论，并说清楚差在哪。"""

    def test_two_scattered_weigh_ins_is_insufficient(self):
        rows = [_checkin(date(2026, 8, 13), "70.0"), _checkin(date(2026, 8, 15), "70.1")]
        result = _evaluate(rows)
        assert result.status == STATUS_INSUFFICIENT_DATA
        assert result.evidence["valid_weight_rows"] == 2
        assert "仅 2 天有有效体重" in result.evidence["note"]

    def test_three_days_without_weight_expires_conclusion(self):
        rows = _daily(date(2026, 8, 8), ["70.0", "70.1", "70.0", "70.2", "70.1", "70.0", "70.1"])
        result = _evaluate(rows)  # 最后一次 8/14，今天 8/17
        assert result.status == STATUS_INSUFFICIENT_DATA
        assert result.evidence["days_since_last_weight"] == 3
        assert "已连续三天没有体重" in result.evidence["note"]
        # 前端据此不画走向图：这批数字描述的已经不是"现在"
        assert result.evidence["weights_expired"] is True

    def test_current_weights_are_not_marked_expired(self):
        rows = _daily(date(2026, 8, 11), ["70.0", "70.2", "69.9", "70.1", "70.0", "70.2", "70.1"])
        result = _evaluate(rows)
        assert result.evidence["weights_expired"] is False

    def test_gap_inside_window_blocks_judgment(self):
        rows = [
            _checkin(date(2026, 8, 11), "70.0"),
            _checkin(date(2026, 8, 12), "70.1"),
            _checkin(date(2026, 8, 16), "70.0"),
            _checkin(date(2026, 8, 17), "70.1"),
        ]
        result = _evaluate(rows)  # 8/13-8/15 连续三天没称
        assert result.status == STATUS_INSUFFICIENT_DATA
        assert result.evidence["max_gap_days"] == 3
        assert "连续 3 天没有体重" in result.evidence["note"]

    def test_fresh_starter_is_told_span_is_short_not_broken(self):
        """刚开始称重、天天全勤但跨度不够 —— 不能说成「记录断档」。

        线上真实案例：8/15 起连称四天一次没落，系统却写「连续 3 天没有体重」，
        运营照着去问「你怎么好几天没称」，问的是一个全勤的人。
        """

        rows = _daily(date(2026, 8, 14), ["68.7", "68.9", "68.3", "68.5"])
        result = _evaluate(rows)  # 窗口 8/11~8/17，开头三天还没开始称
        assert result.status == STATUS_INSUFFICIENT_DATA
        assert result.evidence["valid_weight_rows"] == 4
        assert result.evidence["max_gap_days"] == 3
        note = result.evidence["note"]
        assert "2026-08-14 起连续 4 天有体重" in note
        assert "还差 1 天" in note
        assert "没有体重" not in note

    def test_fresh_starter_with_declaration_keeps_the_same_reason(self):
        rows = _daily(date(2026, 8, 14), ["68.7", "68.9", "68.3", "68.5"])
        result = _evaluate(rows, signals=[_signal(date(2026, 8, 17), "unchanged", "卡住了")])
        assert result.status == STATUS_PLATEAU
        assert result.evidence["note"].startswith("用户口述平台期；")
        assert "称重跨度还差 1 天" in result.evidence["note"]

    def test_two_weigh_ins_ten_days_apart_is_insufficient(self):
        rows = [_checkin(date(2026, 8, 7), "70.0"), _checkin(date(2026, 8, 17), "70.1")]
        result = _evaluate(rows)
        assert result.status == STATUS_INSUFFICIENT_DATA
        assert result.evidence["valid_weight_rows"] == 1

    def test_no_weight_and_no_declaration_is_insufficient(self):
        result = _evaluate([])
        assert result.status == STATUS_INSUFFICIENT_DATA
        assert result.evidence["note"] == "无有效体重打卡数据"


class TestUserDeclaration:
    """自述只在数据不够时说了算，且证据必须写明数据情况。"""

    def test_declaration_without_any_weight_is_plateau(self):
        result = _evaluate([], signals=[_signal(date(2026, 8, 16), "unchanged", "体重卡住了")])
        assert result.status == STATUS_PLATEAU
        assert result.evidence["basis"] == ["user_declared"]
        assert result.evidence["declared_evidence"] == ["体重卡住了"]
        assert "无有效体重打卡数据" in result.evidence["note"]

    def test_declaration_with_thin_weight_data_is_plateau(self):
        rows = [_checkin(date(2026, 8, 13), "70.0"), _checkin(date(2026, 8, 15), "70.1")]
        result = _evaluate(rows, signals=[_signal(date(2026, 8, 16), "unchanged", "体重卡住了")])
        assert result.status == STATUS_PLATEAU
        assert result.evidence["basis"] == ["user_declared"]
        assert result.evidence["data_sufficient"] is False
        assert "仅 2 天有有效体重" in result.evidence["note"]

    def test_declaration_matching_flat_data_lists_both_basis(self):
        rows = _daily(date(2026, 8, 11), ["70.0", "70.2", "69.9", "70.1", "70.0", "70.2", "70.1"])
        result = _evaluate(rows, signals=[_signal(date(2026, 8, 16), "unchanged", "体重卡住了")])
        assert result.status == STATUS_PLATEAU
        assert result.evidence["basis"] == ["statistical", "user_declared"]
        assert result.evidence["declaration_conflicts_data"] is False

    def test_declaration_conflicting_with_real_loss_stays_normal(self):
        """数据够且明显在掉秤 → 结论正常，但保留自述并标注不符（运营可跟进期望落差）。"""
        rows = _daily(date(2026, 8, 11), ["70.0", "69.9", "69.7", "69.5", "69.3", "69.0", "68.6"])
        result = _evaluate(rows, signals=[_signal(date(2026, 8, 17), "unchanged", "这两天体重没动")])
        assert result.status == STATUS_NORMAL
        assert result.evidence["declaration_conflicts_data"] is True
        assert result.evidence["declared_evidence"] == ["这两天体重没动"]
        assert result.evidence["delta_kg"] == -1.4
        assert "与用户自述" in result.evidence["note"]

    def test_newer_improved_outcome_supersedes_declaration(self):
        result = _evaluate(
            [],
            signals=[
                _signal(date(2026, 8, 13), "unchanged", "最近体重一直卡着不动"),
                _signal(date(2026, 8, 16), "improved", "这两天终于开始掉秤了"),
            ],
        )
        assert result.status == STATUS_INSUFFICIENT_DATA
        assert result.evidence["user_declared"] is False
        assert result.evidence["declared_superseded_by"] == "outcome_improved"

    def test_post_declaration_weight_loss_supersedes_declaration(self):
        rows = [_checkin(date(2026, 8, 13), "70.0"), _checkin(date(2026, 8, 16), "68.5")]
        result = _evaluate(rows, signals=[_signal(date(2026, 8, 13), "unchanged", "体重卡住了")])
        assert result.status == STATUS_INSUFFICIENT_DATA
        assert result.evidence["user_declared"] is False
        assert result.evidence["declared_superseded_by"] == "weight_loss"
        assert result.evidence["post_declared_delta_kg"] == -1.5


async def _make_case(
    db_session,
    *,
    status: ServiceCaseStatus = ServiceCaseStatus.IN_SERVICE,
    deleted_at: datetime | None = None,
) -> ServiceCase:
    user = User(name="平台期测试用户")
    db_session.add(user)
    await db_session.flush()
    now = datetime.now(timezone.utc)
    case = ServiceCase(
        case_no=f"TEST-PLATEAU-{uuid4().hex[:6].upper()}",
        user_id=user.id,
        group_id=f"plateau-{uuid4().hex[:8]}",
        questionnaire_token=uuid4().hex,
        status=status,
        started_at=now - timedelta(days=10),
        group_type=ServiceCaseGroupType.NEW_GROUP.value,
        deleted_at=deleted_at,
    )
    db_session.add(case)
    await db_session.flush()
    return case


def _add_flat_week(db_session, case: ServiceCase, today: date) -> None:
    """最近 5 天每天称重、体重没动——足够进入统计判定。"""
    for offset in range(4, -1, -1):
        db_session.add(
            Checkin(
                service_case_id=case.id,
                group_id=case.group_id,
                checkin_date=today - timedelta(days=offset),
                content="打卡",
                parsed_json={"weight": "70.0" if offset % 2 else "70.1"},
            )
        )


class TestScanPlateaus:
    """扫描落库：只断言本用例创建的服务单，避免依赖本地库里的存量数据。"""

    async def test_scan_marks_plateau_and_insufficient(self, db_session):
        today = datetime.now(TZ).date()
        plateau_case = await _make_case(db_session)
        _add_flat_week(db_session, plateau_case, today)
        thin_case = await _make_case(db_session)
        db_session.add(
            Checkin(
                service_case_id=thin_case.id,
                group_id=thin_case.group_id,
                checkin_date=today,
                content="打卡",
                parsed_json={"weight": "70.0"},
            )
        )
        await db_session.flush()

        await scan_plateaus(db_session)

        await db_session.refresh(plateau_case)
        await db_session.refresh(thin_case)
        assert plateau_case.plateau_status == STATUS_PLATEAU
        assert plateau_case.plateau_evidence["basis"] == ["statistical"]
        assert plateau_case.plateau_updated_at is not None
        assert thin_case.plateau_status == STATUS_INSUFFICIENT_DATA

    async def test_scan_skips_write_when_conclusion_unchanged(self, db_session):
        today = datetime.now(TZ).date()
        case = await _make_case(db_session)
        _add_flat_week(db_session, case, today)
        await db_session.flush()

        await scan_plateaus(db_session)
        await db_session.refresh(case)
        first_written_at = case.plateau_updated_at
        first_updated_at = case.updated_at

        stats = await scan_plateaus(db_session)

        await db_session.refresh(case)
        assert stats.changed == 0
        assert case.plateau_updated_at == first_written_at
        assert case.updated_at == first_updated_at

    async def test_user_declared_event_drives_plateau(self, db_session):
        case = await _make_case(db_session)
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

        await scan_plateaus(db_session)

        await db_session.refresh(case)
        assert case.plateau_status == STATUS_PLATEAU
        assert case.plateau_evidence["basis"] == ["user_declared"]
        assert "无有效体重打卡数据" in case.plateau_evidence["note"]

    async def test_newer_improved_event_clears_old_declaration(self, db_session):
        case = await _make_case(db_session)
        now = datetime.now(timezone.utc)
        db_session.add_all(
            [
                OutcomeEvent(
                    service_case_id=case.id,
                    message_id=None,
                    milestone="day7",
                    field_key="weight_change",
                    event_type="unchanged",
                    value_text="体重没动",
                    evidence_text="最近体重一直卡着不动",
                    occurred_at=now - timedelta(days=2),
                ),
                OutcomeEvent(
                    service_case_id=case.id,
                    message_id=None,
                    milestone="day7",
                    field_key="weight_change",
                    event_type="improved",
                    value_text="体重开始下降",
                    evidence_text="这两天终于开始掉秤了",
                    occurred_at=now,
                ),
            ]
        )
        await db_session.flush()

        await scan_plateaus(db_session)

        await db_session.refresh(case)
        assert case.plateau_status == STATUS_INSUFFICIENT_DATA
        assert case.plateau_evidence["user_declared"] is False
        assert case.plateau_evidence["declared_superseded_by"] == "outcome_improved"

    async def test_scan_covers_all_non_deleted_statuses_and_skips_deleted(self, db_session):
        today = datetime.now(TZ).date()
        cases = [
            await _make_case(db_session, status=ServiceCaseStatus.CREATED),
            await _make_case(db_session, status=ServiceCaseStatus.COMPLETED),
            await _make_case(db_session, status=ServiceCaseStatus.REFUNDED),
        ]
        deleted_case = await _make_case(
            db_session,
            status=ServiceCaseStatus.COMPLETED,
            deleted_at=datetime.now(TZ),
        )
        for case in [*cases, deleted_case]:
            _add_flat_week(db_session, case, today)
        await db_session.flush()

        await scan_plateaus(db_session)

        for case in cases:
            await db_session.refresh(case)
            assert case.plateau_status == STATUS_PLATEAU
        await db_session.refresh(deleted_case)
        assert deleted_case.plateau_status == STATUS_NORMAL
        assert deleted_case.plateau_evidence is None


class TestFollowupQueue:
    """审核控制台的「体重状态跟进」待办：标记后清零，结论再变一次重新出现。"""

    async def _pending_ids(self, db_session) -> set[int]:
        rows = await db_session.scalars(
            select(ServiceCase.id).where(
                ServiceCase.deleted_at.is_(None),
                plateau_followup_pending_condition(),
            )
        )
        return set(rows)

    async def _done_ids(self, db_session) -> set[int]:
        rows = await db_session.scalars(
            select(ServiceCase.id).where(
                ServiceCase.deleted_at.is_(None),
                plateau_followup_done_condition(),
            )
        )
        return set(rows)

    async def test_plateau_case_enters_queue_and_clears_after_followup(self, db_session):
        today = datetime.now(TZ).date()
        case = await _make_case(db_session)
        _add_flat_week(db_session, case, today)
        await db_session.flush()
        await scan_plateaus(db_session)
        await db_session.refresh(case)
        assert case.plateau_status == STATUS_PLATEAU
        assert case.id in await self._pending_ids(db_session)

        # 运营点「标记已跟进」
        case.plateau_followed_up_at = datetime.now(timezone.utc)
        await db_session.flush()
        assert case.id not in await self._pending_ids(db_session)

        # 结论没变 → 再扫一遍也不该重新占用红点
        await scan_plateaus(db_session)
        assert case.id not in await self._pending_ids(db_session)
        # 但要能在「已跟进」列表里找回来（运营点完之后要能回查、能撤销）
        assert case.id in await self._done_ids(db_session)

    async def test_conclusion_change_after_followup_reopens_the_item(self, db_session):
        today = datetime.now(TZ).date()
        case = await _make_case(db_session)
        _add_flat_week(db_session, case, today)
        await db_session.flush()
        await scan_plateaus(db_session)
        await db_session.refresh(case)
        # 跟进发生在这次判定之后
        case.plateau_followed_up_at = datetime.now(timezone.utc)
        await db_session.flush()
        assert case.id not in await self._pending_ids(db_session)

        # 之后体重掉下来又反弹：结论从平台期变成反弹，判定时间刷新 → 重新待跟进
        for offset, weight in ((2, "70.6"), (1, "71.2"), (0, "71.8")):
            existing = await db_session.scalar(
                select(Checkin).where(
                    Checkin.service_case_id == case.id,
                    Checkin.checkin_date == today - timedelta(days=offset),
                )
            )
            existing.parsed_json = {"weight": weight}
        await db_session.flush()
        await scan_plateaus(db_session)

        await db_session.refresh(case)
        assert case.plateau_status == STATUS_REBOUND
        assert case.id in await self._pending_ids(db_session)
        assert case.id not in await self._done_ids(db_session)  # 两个列表互斥

    async def test_normal_case_never_enters_queue(self, db_session):
        today = datetime.now(TZ).date()
        case = await _make_case(db_session)
        for offset, weight in enumerate(["70.0", "69.8", "69.5", "69.2", "68.6"]):
            db_session.add(
                Checkin(
                    service_case_id=case.id,
                    group_id=case.group_id,
                    checkin_date=today - timedelta(days=4 - offset),
                    content="打卡",
                    parsed_json={"weight": weight},
                )
            )
        await db_session.flush()
        await scan_plateaus(db_session)

        await db_session.refresh(case)
        assert case.plateau_status == STATUS_NORMAL
        assert case.id not in await self._pending_ids(db_session)
        assert case.id not in await self._done_ids(db_session)


class TestTrendChartEvidence:
    """详情页要画出走向，判定证据里必须带上窗口内每次称重和那条趋势线的两端。"""

    def test_evidence_carries_weigh_ins_and_fitted_line(self):
        rows = [
            _checkin(date(2026, 8, 11), "70.0"),
            _checkin(date(2026, 8, 13), "70.2"),
            _checkin(date(2026, 8, 15), "69.9"),
            _checkin(date(2026, 8, 17), "70.1"),
        ]
        result = _evaluate(rows)

        assert result.status == STATUS_PLATEAU
        assert [point["date"] for point in result.evidence["points"]] == [
            "2026-08-11",
            "2026-08-13",
            "2026-08-15",
            "2026-08-17",
        ]
        assert [point["kg"] for point in result.evidence["points"]] == [70.0, 70.2, 69.9, 70.1]
        # 线的两端之差就是判定用的趋势值：页面画的线和结论说的话必须是同一条
        # （两端各自四舍五入到 0.01，允许一分钱级别的误差，肉眼看不出来）
        start = result.evidence["trend_start_kg"]
        end = result.evidence["trend_end_kg"]
        assert abs((end - start) - result.evidence["trend_kg"]) <= 0.02

    def test_points_present_even_when_data_is_too_thin(self):
        """只称过两次也要给点：运营看图才知道缺的是哪几天。"""
        rows = [_checkin(date(2026, 8, 13), "70.0"), _checkin(date(2026, 8, 15), "70.1")]
        result = _evaluate(rows)

        assert result.status == STATUS_INSUFFICIENT_DATA
        assert len(result.evidence["points"]) == 2
        assert result.evidence["trend_start_kg"] is None


class TestPlateauScheduling:
    """天闸：每天到点扫一次，当天不重复；重启不会让判定时刻漂移。"""

    @staticmethod
    def _service(monkeypatch, calls: list[datetime], **overrides) -> ReminderService:
        async def fake_scan(_session):
            calls.append(datetime.now(timezone.utc))
            return ScanStats(scanned=0, plateau=0, insufficient=0, declared=0, changed=0)

        monkeypatch.setattr("app.services.plateau_detector.scan_plateaus", fake_scan)
        options = {
            "PLATEAU_DETECTION_ENABLED": True,
            "PLATEAU_CHECK_HOUR": 12,
            "REMINDER_TIMEZONE": "Asia/Shanghai",
            **overrides,
        }
        return ReminderService(Settings(**options))

    async def test_scans_once_per_day_after_configured_hour(self, monkeypatch):
        calls: list[datetime] = []
        service = self._service(monkeypatch, calls)
        china = timezone(timedelta(hours=8))

        await service.check_plateau_once(datetime(2026, 8, 17, 11, 59, tzinfo=china))
        assert calls == []

        await service.check_plateau_once(datetime(2026, 8, 17, 12, 1, tzinfo=china))
        await service.check_plateau_once(datetime(2026, 8, 17, 20, 0, tzinfo=china))
        assert len(calls) == 1

        await service.check_plateau_once(datetime(2026, 8, 18, 12, 5, tzinfo=china))
        assert len(calls) == 2

    async def test_disabled_switch_skips_scan(self, monkeypatch):
        calls: list[datetime] = []
        service = self._service(monkeypatch, calls, PLATEAU_DETECTION_ENABLED=False)

        await service.check_plateau_once(datetime(2026, 8, 17, 13, 0, tzinfo=timezone(timedelta(hours=8))))

        assert calls == []
