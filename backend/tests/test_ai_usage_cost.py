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
    # 显式关掉峰谷倍率：本例只校验单价本身，避免结果随运行时刻漂移
    settings = Settings(
        AI_PROMPT_COST_PER_1M_TOKENS=12.55,
        AI_COMPLETION_COST_PER_1M_TOKENS=25.11,
        AI_PEAK_PRICE_MULTIPLIER=1,
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
    # 峰谷倍率显式关掉：下面几例只校验缓存命中/未命中拆分，不应随运行时刻漂移
    return Settings(
        AI_PROMPT_COST_PER_1M_TOKENS=1,
        AI_COMPLETION_COST_PER_1M_TOKENS=2,
        AI_PROMPT_CACHE_HIT_COST_PER_1M_TOKENS=0.02,
        AI_PEAK_PRICE_MULTIPLIER=1,
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
        AI_PEAK_PRICE_MULTIPLIER=1,
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


def test_peak_multiplier_can_be_disabled():
    # 倍率填 1 = 整体关闭时段加价（官方默认已是 2，此处验证可关）
    settings = Settings(
        AI_PROMPT_COST_PER_1M_TOKENS=1,
        AI_COMPLETION_COST_PER_1M_TOKENS=2,
        AI_PEAK_PRICE_MULTIPLIER=1,
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


def _tiered_settings(**overrides) -> Settings:
    """2026-08-17 起的官方空闲价：flash 0.05/1.5/4.5，pro 0.15/4.5/13.5。"""
    values = {
        "AI_PROMPT_COST_PER_1M_TOKENS": 1.5,
        "AI_COMPLETION_COST_PER_1M_TOKENS": 4.5,
        "AI_PROMPT_CACHE_HIT_COST_PER_1M_TOKENS": 0.05,
        "AI_PREMIUM_MODELS": "deepseek-v4-pro",
        "AI_PREMIUM_PROMPT_COST_PER_1M_TOKENS": 4.5,
        "AI_PREMIUM_COMPLETION_COST_PER_1M_TOKENS": 13.5,
        "AI_PREMIUM_PROMPT_CACHE_HIT_COST_PER_1M_TOKENS": 0.15,
        "AI_PEAK_PRICE_MULTIPLIER": 1,
    }
    values.update(overrides)
    return Settings(**values)


def test_premium_model_uses_premium_pricing():
    settings = _tiered_settings()
    cost = _estimated_cost_yuan(
        prompt_tokens=6000,
        completion_tokens=3000,
        settings=settings,
        scenario="intake_doc",
        model="deepseek-v4-pro",
    )
    expected = (6000 * 4.5 + 3000 * 13.5) / 1_000_000
    assert cost == round(expected, 8)


def test_base_model_keeps_base_pricing():
    settings = _tiered_settings()
    cost = _estimated_cost_yuan(
        prompt_tokens=6000,
        completion_tokens=3000,
        settings=settings,
        scenario="ai_reply",
        model="deepseek-v4-flash",
    )
    expected = (6000 * 1.5 + 3000 * 4.5) / 1_000_000
    assert cost == round(expected, 8)


def test_premium_prefix_matches_dated_build():
    # 带日期后缀的实际模型名同样按高价档计费
    settings = _tiered_settings()
    cost = _estimated_cost_yuan(
        prompt_tokens=1000,
        completion_tokens=0,
        settings=settings,
        scenario="intake_doc",
        model="deepseek-v4-pro-0813",
    )
    assert cost == round(1000 * 4.5 / 1_000_000, 8)


def test_document_fallback_to_base_model_bills_base_rate():
    # 文档场景 fallback 回 flash：场景名仍是文档，但按实际模型落回基础单价
    settings = _tiered_settings()
    cost = _estimated_cost_yuan(
        prompt_tokens=6000,
        completion_tokens=3000,
        settings=settings,
        scenario="intake_doc",
        model="deepseek-v4-flash",
    )
    expected = (6000 * 1.5 + 3000 * 4.5) / 1_000_000
    assert cost == round(expected, 8)


def test_premium_cache_hit_billed_at_premium_hit_price():
    settings = _tiered_settings()
    cost = _estimated_cost_yuan(
        prompt_tokens=1000,
        completion_tokens=0,
        settings=settings,
        scenario="intake_doc",
        model="deepseek-v4-pro",
        prompt_cache_hit_tokens=800,
    )
    expected = (200 * 4.5 + 800 * 0.15) / 1_000_000
    assert cost == round(expected, 8)


def test_premium_cache_hit_unconfigured_falls_back_to_premium_miss_price():
    # 高价档命中价漏配时按本档未命中价计，不得沿用便宜档的命中价(0.05)而低估
    settings = _tiered_settings(AI_PREMIUM_PROMPT_CACHE_HIT_COST_PER_1M_TOKENS=0)
    cost = _estimated_cost_yuan(
        prompt_tokens=1000,
        completion_tokens=0,
        settings=settings,
        scenario="intake_doc",
        model="deepseek-v4-pro",
        prompt_cache_hit_tokens=800,
    )
    assert cost == round(1000 * 4.5 / 1_000_000, 8)


def test_premium_pricing_unconfigured_falls_back_to_base():
    settings = _tiered_settings(
        AI_PREMIUM_PROMPT_COST_PER_1M_TOKENS=0,
        AI_PREMIUM_COMPLETION_COST_PER_1M_TOKENS=0,
        AI_PREMIUM_PROMPT_CACHE_HIT_COST_PER_1M_TOKENS=0,
    )
    cost = _estimated_cost_yuan(
        prompt_tokens=1000,
        completion_tokens=1000,
        settings=settings,
        scenario="intake_doc",
        model="deepseek-v4-pro",
    )
    expected = (1000 * 1.5 + 1000 * 4.5) / 1_000_000
    assert cost == round(expected, 8)


def test_missing_model_keeps_base_pricing():
    # 拿不到模型名时按基础单价，不猜档位
    settings = _tiered_settings()
    cost = _estimated_cost_yuan(
        prompt_tokens=1000,
        completion_tokens=1000,
        settings=settings,
        scenario="intake_doc",
    )
    expected = (1000 * 1.5 + 1000 * 4.5) / 1_000_000
    assert cost == round(expected, 8)


def test_premium_pricing_doubles_in_peak_window():
    settings = _tiered_settings(AI_PEAK_PRICE_MULTIPLIER=2)
    bj = ZoneInfo("Asia/Shanghai")
    base = (6000 * 4.5 + 3000 * 13.5) / 1_000_000
    peak = _estimated_cost_yuan(
        prompt_tokens=6000,
        completion_tokens=3000,
        settings=settings,
        scenario="intake_doc",
        model="deepseek-v4-pro",
        now=datetime(2026, 8, 17, 10, 0, tzinfo=bj),
    )
    offpeak = _estimated_cost_yuan(
        prompt_tokens=6000,
        completion_tokens=3000,
        settings=settings,
        scenario="intake_doc",
        model="deepseek-v4-pro",
        now=datetime(2026, 8, 17, 20, 0, tzinfo=bj),
    )
    assert peak == round(base * 2, 8)
    assert offpeak == round(base, 8)


def test_qwen_pricing_ignores_model_tier_and_peak():
    # 视觉模型是另一家的计价，既不按 DeepSeek 档位也不受峰谷倍率影响
    settings = _tiered_settings(
        AI_PEAK_PRICE_MULTIPLIER=2,
        QWEN_PROMPT_COST_UNDER_128K_PER_1M_TOKENS=2,
        QWEN_COMPLETION_COST_UNDER_128K_PER_1M_TOKENS=8,
    )
    bj = ZoneInfo("Asia/Shanghai")
    cost = _estimated_cost_yuan(
        prompt_tokens=1000,
        completion_tokens=1000,
        settings=settings,
        scenario="qwen_vl_translate",
        model="deepseek-v4-pro",
        now=datetime(2026, 8, 17, 10, 0, tzinfo=bj),
    )
    assert cost == round((1000 * 2 + 1000 * 8) / 1_000_000, 8)
