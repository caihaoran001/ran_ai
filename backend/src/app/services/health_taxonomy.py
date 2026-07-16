from __future__ import annotations

from typing import Any


HEALTH_DOMAIN_LABELS: dict[str, str] = {
    "cardiovascular": "心血管",
    "glucose_metabolism": "血糖代谢",
    "lipid_metabolism": "血脂代谢",
    "thyroid": "甲状腺",
    "reproductive_endocrine": "生殖内分泌",
    "hepatobiliary": "肝胆",
    "renal_urinary": "肾脏泌尿",
    "mental_health": "精神心理",
}

# condition code -> (domain code, display label)
HEALTH_CONDITIONS: dict[str, tuple[str, str]] = {
    "hypertension": ("cardiovascular", "高血压"),
    "elevated_blood_pressure": ("cardiovascular", "血压偏高"),
    "type2_diabetes": ("glucose_metabolism", "2型糖尿病"),
    "diabetes_unspecified": ("glucose_metabolism", "糖尿病（类型未明确）"),
    "dyslipidemia": ("lipid_metabolism", "高血脂/血脂异常"),
    "hypothyroidism": ("thyroid", "甲状腺功能减退"),
    "hyperthyroidism": ("thyroid", "甲状腺功能亢进"),
    "thyroid_nodule": ("thyroid", "甲状腺结节"),
    "post_thyroidectomy": ("thyroid", "甲状腺术后"),
    "pcos": ("reproductive_endocrine", "多囊卵巢综合征"),
    "hyperprolactinemia": ("reproductive_endocrine", "高泌乳素血症"),
    "fatty_liver": ("hepatobiliary", "脂肪肝"),
    "cholecystitis": ("hepatobiliary", "胆囊炎"),
    "gallstone": ("hepatobiliary", "胆结石"),
    "liver_condition_unspecified": ("hepatobiliary", "肝脏情况未明确"),
    "renal_impairment": ("renal_urinary", "肾功能异常"),
    "kidney_stone": ("renal_urinary", "肾结石"),
    "depression_diagnosis": ("mental_health", "明确抑郁诊断"),
}

MEDICATION_CATEGORY_LABELS: dict[str, str] = {
    "thyroid_hormone": "甲状腺激素用药",
    "antihypertensive": "降压药",
    "glucose_lowering": "降糖/胰岛素抵抗用药",
    "lipid_lowering": "降脂药",
    "prolactin_lowering": "降泌乳素用药",
    "endocrine_therapy": "内分泌治疗用药",
    "other_prescription": "其他处方药",
}

SPECIAL_STAGE_LABELS: dict[str, str] = {
    "pregnancy": "已确认孕期",
    "lactation": "哺乳期",
    "postoperative_recovery": "近期术后恢复",
}

ALLERGY_CATEGORY_LABELS: dict[str, str] = {
    "drug_allergy": "药物过敏",
    "food_allergy": "食物过敏",
    "product_ingredient_allergy": "产品成分过敏",
    "other_allergy": "其他过敏",
}

CONDITION_STATUSES = frozenset({"confirmed", "pending", "resolved"})
MEDICATION_STATUSES = frozenset({"active", "stopped", "unknown"})
STAGE_STATUSES = frozenset({"active", "resolved"})
ALLERGY_STATUSES = frozenset({"confirmed", "pending"})
VALID_SOURCES = frozenset({"chat_message", "questionnaire", "checkin"})


def empty_health_profile() -> dict[str, list[dict[str, Any]]]:
    return {"conditions": [], "medications": [], "special_stages": [], "allergies": []}


def normalize_health_profile(value: Any, *, source: str = "") -> dict[str, list[dict[str, Any]]]:
    raw = value if isinstance(value, dict) else {}
    return {
        "conditions": _normalize_conditions(raw.get("conditions"), source=source),
        "medications": _normalize_medications(raw.get("medications"), source=source),
        "special_stages": _normalize_special_stages(raw.get("special_stages"), source=source),
        "allergies": _normalize_allergies(raw.get("allergies"), source=source),
    }


