from __future__ import annotations

import pytest

from app.api.routes.dashboard import (
    UNKNOWN_LABEL,
    _band_short,
    _bucket_numeric,
    _build_distribution,
    _build_geography,
    _category_label,
    _num,
)
from app.services.china_regions import match_city


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


def test_category_label_normalizes_unknowns():
    assert _category_label("疾病管理型") == "疾病管理型"
    assert _category_label("  北京 ") == "北京"
    for sentinel in ("", None, "未采集", "未提供", "待完善", "需补充身高体重后判断"):
        assert _category_label(sentinel) == UNKNOWN_LABEL


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("湖州", ("浙江", "湖州")),
        ("浙江湖州", ("浙江", "湖州")),
        ("湖州市", ("浙江", "湖州")),
        ("浙江省湖州市吴兴区", ("浙江", "湖州")),
        ("新疆阿勒泰", ("新疆", "阿勒泰")),
        ("北京市朝阳区", ("北京", "北京")),
        ("未知地址", (None, None)),
        (None, (None, None)),
    ],
)
def test_match_city_uses_longest_city_name(raw, expected):
    assert match_city(raw) == expected


def test_bucket_numeric_buckets_and_unknown():
    edges = [18.5, 24, 28, 32]
    labels = ["偏瘦 <18.5", "正常 18.5-24", "超重 24-28", "肥胖 28-32", "重度 ≥32"]
    assert _bucket_numeric(17.0, edges, labels) == "偏瘦 <18.5"
    assert _bucket_numeric(21.0, edges, labels) == "正常 18.5-24"
    assert _bucket_numeric(26.0, edges, labels) == "超重 24-28"
    assert _bucket_numeric(30.0, edges, labels) == "肥胖 28-32"
    assert _bucket_numeric(40.0, edges, labels) == "重度 ≥32"
    assert _bucket_numeric(None, edges, labels) == UNKNOWN_LABEL


def test_bucket_numeric_boundary_is_left_closed():
    # 值恰好等于 edge 落入右侧桶（value < edge 才归左桶）
    edges = [0.4, 0.5, 0.6]
    labels = ["<0.4", "0.4-0.5", "0.5-0.6", "≥0.6"]
    assert _bucket_numeric(0.5, edges, labels) == "0.5-0.6"


def test_build_numeric_distribution_fixed_order_with_zero_buckets():
    dim = {
        "key": "bmi",
        "label": "BMI 区间",
        "description": "",
        "numeric": True,
        "edges": [18.5, 24, 28, 32],
        "labels": ["偏瘦 <18.5", "正常 18.5-24", "超重 24-28", "肥胖 28-32", "重度 ≥32"],
    }
    # 逐-case 标签：一个已分桶到"超重"，一个未采集
    case_labels = {
        1: {"bmi": "超重 24-28"},
        2: {"bmi": UNKNOWN_LABEL},
    }
    dist = _build_distribution(dim, case_labels)
    assert dist.unknown == 1
    assert dist.total == 1
    # 固定顺序、含 0 计数桶
    assert [b.label for b in dist.buckets] == dim["labels"]
    counts = {b.label: b.count for b in dist.buckets}
    assert counts["超重 24-28"] == 1
    assert counts["偏瘦 <18.5"] == 0


def test_build_category_distribution_counts_and_unknown_sorted():
    dim = {
        "key": "customer_type",
        "label": "客户分型",
        "description": "",
        "numeric": False,
    }
    case_labels = {
        1: {"customer_type": "健康投资型"},
        2: {"customer_type": "健康投资型"},
        3: {"customer_type": "疾病管理型"},
        4: {"customer_type": UNKNOWN_LABEL},
        5: {"customer_type": UNKNOWN_LABEL},
    }
    dist = _build_distribution(dim, case_labels)
    assert dist.unknown == 2
    assert dist.total == 3
    # 按计数降序
    assert dist.buckets[0].label == "健康投资型"
    assert dist.buckets[0].count == 2


def test_build_multi_select_distribution_counts_each_case_once_per_bucket():
    dim = {
        "key": "health_domain",
        "label": "健康领域",
        "description": "",
        "numeric": False,
        "multi_select": True,
    }
    case_labels = {
        1: {"health_domain": ["心血管", "肝胆", "心血管"]},
        2: {"health_domain": ["心血管"]},
        3: {"health_domain": []},
    }

    dist = _build_distribution(dim, case_labels)
    assert dist.multi_select is True
    assert dist.total == 2
    assert dist.unknown == 1
    assert {bucket.label: bucket.count for bucket in dist.buckets} == {"心血管": 2, "肝胆": 1}


def test_build_geography_groups_cities_under_provinces_and_keeps_unknown():
    geography = _build_geography(
        {
            1: {"province": "浙江", "city": "杭州"},
            2: {"province": "浙江", "city": "湖州"},
            3: {"province": "浙江", "city": "杭州"},
            4: {"province": UNKNOWN_LABEL, "city": UNKNOWN_LABEL},
        }
    )
    assert geography.total == 3
    assert geography.unknown == 1
    assert [(province.province, province.count) for province in geography.provinces] == [("浙江", 3)]
    assert [(city.city, city.count) for city in geography.provinces[0].cities] == [("杭州", 2), ("湖州", 1)]


@pytest.mark.asyncio
async def test_population_portrait_endpoint_shape(admin_client):
    resp = await admin_client.get("/dashboard/population-portrait")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_users" in data
    keys = {d["key"] for d in data["distributions"]}
    assert keys == {
        "customer_type",
        "health_domain",
        "health_condition",
        "medication_category",
        "special_stage",
        "pending_condition",
        "allergy_category",
        "weight_loss_band",
        "age",
        "bmi",
        "bodyfat",
        "waist_height_ratio",
    }
    assert "geography" in data
    assert {"total", "unknown", "provinces"} <= set(data["geography"])


@pytest.mark.asyncio
async def test_population_portrait_members_accepts_city_and_province_keys(admin_client):
    for key, bucket in (("city", "杭州"), ("province", "浙江")):
        resp = await admin_client.get(
            "/dashboard/population-portrait/members", params={"key": key, "bucket": bucket}
        )
        assert resp.status_code == 200
