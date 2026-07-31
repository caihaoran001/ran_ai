from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.core.config import Settings
from app.services.ai_service.client import _estimated_cost_yuan


def test_qwen_vl_translate_uses_qwen_tiered_pricing():
    settings = Settings(
        AI_PROMPT_COST_PER_1M_TOKENS=12.55,
        AI_COMPLETION_COST_PER_1M_TOKENS=25.11,
        QWEN_PROMPT_COST_UNDER_128K_PER_1M_TOKENS=2,
        QWEN_COMPLETION_COST_UNDER_128K_PER_1M_TOKENS=8,
    )

    cost = _estimated_cost_yuan(
        prompt_tokens=2418,
        completion_tokens=2287,
        settings=settings,
        scenario="qwen_vl_translate",
    )

    assert cost == 0.023132


def test_non_qwen_usage_keeps_ai_pricing():
    settings = Settings(
        AI_PROMPT_COST_PER_1M_TOKENS=12.55,
        AI_COMPLETION_COST_PER_1M_TOKENS=25.11,
        QWEN_PROMPT_COST_UNDER_128K_PER_1M_TOKENS=2,
        QWEN_COMPLETION_COST_UNDER_128K_PER_1M_TOKENS=8,
    )

    cost = _estimated_cost_yuan(
        prompt_tokens=1000,
        completion_tokens=1000,
        settings=settings,
        scenario="ai_reply",
    )

    assert cost == 0.03766


def _flash_settings() -> Settings:
    return Settings(
        AI_PROMPT_COST_PER_1M_TOKENS=1,
        AI_COMPLETION_COST_PER_1M_TOKENS=2,
        AI_PROMPT_CACHE_HIT_COST_PER_1M_TOKENS=0.02,
    )


def test_cache_hit_tokens_billed_at_hit_price():
    settings = _flash_settings()
    # 1000 输入里 800 命中(0.02) + 200 未命中(1) + 500 输出(2)
    cost = _estimated_cost_yuan(
        prompt_tokens=1000,
        completion_tokens=500,
        settings=settings,
        scenario="ai_reply",
        prompt_cache_hit_tokens=800,
    )
    expected = (200 * 1 + 800 * 0.02 + 500 * 2) / 1_000_000
    assert cost == round(expected, 8)


def test_cache_hit_missing_falls_back_to_miss_price():
    settings = _flash_settings()
    # 不传命中数 → 全部按未命中价，等同历史行为
    cost = _estimated_cost_yuan(
        prompt_tokens=1000,
        completion_tokens=500,
        settings=settings,
        scenario="ai_reply",
    )
    assert cost == round((1000 * 1 + 500 * 2) / 1_000_000, 8)


def test_cache_hit_unconfigured_bills_at_miss_price():
    # 未配置命中价(默认0) → 命中 token 也按未命中价，避免低估
    settings = Settings(
        AI_PROMPT_COST_PER_1M_TOKENS=1,
        AI_COMPLETION_COST_PER_1M_TOKENS=2,
    )
    cost = _estimated_cost_yuan(
        prompt_tokens=1000,
        completion_tokens=0,
        settings=settings,
        scenario="ai_reply",
        prompt_cache_hit_tokens=800,
    )
    assert cost == round(1000 * 1 / 1_000_000, 8)


def test_peak_multiplier_applies_only_in_peak_window():
    settings = Settings(
        AI_PROMPT_COST_PER_1M_TOKENS=1,
        AI_COMPLETION_COST_PER_1M_TOKENS=2,
        AI_PEAK_PRICE_MULTIPLIER=2,
    )
    bj = ZoneInfo("Asia/Shanghai")
    base = (1000 * 1 + 1000 * 2) / 1_000_000
    peak = _estimated_cost_yuan(
        prompt_tokens=1000,
        completion_tokens=1000,
        settings=settings,
        scenario="ai_reply",
        now=datetime(2026, 7, 31, 10, 0, tzinfo=bj),
    )
    offpeak = _estimated_cost_yuan(
        prompt_tokens=1000,
        completion_tokens=1000,
        settings=settings,
        scenario="ai_reply",
        now=datetime(2026, 7, 31, 20, 0, tzinfo=bj),
    )
    assert peak == round(base * 2, 8)
    assert offpeak == round(base, 8)


def test_peak_multiplier_default_is_noop():
    settings = Settings(
        AI_PROMPT_COST_PER_1M_TOKENS=1,
        AI_COMPLETION_COST_PER_1M_TOKENS=2,
    )
    bj = ZoneInfo("Asia/Shanghai")
    cost = _estimated_cost_yuan(
        prompt_tokens=1000,
        completion_tokens=1000,
        settings=settings,
        scenario="ai_reply",
        now=datetime(2026, 7, 31, 10, 0, tzinfo=bj),
    )
    assert cost == round((1000 * 1 + 1000 * 2) / 1_000_000, 8)
