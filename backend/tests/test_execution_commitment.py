from datetime import datetime, timezone

import pytest

from app.services.execution_commitment import (
    apply_commitment_choice,
    initialize_execution_commitment,
    minimum_complete_days,
    review_execution_commitment,
)


def _state(*, candidate: bool = True) -> dict:
    return initialize_execution_commitment(
        {
            "high_commitment_candidate": candidate,
            "high_commitment_basis": ["满足"],
            "basic_target_jin_or_range": "28天减重5-7斤",
            "challenge_target_jin_or_review_text": "28天向8-10斤推进",
        }
    )


def _choose(state: dict, choice: str) -> dict:
    return apply_commitment_choice(
        state,
        choice=choice,
        source_message_id=123,
        source_text=f"选择{choice}",
        occurred_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        sender_id="user-1",
        sender_name="用户",
    )


def test_cumulative_threshold_days_round_up():
    assert [minimum_complete_days(day, 0.60) for day in (7, 14, 21, 28)] == [5, 9, 13, 17]
    assert [minimum_complete_days(day, 0.85) for day in (7, 14, 21, 28)] == [6, 12, 18, 24]


def test_high_commitment_below_85_downgrades_to_standard():
    state = _choose(_state(), "high_commitment")
    updated, message = review_execution_commitment(
        state,
        checkpoint=14,
        complete_days=10,
        occurred_at=datetime(2026, 9, 15, tzinfo=timezone.utc),
    )
    assert updated["execution_path"] == "standard"
    assert updated["status"] == "active"
    assert "10/14天" in updated["high_commitment_downgrade_reason"]
    assert "调整为标准档" in (message or "")


def test_below_standard_reasks_user_instead_of_silently_pausing():
    state = _choose(_state(), "standard")
    updated, message = review_execution_commitment(
        state,
        checkpoint=7,
        complete_days=4,
        occurred_at=datetime(2026, 9, 8, tzinfo=timezone.utc),
    )
    assert updated["status"] == "reconfirming"
    assert "4天完整打卡" in (message or "")
    assert "继续按标准目标推进" in (message or "")
    assert "先按目前能做到的节奏稳住" in (message or "")


def test_current_pace_pauses_target_until_explicit_recommitment():
    paused = _choose(_state(), "current_pace")
    assert paused["status"] == "paused"
    resumed = _choose(paused, "standard")
    assert resumed["status"] == "active"
    assert resumed["execution_path"] == "standard"


def test_non_candidate_cannot_choose_high_commitment():
    with pytest.raises(ValueError, match="不具备高承诺档资格"):
        _choose(_state(candidate=False), "high_commitment")