def merge_health_profiles(existing: Any, incoming: Any) -> dict[str, list[dict[str, Any]]]:
    current = normalize_health_profile(existing)
    update = normalize_health_profile(incoming)
    return {
        "conditions": _merge_by_key(current["conditions"], update["conditions"], lambda item: item["code"]),
        "medications": _merge_by_key(
            current["medications"],
            update["medications"],
            lambda item: (item["category_code"], item["name"].casefold()),
        ),
        "special_stages": _merge_by_key(
            current["special_stages"], update["special_stages"], lambda item: item["code"]
        ),
        "allergies": _merge_by_key(
            current["allergies"],
            update["allergies"],
            lambda item: (item["category_code"], item["substance"].casefold()),
        ),
    }


def health_profile_dimension_labels(profile: Any) -> dict[str, list[str]]:
    normalized = normalize_health_profile(profile)
    confirmed_conditions = [
        item for item in normalized["conditions"] if item["status"] == "confirmed"
    ]
    pending_conditions = [
        item for item in normalized["conditions"] if item["status"] == "pending"
    ]
    active_medications = [
        item
        for item in normalized["medications"]
        if item["status"] != "stopped" and item["long_term"]
    ]
    active_stages = [
        item for item in normalized["special_stages"] if item["status"] == "active"
    ]
    confirmed_allergies = [
        item for item in normalized["allergies"] if item["status"] == "confirmed"
    ]
    return {
        "health_domain": _unique_labels(
            HEALTH_DOMAIN_LABELS[HEALTH_CONDITIONS[item["code"]][0]]
            for item in confirmed_conditions
        ),
        "health_condition": _unique_labels(
            HEALTH_CONDITIONS[item["code"]][1] for item in confirmed_conditions
        ),
        "medication_category": _unique_labels(
            MEDICATION_CATEGORY_LABELS[item["category_code"]] for item in active_medications
        ),
        "special_stage": _unique_labels(
            SPECIAL_STAGE_LABELS[item["code"]] for item in active_stages
        ),
        "pending_condition": _unique_labels(
            HEALTH_CONDITIONS[item["code"]][1] for item in pending_conditions
        ),
        "allergy_category": _unique_labels(
            ALLERGY_CATEGORY_LABELS[item["category_code"]] for item in confirmed_allergies
        ),
    }


def has_confirmed_persistent_health_fact(profile: Any) -> bool:
    normalized = normalize_health_profile(profile)
    return bool(
        any(
            item["status"] == "confirmed" and item["code"] != "elevated_blood_pressure"
            for item in normalized["conditions"]
        )
        or any(item["long_term"] and item["status"] != "stopped" for item in normalized["medications"])
        or any(
            item["code"] in {"pregnancy", "lactation"} and item["status"] == "active"
            for item in normalized["special_stages"]
        )
    )


def health_profile_supports_risk(profile: Any, risk_code: str) -> bool:
    """Return whether the profile contains the matching durable fact for a high-risk code."""
    normalized = normalize_health_profile(profile)
    confirmed_codes = {
        item["code"] for item in normalized["conditions"] if item["status"] == "confirmed"
    }
    active_long_term_medications = [
        item
        for item in normalized["medications"]
        if item["long_term"] and item["status"] != "stopped"
    ]
    active_stages = {
        item["code"] for item in normalized["special_stages"] if item["status"] == "active"
    }

    if risk_code == "chronic_disease":
        return bool(confirmed_codes - {"elevated_blood_pressure"})
    if risk_code == "depression":
        return "depression_diagnosis" in confirmed_codes
    if risk_code == "long_term_medication":
        return bool(active_long_term_medications)
    if risk_code == "hormone_medication":
        return any(
            item["category_code"] in {
                "thyroid_hormone",
                "prolactin_lowering",
                "endocrine_therapy",
            }
            for item in active_long_term_medications
        )
    if risk_code == "liver_kidney":
        return any(
            HEALTH_CONDITIONS[code][0] in {"hepatobiliary", "renal_urinary"}
            for code in confirmed_codes
        )
    if risk_code == "stone":
        return bool(confirmed_codes & {"gallstone", "kidney_stone"})
    if risk_code in {"pregnancy", "lactation"}:
        return risk_code in active_stages
    # Age and explicit severe test abnormalities are not represented by this
    # health-profile schema, so their existing evidence-based high-risk path remains unchanged.
    return True


