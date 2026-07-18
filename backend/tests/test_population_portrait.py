from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.api.routes.dashboard import (
    UNKNOWN_LABEL,
    _band_short,
    _bucket_numeric,
    _build_distribution,
    _build_geography,
    _category_label,
    _compute_case_portrait_labels,
    _num,
)
from app.models.questionnaire import QuestionnaireSubmission
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user import User
from app.models.user_state_profile import UserStateProfile
from app.services.china_regions import match_city
from app.services.intake_facts import CUSTOM_ANSWER_LABEL, structured_answer_values


async def _create_portrait_case(
    db_session,
    *,
    user: User | None = None,
    status: ServiceCaseStatus = ServiceCaseStatus.IN_SERVICE,
    created_at: datetime | None = None,
    deleted_at: datetime | None = None,
    refund_pending_confirmation: bool = False,
    health_profile: dict | None = None,
    intake_answers: dict | None = None,
) -> tuple[User, ServiceCase]:
    tag = uuid4().hex[:10]
    if user is None:
        user = User(name=f"画像测试-{tag}")
        db_session.add(user)
        await db_session.flush()
    service_case = ServiceCase(
        case_no=f"PORTRAIT-{tag}",
        user_id=user.id,
        group_id=f"portrait-group-{tag}",
        questionnaire_token=f"portrait-token-{tag}",
        status=status,
        created_at=created_at or datetime.now(timezone.utc),
        deleted_at=deleted_at,
        refund_pending_confirmation=refund_pending_confirmation,
        health_profile=health_profile,
    )
    db_session.add(service_case)
    await db_session.flush()
    if intake_answers is not None:
        db_session.add(
            QuestionnaireSubmission(
                service_case_id=service_case.id,
                template_key="intake",
                answers_json=intake_answers,
            )
        )
        await db_session.flush()
    return user, service_case


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


def test_structured_answer_values_preserves_option_punctuation_and_groups_custom_text():
    fields = [
        {
            "key": "needs",
            "title": "您当前最想先解决的是哪类问题？",
            "options": ["腰腹部肥胖明显、身体发沉", "其他"],
        },
        {
            "key": "diet",
            "title": "哪一句最符合你的长期饮食模式？",
            "options": ["我追求低糖、少加工、蛋白优先、主食适量"],
        },
    ]
    answers = {
        "needs": "腰腹部肥胖明显、身体发沉||其他：想改善体态",
        "diet": "我追求低糖、少加工、蛋白优先、主食适量",
    }

    assert structured_answer_values(fields, answers, "最想先解决") == [
        "腰腹部肥胖明显、身体发沉",
        CUSTOM_ANSWER_LABEL,
    ]
    assert structured_answer_values(fields, answers, "长期饮食模式") == [
        "我追求低糖、少加工、蛋白优先、主食适量"
    ]


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
        "main_need",
        "expected_change",
        "diet_pattern",
        "sleep_problem",
        "sleep_quality",
        "supplement_habit",
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
async def test_population_portrait_groups_structured_supplement_habits_and_drills_down(
    admin_client,
    db_session,
):
    _, using_case = await _create_portrait_case(db_session)
    _, considering_case = await _create_portrait_case(db_session)
    _, no_supplement_case = await _create_portrait_case(db_session)
    now = datetime.now(timezone.utc).isoformat()
    db_session.add_all(
        [
            UserStateProfile(
                service_case_id=using_case.id,
                source="dynamic",
                state_json={
                    "version": 1,
                    "domains": {
                        "lifestyle_habits": {
                            "label": "稳定习惯与生活约束",
                            "current": {
                                "value": {
                                    "supplement_habit": [
                                        {
                                            "value": "正在使用鱼油",
                                            "supplement_name": "鱼油",
                                            "habit_status": "using",
                                        },
                                        {
                                            "value": "咨询/考虑使用维生素D",
                                            "supplement_name": "维生素D",
                                            "habit_status": "considering",
                                        },
                                    ]
                                },
                                "updated_at": now,
                            },
                            "history": [],
                        }
                    },
                },
            ),
            UserStateProfile(
                service_case_id=considering_case.id,
                source="dynamic",
                state_json={
                    "version": 1,
                    "domains": {
                        "lifestyle_habits": {
                            "label": "稳定习惯与生活约束",
                            "current": {
                                "value": {
                                    "supplement_habit": [
                                        {
                                            "value": "正在使用鱼油",
                                            "supplement_name": "鱼油",
                                            "habit_status": "using",
                                        }
                                    ]
                                },
                                "updated_at": now,
                            },
                            "history": [],
                        }
                    },
                },
            ),
        ]
    )
    await db_session.flush()

    _, labels_by_case = await _compute_case_portrait_labels(db_session)
    assert labels_by_case[using_case.id]["supplement_habit"] == [
        "鱼油 · 正在使用",
        "维生素D · 咨询/考虑使用",
    ]
    assert labels_by_case[considering_case.id]["supplement_habit"] == ["鱼油 · 正在使用"]
    assert labels_by_case[no_supplement_case.id]["supplement_habit"] == []

    portrait = await admin_client.get("/dashboard/population-portrait")
    assert portrait.status_code == 200
    supplement_distribution = next(
        item for item in portrait.json()["distributions"] if item["key"] == "supplement_habit"
    )
    buckets = {item["label"]: item["count"] for item in supplement_distribution["buckets"]}
    assert buckets["鱼油 · 正在使用"] >= 2
    assert buckets["维生素D · 咨询/考虑使用"] >= 1

    members = await admin_client.get(
        "/dashboard/population-portrait/members",
        params={"key": "supplement_habit", "bucket": "维生素D · 咨询/考虑使用"},
    )
    assert members.status_code == 200
    assert using_case.id in {item["id"] for item in members.json()}


