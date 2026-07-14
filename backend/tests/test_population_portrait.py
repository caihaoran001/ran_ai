from __future__ import annotations

import pytest

from app.api.routes.dashboard import (
    _band_short,
    _category_distribution,
    _num,
    _numeric_distribution,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("34岁", 34.0),
        ("21.2", 21.2),
        ("未提供", None),
        ("暂无数据", None),
        (None, None),
        ("165cm", 165.0),
    ],
)
def test_num_parses_facts(raw, expected):
    assert _num(raw) == expected


def test_band_short():
    assert _band_short("10斤带（C带）") == "C带"
    assert _band_short("增肌增能带") == "增肌增能带"
    assert _band_short("未采集") == "未采集"


def test_numeric_distribution_buckets_and_unknown():
    dist = _numeric_distribution(
        key="bmi",
        label="BMI 区间",
        description="",
        values=[17.0, 21.0, 26.0, 30.0, 40.0, None],
        edges=[18.5, 24, 28, 32],
        labels=["偏瘦 <18.5", "正常 18.5-24", "超重 24-28", "肥胖 28-32", "重度 ≥32"],
    )
    assert dist.unknown == 1
    assert dist.total == 5
    counts = {b.label: b.count for b in dist.buckets}
    assert counts == {
        "偏瘦 <18.5": 1,
        "正常 18.5-24": 1,
        "超重 24-28": 1,
        "肥胖 28-32": 1,
        "重度 ≥32": 1,
    }


def test_numeric_distribution_boundary_is_left_closed():
    # 值恰好等于 edge 落入右侧桶（value < edge 才归左桶）
    dist = _numeric_distribution(
        key="whtr",
        label="腰高比",
        description="",
        values=[0.5],
        edges=[0.4, 0.5, 0.6],
        labels=["<0.4", "0.4-0.5", "0.5-0.6", "≥0.6"],
    )
    counts = {b.label: b.count for b in dist.buckets}
    assert counts["0.5-0.6"] == 1
    assert counts["0.4-0.5"] == 0


def test_category_distribution_counts_and_unknown_sorted():
    dist = _category_distribution(
        key="customer_type",
        label="客户分型",
        description="",
        values=["健康投资型", "健康投资型", "疾病管理型", "未采集", "待完善"],
    )
    assert dist.unknown == 2  # 未采集 + 待完善
    assert dist.total == 3
    # 按计数降序
    assert dist.buckets[0].label == "健康投资型"
    assert dist.buckets[0].count == 2


@pytest.mark.asyncio
async def test_population_portrait_endpoint_shape(admin_client):
    resp = await admin_client.get("/dashboard/population-portrait")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_users" in data
    keys = {d["key"] for d in data["distributions"]}
    assert keys == {
        "customer_type",
        "weight_loss_band",
        "age",
        "bmi",
        "bodyfat",
        "waist_height_ratio",
    }
