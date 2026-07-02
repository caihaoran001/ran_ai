from __future__ import annotations

from app.core.config import Settings
from app.services.ai_service.client import _estimated_cost_yuan


def test_qwen_vl_translate_uses_qwen_tiered_pricing():
    settings = Settings(
        AI_PROMPT_COST_PER_1M_TOKENS=12.55,
        AI_COMPLETION_COST_PER_1M_TOKENS=25.11,
        QWEN_PROMPT_COST_UNDER_128K_PER_1M_TOKENS=0.8,
        QWEN_COMPLETION_COST_UNDER_128K_PER_1M_TOKENS=4.8,
    )

    cost = _estimated_cost_yuan(
        prompt_tokens=2418,
        completion_tokens=2287,
        settings=settings,
        scenario="qwen_vl_translate",
    )

    assert cost == 0.012912


def test_non_qwen_usage_keeps_ai_pricing():
    settings = Settings(
        AI_PROMPT_COST_PER_1M_TOKENS=12.55,
        AI_COMPLETION_COST_PER_1M_TOKENS=25.11,
        QWEN_PROMPT_COST_UNDER_128K_PER_1M_TOKENS=0.8,
        QWEN_COMPLETION_COST_UNDER_128K_PER_1M_TOKENS=4.8,
    )

    cost = _estimated_cost_yuan(
        prompt_tokens=1000,
        completion_tokens=1000,
        settings=settings,
        scenario="ai_reply",
    )

    assert cost == 0.03766