@pytest.mark.asyncio
async def test_population_portrait_includes_user_without_ai_profile(
    admin_client,
    db_session,
    cai_haoran_answers,
):
    baseline_total, _ = await _compute_case_portrait_labels(db_session)
    answers = dict(cai_haoran_answers)
    answers.update(
        {
            "SIMPLE1774103589114mn0fgyd6": "83",
            "SIMPLE1774103860160mn0fmri8": "新疆阿勒泰",
            "SIMPLE1774104038286mn0fqky6": "160",
            "SIMPLE1774104087629mn0frn0t": "80",
            "SIMPLE1774104132462mn0fslm6": "39",
            "SIMPLE1774104236120mn0futlk": "100",
        }
    )
    _, service_case = await _create_portrait_case(
        db_session,
        status=ServiceCaseStatus.QUESTIONNAIRE_SUBMITTED,
        intake_answers=answers,
        health_profile={
            "conditions": [
                {
                    "code": "hypertension",
                    "status": "confirmed",
                    "evidence": "用户明确填写高血压",
                    "source": "questionnaire",
                }
            ],
            "medications": [],
            "special_stages": [],
            "allergies": [
                {
                    "category_code": "food_allergy",
                    "substance": "花生",
                    "status": "confirmed",
                    "evidence": "用户明确填写花生过敏",
                    "source": "questionnaire",
                }
            ],
        },
    )

    total, labels_by_case = await _compute_case_portrait_labels(db_session)
    assert total == baseline_total + 1
    labels = labels_by_case[service_case.id]
    assert labels["customer_type"] == UNKNOWN_LABEL
    assert labels["weight_loss_band"] == UNKNOWN_LABEL
    assert labels["age"] == "55 岁以上"
    assert labels["bmi"] == "肥胖 28-32"
    assert labels["bodyfat"] == "≥35%"
    assert labels["waist_height_ratio"] == "≥0.6 偏高风险"
    assert labels["province"] == "新疆"
    assert labels["city"] == "阿勒泰"
    assert labels["health_domain"] == ["心血管"]
    assert labels["health_condition"] == ["高血压"]
    assert labels["allergy_category"] == ["食物过敏"]
    assert labels["main_need"] == ["情绪与压力易失控"]
    assert labels["expected_change"] == ["节律与情绪更稳定"]
    assert labels["diet_pattern"] == "高碳饮食"
    assert labels["sleep_problem"] == ["睡眠时间很不固定"]
    assert labels["sleep_quality"] == "还可以"

    portrait = await admin_client.get("/dashboard/population-portrait")
    assert portrait.status_code == 200
    assert portrait.json()["total_users"] == total

    members = await admin_client.get(
        "/dashboard/population-portrait/members",
        params={"key": "age", "bucket": "55 岁以上"},
    )
    assert members.status_code == 200
    assert service_case.id in {item["id"] for item in members.json()}

    need_members = await admin_client.get(
        "/dashboard/population-portrait/members",
        params={"key": "main_need", "bucket": "情绪与压力易失控"},
    )
    assert need_members.status_code == 200
    assert service_case.id in {item["id"] for item in need_members.json()}


@pytest.mark.asyncio
async def test_population_portrait_uses_latest_non_deleted_case_per_user_and_excludes_refund(
    db_session,
):
    baseline_total, _ = await _compute_case_portrait_labels(db_session)
    now = datetime.now(timezone.utc)

    # 同一用户多期服务只取最新一张。
    user_latest, old_case = await _create_portrait_case(
        db_session,
        created_at=now - timedelta(days=20),
        status=ServiceCaseStatus.COMPLETED,
    )
    _, latest_case = await _create_portrait_case(
        db_session,
        user=user_latest,
        created_at=now - timedelta(days=10),
    )

    # 必须先取最新再判断退款：最新已退款时，不得回退到旧的未退款服务单。
    user_refunded, old_non_refunded = await _create_portrait_case(
        db_session,
        created_at=now - timedelta(days=20),
    )
    _, latest_refunded = await _create_portrait_case(
        db_session,
        user=user_refunded,
        created_at=now - timedelta(days=10),
        status=ServiceCaseStatus.REFUNDED,
    )

    # 退款待确认尚未成为退款状态，仍然纳入。
    _, refund_pending = await _create_portrait_case(
        db_session,
        refund_pending_confirmation=True,
    )

    # 已毕业但未退款仍然纳入。
    _, completed = await _create_portrait_case(
        db_session,
        status=ServiceCaseStatus.COMPLETED,
    )

    deleted_at = now - timedelta(days=1)
    deleted_user = User(name="已删除画像用户", deleted_at=deleted_at)
    db_session.add(deleted_user)
    await db_session.flush()
    _, deleted_user_case = await _create_portrait_case(db_session, user=deleted_user)
    _, deleted_case = await _create_portrait_case(db_session, deleted_at=deleted_at)

    total, labels_by_case = await _compute_case_portrait_labels(db_session)
    assert total == baseline_total + 3
    assert latest_case.id in labels_by_case
    assert refund_pending.id in labels_by_case
    assert completed.id in labels_by_case
    for excluded in (
        old_case,
        old_non_refunded,
        latest_refunded,
        deleted_user_case,
        deleted_case,
    ):
        assert excluded.id not in labels_by_case


@pytest.mark.asyncio
async def test_population_portrait_members_accepts_city_and_province_keys(admin_client):
    for key, bucket in (("city", "杭州"), ("province", "浙江")):
        resp = await admin_client.get(
            "/dashboard/population-portrait/members", params={"key": key, "bucket": bucket}
        )
        assert resp.status_code == 200
