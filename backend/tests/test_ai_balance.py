from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings
from app.services.ai_service.balance import (
    _looks_like_deepseek,
    _parse,
    fetch_deepseek_balance,
)


def test_parse_real_deepseek_response():
    data = {
        "is_available": True,
        "balance_infos": [
            {
                "currency": "CNY",
                "total_balance": "86.77",
                "granted_balance": "0.00",
                "topped_up_balance": "86.77",
            }
        ],
    }
    snap = _parse(data, 123.0)
    assert snap.available is True
    assert snap.balance_yuan == 86.77
    assert snap.currency == "CNY"
    assert snap.is_available is True


def test_parse_prefers_cny_when_multiple_currencies():
    data = {
        "is_available": True,
        "balance_infos": [
            {"currency": "USD", "total_balance": "12.00"},
            {"currency": "CNY", "total_balance": "50.50"},
        ],
    }
    snap = _parse(data, 0.0)
    assert snap.currency == "CNY"
    assert snap.balance_yuan == 50.50


def test_parse_empty_infos_returns_zero_available():
    snap = _parse({"is_available": False, "balance_infos": []}, 0.0)
    assert snap.available is True
    assert snap.balance_yuan == 0.0
    assert snap.is_available is False


def test_parse_bad_total_balance_marks_unavailable():
    data = {"balance_infos": [{"currency": "CNY", "total_balance": "not-a-number"}]}
    snap = _parse(data, 0.0)
    assert snap.available is False
    assert snap.balance_yuan is None


def test_parse_non_dict_marks_unavailable():
    snap = _parse(["unexpected"], 0.0)
    assert snap.available is False


def test_looks_like_deepseek():
    assert _looks_like_deepseek("https://api.deepseek.com")
    assert not _looks_like_deepseek("https://api.openai.com/v1")


@pytest.mark.asyncio
async def test_fetch_skips_non_deepseek_endpoint():
    settings = Settings(AI_BASE_URL="https://api.openai.com/v1", AI_API_KEY="sk-x")
    assert await fetch_deepseek_balance(settings) is None


@pytest.mark.asyncio
async def test_fetch_skips_when_no_key():
    settings = Settings(AI_BASE_URL="https://api.deepseek.com", AI_API_KEY="")
    assert await fetch_deepseek_balance(settings) is None


@pytest.mark.asyncio
async def test_balance_alert_day_gate(db_session):
    """同一天第二次插入触发唯一约束 IntegrityError → 天闸生效。"""
    from app.models.ai_balance_alert import AIBalanceAlert

    d = date(2026, 7, 31)
    async with db_session.begin_nested():
        db_session.add(AIBalanceAlert(alert_date=d, balance_yuan=5.0))
        await db_session.flush()

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(AIBalanceAlert(alert_date=d, balance_yuan=4.0))
            await db_session.flush()