def _normalize_conditions(value: Any, *, source: str) -> list[dict[str, Any]]:
    result = []
    for item in _dict_items(value):
        code = _string(item.get("code"))
        if code not in HEALTH_CONDITIONS:
            continue
        status = _string(item.get("status")) or "confirmed"
        if status not in CONDITION_STATUSES:
            status = "pending"
        evidence = _string(item.get("evidence"))
        if not evidence:
            continue
        result.append({
            "code": code,
            "status": status,
            "evidence": evidence,
            "source": _source(item.get("source"), source),
        })
    return _dedupe(result, lambda item: item["code"])


def _normalize_medications(value: Any, *, source: str) -> list[dict[str, Any]]:
    result = []
    for item in _dict_items(value):
        category = _string(item.get("category_code"))
        name = _string(item.get("name"))
        evidence = _string(item.get("evidence"))
        if category not in MEDICATION_CATEGORY_LABELS or not name or not evidence:
            continue
        status = _string(item.get("status")) or "unknown"
        if status not in MEDICATION_STATUSES:
            status = "unknown"
        result.append({
            "category_code": category,
            "name": name,
            "long_term": bool(item.get("long_term")),
            "status": status,
            "evidence": evidence,
            "source": _source(item.get("source"), source),
        })
    return _dedupe(result, lambda item: (item["category_code"], item["name"].casefold()))


def _normalize_special_stages(value: Any, *, source: str) -> list[dict[str, Any]]:
    result = []
    for item in _dict_items(value):
        code = _string(item.get("code"))
        evidence = _string(item.get("evidence"))
        if code not in SPECIAL_STAGE_LABELS or not evidence:
            continue
        status = _string(item.get("status")) or "active"
        if status not in STAGE_STATUSES:
            status = "active"
        result.append({
            "code": code,
            "status": status,
            "evidence": evidence,
            "source": _source(item.get("source"), source),
        })
    return _dedupe(result, lambda item: item["code"])


def _normalize_allergies(value: Any, *, source: str) -> list[dict[str, Any]]:
    result = []
    for item in _dict_items(value):
        category = _string(item.get("category_code"))
        substance = _string(item.get("substance"))
        evidence = _string(item.get("evidence"))
        if category not in ALLERGY_CATEGORY_LABELS or not substance or not evidence:
            continue
        status = _string(item.get("status")) or "confirmed"
        if status not in ALLERGY_STATUSES:
            status = "pending"
        result.append({
            "category_code": category,
            "substance": substance,
            "status": status,
            "evidence": evidence,
            "source": _source(item.get("source"), source),
        })
    return _dedupe(result, lambda item: (item["category_code"], item["substance"].casefold()))


def _merge_by_key(existing: list[dict], incoming: list[dict], key_fn: Any) -> list[dict]:
    merged = {key_fn(item): item for item in existing}
    for item in incoming:
        merged[key_fn(item)] = item
    return list(merged.values())


def _dedupe(items: list[dict], key_fn: Any) -> list[dict]:
    deduped: dict[Any, dict] = {}
    for item in items:
        deduped[key_fn(item)] = item
    return list(deduped.values())


def _unique_labels(values: Any) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _dict_items(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _source(value: Any, fallback: str) -> str:
    candidate = _string(value)
    return candidate if candidate in VALID_SOURCES else fallback


def _string(value: Any) -> str:
    return str(value or "").strip()
