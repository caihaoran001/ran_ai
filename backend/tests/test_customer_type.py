from __future__ import annotations

import pytest

from app.services.intake_facts import (
    CUSTOMER_TYPE_DISEASE,
    CUSTOMER_TYPE_HEALTH_INVEST,
    CUSTOMER_TYPE_WEIGHT_LOSS,
    customer_type_for_profile,
    derive_customer_type,
    weight_loss_band_from_profile,
)


@pytest.mark.parametrize(
    "risk_level,band,expected",
    [
        # 高风险优先级最高，无论带位
        ("high", "维持优化带（A带）", CUSTOMER_TYPE_DISEASE),
        ("high", "20斤及以上带（D带）", CUSTOMER_TYPE_DISEASE),
        ("HIGH", None, CUSTOMER_TYPE_DISEASE),
        # 非高风险 + C/D 带 → 减重攻坚
        ("medium", "10斤带（C带）", CUSTOMER_TYPE_WEIGHT_LOSS),
        ("normal", "20斤及以上带（D带）", CUSTOMER_TYPE_WEIGHT_LOSS),
        # 非高风险 + A/B/增肌增能 → 健康投资
        ("normal", "维持优化带（A带）", CUSTOMER_TYPE_HEALTH_INVEST),
        ("medium", "5斤带（B带）", CUSTOMER_TYPE_HEALTH_INVEST),
        ("normal", "增肌增能带", CUSTOMER_TYPE_HEALTH_INVEST),
        # 带位缺失且非高风险 → 兜底健康投资，不留空标签
        ("normal", None, CUSTOMER_TYPE_HEALTH_INVEST),
        (None, "", CUSTOMER_TYPE_HEALTH_INVEST),
        (None, "需补充身高体重后判断", CUSTOMER_TYPE_HEALTH_INVEST),
    ],
)
def test_derive_customer_type(risk_level, band, expected):
    assert derive_customer_type(risk_level, band) == expected


def test_weight_loss_band_from_profile_prefers_top_level():
    assert weight_loss_band_from_profile({"weight_loss_band": "10斤带（C带）"}) == "10斤带（C带）"


def test_weight_loss_band_from_profile_falls_back_to_overview():
    profile = {"overview": {"weight_loss_band": "5斤带（B带）"}}
    assert weight_loss_band_from_profile(profile) == "5斤带（B带）"


def test_weight_loss_band_from_profile_missing():
    assert weight_loss_band_from_profile({}) is None
    assert weight_loss_band_from_profile(None) is None


def test_customer_type_for_profile_combines_signals():
    # 高风险优先
    assert (
        customer_type_for_profile({"overview": {"weight_loss_band": "维持优化带（A带）"}}, "high")
        == CUSTOMER_TYPE_DISEASE
    )
    # C 带 + 非高风险
    assert (
        customer_type_for_profile({"weight_loss_band": "10斤带（C带）"}, "normal")
        == CUSTOMER_TYPE_WEIGHT_LOSS
    )
    # 兜底
    assert customer_type_for_profile({}, "normal") == CUSTOMER_TYPE_HEALTH_INVEST
