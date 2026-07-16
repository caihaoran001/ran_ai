from __future__ import annotations

from app.models.service_case import ServiceCase
from app.services.health_taxonomy import (
    has_confirmed_persistent_health_fact,
    health_profile_dimension_labels,
    health_profile_supports_risk,
    merge_health_profiles,
    normalize_health_profile,
)
from app.services.intervention_service import merge_case_insight, normalize_insight


def _case() -> ServiceCase:
    return ServiceCase(case_no="TEST-HEALTH", group_id="g-health", questionnaire_token="t-health")


def test_health_profile_dimensions_are_orthogonal_and_deduplicated():
    profile = normalize_health_profile({
        "conditions": [
            {"code": "hypertension", "status": "confirmed", "evidence": "我有高血压", "source": "chat_message"},
            {"code": "gallstone", "status": "confirmed", "evidence": "我有胆结石", "source": "chat_message"},
            {"code": "hyperthyroidism", "status": "pending", "evidence": "可能亚临床甲亢", "source": "chat_message"},
        ],
        "medications": [
            {
                "category_code": "antihypertensive",
                "name": "降压药",
                "long_term": True,
                "status": "active",
                "evidence": "每天吃降压药",
                "source": "chat_message",
            }
        ],
    })

    labels = health_profile_dimension_labels(profile)
    assert labels["health_domain"] == ["心血管", "肝胆"]
    assert labels["health_condition"] == ["高血压", "胆结石"]
    assert labels["medication_category"] == ["降压药"]
    assert labels["pending_condition"] == ["甲状腺功能亢进"]


def test_health_profile_merge_replaces_same_fact_instead_of_appending_duplicates():
    existing = {
        "conditions": [
            {"code": "hyperthyroidism", "status": "pending", "evidence": "可能甲亢", "source": "chat_message"}
        ]
    }
    incoming = {
        "conditions": [
            {"code": "hyperthyroidism", "status": "confirmed", "evidence": "医院确诊甲亢", "source": "chat_message"}
        ]
    }

    merged = merge_health_profiles(existing, incoming)
    assert merged["conditions"] == [
        {"code": "hyperthyroidism", "status": "confirmed", "evidence": "医院确诊甲亢", "source": "chat_message"}
    ]


def test_elevated_blood_pressure_is_visible_but_not_a_chronic_disease_diagnosis():
    profile = normalize_health_profile({
        "conditions": [{
            "code": "elevated_blood_pressure",
            "status": "confirmed",
            "evidence": "就是血压有点高",
            "source": "chat_message",
        }]
    })

    assert health_profile_dimension_labels(profile)["health_condition"] == ["血压偏高"]
    assert not has_confirmed_persistent_health_fact(profile)
    assert not health_profile_supports_risk(profile, "chronic_disease")


def test_pending_condition_cannot_persist_as_high_risk():
    case = _case()
    merge_case_insight(case, {
        "risk_flags": [
            {
                "code": "chronic_disease",
                "level": "high",
                "reason": "可能存在甲亢",
                "evidence": "可能亚临床甲亢",
                "source": "chat_message",
            }
        ],
        "health_profile": {
            "conditions": [
                {"code": "hyperthyroidism", "status": "pending", "evidence": "可能亚临床甲亢", "source": "chat_message"}
            ]
        },
    })

    assert case.risk_level == "medium"
    assert case.risk_flags[0]["level"] == "medium"
    assert not has_confirmed_persistent_health_fact(case.health_profile)


def test_confirmed_condition_persists_high_risk_and_health_profile():
    case = _case()
    merge_case_insight(case, {
        "risk_flags": [
            {
                "code": "chronic_disease",
                "level": "high",
                "reason": "明确高血压",
                "evidence": "我有高血压",
                "source": "chat_message",
            }
        ],
        "health_profile": {
            "conditions": [
                {"code": "hypertension", "status": "confirmed", "evidence": "我有高血压", "source": "chat_message"}
            ]
        },
    })

    assert case.risk_level == "high"
    assert health_profile_dimension_labels(case.health_profile)["health_condition"] == ["高血压"]


def test_acute_crisis_hands_off_without_becoming_persistent_high_risk():
    case = _case()
    insight = normalize_insight({
        "requires_human_intervention": True,
        "risk_flags": [
            {
                "code": "depression",
                "level": "high",
                "reason": "用户表达当下严重心理危机",
                "evidence": "我现在控制不住伤害自己的想法",
                "source": "chat_message",
            }
        ],
        "emotion_signal": {
            "detected": True,
            "level": "high",
            "reason": "本次消息必须立即转人工处理",
            "evidence": "我现在控制不住伤害自己的想法",
            "source": "chat_message",
        },
        "health_profile": {
            "conditions": [
                {
                    "code": "hypertension",
                    "status": "confirmed",
                    "evidence": "我有高血压",
                    "source": "chat_message",
                }
            ],
            "medications": [],
            "special_stages": [],
            "allergies": [],
        },
    }, source="chat_message", content="我现在控制不住伤害自己的想法")
    merge_case_insight(case, insight)

    assert insight["requires_human_intervention"] is True
    assert case.risk_level == "medium"
    assert case.risk_flags[0]["level"] == "medium"
    assert any(reason["level"] == "high" for reason in case.intervention_reasons)
    assert health_profile_dimension_labels(case.health_profile)["health_condition"] == ["高血压"]
