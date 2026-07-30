from __future__ import annotations

import re
from typing import Any


CUSTOM_ANSWER_LABEL = "其他（用户补充）"

# 身高体重缺失导致算不出安全减重空间时的带位哨兵值。这是后台口径：
# dashboard 分类统计和人群画像都按它过滤未采集用户，改字面量会静默破坏那些统计。
UNRESOLVED_BAND = "需补充身高体重后判断"

BAND_A = "A带｜修复塑形"

# 保护性下限的上调档位。客户 V2.1 §6.5 只说了"可将该下限上调"，没有给幅度，
# 这套档位是我们定的可审计规则：以 BMI 20 自动复核线为起点，每命中一类风险抬 0.5 个
# BMI 单位，累计封顶 1.5 个（即最高按 BMI 21.5 计）。
#
# 为什么是 0.5 / 封顶 1.5：165cm 的用户从 BMI 20 抬到 21.5 是 54.5kg → 58.5kg，
# 下限抬高 4kg，足以把 safe_room 8kg（C带）压到 4kg（B带）——有实际约束力。
# 再往上抬会让多数有既往病史的用户直接掉进 A 带；风险该不该让人停止减重是人工复核
# 要判断的事，不该由上调档位的算术副作用替运营做掉。
PROTECTED_LOWER_BASE_BMI = 20.0
PROTECTED_LOWER_STEP_BMI = 0.5
PROTECTED_LOWER_MAX_UPLIFT_BMI = 1.5

# 每一档对应的用户不可见依据文案，用于 band_basis 审计。
PROTECTED_LOWER_UPLIFT_FACTORS = {
    "muscle_insufficiency": "可靠体脂/肌肉数据显示肌肉量明显不足",
    "disease_or_medication_limit": "存在重大疾病或长期用药限制",
    "recovery_risk": "月经或恢复力风险",
}


def structured_answer_values(
    fields: list[dict[str, Any]],
    answers: dict[str, Any],
    *title_parts: str,
) -> list[str]:
    """Read one questionnaire answer without splitting punctuation inside an option.

    Historical checkbox submissions use ``||`` between selected options, while
    radio answers and newer payloads may be a string or a list.  Chinese commas
    such as ``、`` are part of several option labels and therefore must never be
    treated as separators here.  Free-form ``其他`` values are collapsed into a
    stable bucket so population distributions do not expose or fragment user text.
    """

    for field in fields:
        key = str(field.get("key") or field.get("id") or "")
        title = str(field.get("title") or field.get("label") or "")
        if key not in answers or not all(part in title for part in title_parts):
            continue

        raw = answers.get(key)
        if isinstance(raw, list):
            values = [str(item).strip() for item in raw if str(item).strip()]
        else:
            text = str(raw or "").strip()
            values = [item.strip() for item in text.split("||") if item.strip()] if text else []

        options = {
            str(option).strip() for option in field.get("options", []) if str(option).strip()
        }
        normalized: list[str] = []
        for value in values:
            if value == "其他" or value.startswith(("其他：", "其他:")):
                label = CUSTOM_ANSWER_LABEL
            elif options and value not in options:
                label = CUSTOM_ANSWER_LABEL
            else:
                label = value
            if label and label not in normalized:
                normalized.append(label)
        return normalized
    return []


def build_intake_fact_summary(
    fields: list[dict[str, Any]],
    answers: dict[str, Any],
    safety_factors: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Extract deterministic facts shared by profile and document generation.

    ``safety_factors`` 是安全因子抽取阶段的结构化结论（见
    ``intake_generation._extract_safety_factors``）。它决定保护性下限上调多少，
    以及是否因红色风险直接进入 A 带。缺省为 None 时退化成纯 BMI 20 下限，
    行为与历史一致。
    """

    title_by_key = {
        str(field.get("key") or field.get("id") or ""): str(field.get("title") or field.get("label") or "")
        for field in fields
    }

    def value_for(*title_parts: str) -> str:
        for key, title in title_by_key.items():
            if key in answers and all(part in title for part in title_parts):
                return _answer_text(answers.get(key))
        return ""

    legal_name = value_for("真实姓名") or value_for("姓名")
    preferred_name = value_for("抖音昵称") or value_for("常用称呼") or value_for("称呼")
    gender = value_for("性别")
    age = value_for("年龄")
    city = value_for("所在的城市") or value_for("城市")
    height_raw = value_for("身高")
    weight_raw = value_for("当前体重") or value_for("体重")
    waist = value_for("腰围")
    bodyfat = value_for("体脂率")
    health_note = value_for("健康情况") or value_for("病史")
    main_needs = value_for("最想先解决")
    day28_expectation = value_for("28 天后") or value_for("28天后")
    adherence_worry = value_for("坚持不下来")
    pressure_reaction = value_for("压力大时")
    diet_pattern = value_for("长期饮食模式")

    height = _normalize_height_cm(_first_number(height_raw))
    weight = _first_number(weight_raw)
    bmi = _calculate_bmi(height, weight)
    safe_reduction = _safe_reduction_facts(height, weight, safety_factors)
    safety_review = _safety_review_required(safety_factors)
    weight_loss_band = _management_band_from_safe_room(
        safe_reduction["safe_reduction_room_kg"]
    )

    return {
        "name": legal_name or "未提供",
        "legal_name": legal_name or "未提供",
        "preferred_name": preferred_name or legal_name or "未提供",
        "recipient_name": _recipient_name(preferred_name or legal_name, legal_name, gender),
        "gender": gender or "未提供",
        "age": age or "未提供",
        "city": city or "未提供",
        "height_cm": _format_number(height) if height else "未提供",
        "weight_kg": _format_number(weight) if weight else "未提供",
        "bmi": f"{bmi:.1f}" if bmi else "暂无数据",
        "weight_loss_band": weight_loss_band,
        # 四带引擎的确定性内部事实。管理带只读 safe_reduction_room_kg 的精确边界，
        # 模型、BMI标签、腰腹体感和用户期待斤数都无权改带。
        "baseline_weight_kg": _format_safe_measurement(
            safe_reduction["baseline_weight_kg"]
        ),
        # 入营问卷当前只有最新可信体重，没有连续3天晨重均值；按客户规则标为暂定基线。
        "baseline_weight_provisional": safe_reduction["baseline_weight_kg"] is not None,
        "absolute_low_weight_boundary_kg": _format_safe_measurement(
            safe_reduction["absolute_low_weight_boundary_kg"]
        ),
        "automatic_review_weight_kg": _format_safe_measurement(
            safe_reduction["automatic_review_weight_kg"]
        ),
        "protected_lower_weight_kg": _format_safe_measurement(
            safe_reduction["protected_lower_weight_kg"]
        ),
        # 下限被抬高了多少、依据是什么，必须可复算可审计：运营看到带位比预期低时，
        # 要能立刻分辨是本来空间就小，还是被风险因子把下限抬上去了。
        "protected_lower_uplift_bmi": _format_safe_measurement(
            safe_reduction["protected_lower_uplift_bmi"]
        ),
        "protected_lower_uplift_reasons": safe_reduction["protected_lower_uplift_reasons"],
        "safety_review_required": safety_review,
        "safety_review_reasons": _safety_review_reasons(safety_factors) if safety_review else [],
        "safe_reduction_room_kg": _format_safe_measurement(
            safe_reduction["safe_reduction_room_kg"]
        ),
        "weight_loss_room_exhausted": (
            "暂无数据"
            if safe_reduction["room_exhausted"] is None
            else ("是" if safe_reduction["room_exhausted"] else "否")
        ),
        "day28_goal_cap_kg": _format_safe_measurement(
            safe_reduction["day28_goal_cap_kg"]
        ),
        "waist": waist or "未提供",
        "bodyfat": bodyfat or "未提供",
        "health_note": health_note or "未提供",
        "main_needs": main_needs or "未提供",
        "day28_expectation": day28_expectation or "未提供",
        "adherence_worry": adherence_worry or "未提供",
        "pressure_reaction": pressure_reaction or "未提供",
        "diet_pattern": diet_pattern or "未提供",
    }


def sanitize_profile_json(profile: dict[str, Any], facts: dict[str, Any] | None) -> dict[str, Any]:
    if not facts:
        return profile
    overview = profile.setdefault("overview", {})
    if isinstance(overview, dict):
        overview["gender_age"] = _join_non_empty([facts.get("gender"), facts.get("age")], " / ")
        overview["height_weight"] = _join_non_empty(
            [_with_unit(facts.get("height_cm"), "cm"), _with_unit(facts.get("weight_kg"), "kg")],
            " / ",
        )
        overview["bmi"] = str(facts.get("bmi") or "暂无数据")
        overview["waist"] = str(facts.get("waist") or "未提供")
        overview["weight_loss_band"] = str(facts.get("weight_loss_band") or "需后续补充")

    return profile


def _answer_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "、".join(str(item).strip() for item in value if str(item).strip())
    return str(value).replace("||", "、").strip()


def _first_number(value: str) -> float | None:
    match = re.search(r"\d+(?:\.\d+)?", value or "")
    if not match:
        return None
    return float(match.group())


def _normalize_height_cm(value: float | None) -> float | None:
    """身高单位归一：问卷标题写了 cm，用户照样会填「1.65」。

    1.2–2.3 之间的数不可能是厘米，按米换算。体重的斤/公斤没有这种不重叠区间
    （110 既可能是斤也可能是大基数公斤），不可判就不猜，留给人工复核。
    不做这一步的话，1.65 会让 BMI 变成 20 万，并被 _replace_bmi_text 覆写到全文每一处。
    """

    if value is None or value <= 0:
        return None
    if 1.2 <= value <= 2.3:
        return value * 100
    return value


def _protected_lower_uplift(
    safety_factors: dict[str, Any] | None,
) -> tuple[float, list[str]]:
    """按命中的风险类别累加保护性下限的上调幅度（BMI 单位）。

    返回 (上调的 BMI 单位数, 依据文案)。风险类别由模型结构化输出，不在这里做
    关键词判断；这里只负责把布尔因子换算成确定性的、可复算的公斤线。
    """

    if not isinstance(safety_factors, dict):
        return 0.0, []
    reasons: list[str] = []
    steps = 0
    for key, label in PROTECTED_LOWER_UPLIFT_FACTORS.items():
        if safety_factors.get(key) is True:
            steps += 1
            reasons.append(label)
    uplift = min(steps * PROTECTED_LOWER_STEP_BMI, PROTECTED_LOWER_MAX_UPLIFT_BMI)
    return uplift, reasons


def _doctor_specified_floor(safety_factors: dict[str, Any] | None) -> float | None:
    """医生明确给出的体重下限优先于档位换算，但只接受可解析的正数。"""

    if not isinstance(safety_factors, dict):
        return None
    raw = safety_factors.get("doctor_specified_lower_weight_kg")
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _safe_reduction_facts(
    height_cm: float | None,
    weight_kg: float | None,
    safety_factors: dict[str, Any] | None = None,
) -> dict[str, float | bool | list[str] | None]:
    """按 V2.1 计算四带唯一数值主轴及28天自动上限。

    入营问卷暂时只有最新可信体重，因此 baseline 直接使用当前体重并在事实摘要中
    标记 provisional。protected_lower 以 BMI 20 自动复核线为起点，再按客户 §6.5
    的四类证据上调：肌肉量明显不足、重大疾病或用药、月经/恢复力风险由模型结构化
    输出的布尔因子驱动，医生明确的公斤线直接取用并覆盖档位结果（取更保守的一个）。
    """

    if not height_cm or not weight_kg or height_cm <= 0:
        return {
            "baseline_weight_kg": None,
            "absolute_low_weight_boundary_kg": None,
            "automatic_review_weight_kg": None,
            "protected_lower_weight_kg": None,
            "protected_lower_uplift_bmi": None,
            "protected_lower_uplift_reasons": [],
            "safe_reduction_room_kg": None,
            "room_exhausted": None,
            "day28_goal_cap_kg": None,
        }
    height_m = height_cm / 100
    height_sq = height_m * height_m
    absolute_low = 18.5 * height_sq
    automatic_review = PROTECTED_LOWER_BASE_BMI * height_sq
    uplift_bmi, uplift_reasons = _protected_lower_uplift(safety_factors)
    protected_lower = (PROTECTED_LOWER_BASE_BMI + uplift_bmi) * height_sq
    doctor_floor = _doctor_specified_floor(safety_factors)
    if doctor_floor is not None and doctor_floor > protected_lower:
        protected_lower = doctor_floor
        uplift_reasons = [*uplift_reasons, "医生明确的体重下限"]
    safe_room = max(0.0, weight_kg - protected_lower)
    goal_cap = min(weight_kg * 0.05, 4.0, safe_room)
    return {
        "baseline_weight_kg": weight_kg,
        "absolute_low_weight_boundary_kg": absolute_low,
        # 自动复核线始终是未上调的 BMI 20 线，上调后的下限单独记在 protected_lower。
        "automatic_review_weight_kg": automatic_review,
        "protected_lower_weight_kg": protected_lower,
        "protected_lower_uplift_bmi": uplift_bmi,
        "protected_lower_uplift_reasons": uplift_reasons,
        "safe_reduction_room_kg": safe_room,
        "room_exhausted": safe_room <= 0,
        "day28_goal_cap_kg": goal_cap,
    }


def _calculate_bmi(height_cm: float | None, weight_kg: float | None) -> float | None:
    if not height_cm or not weight_kg or height_cm <= 0:
        return None
    height_m = height_cm / 100
    return weight_kg / (height_m * height_m)


def _safety_review_reasons(safety_factors: dict[str, Any] | None) -> list[str]:
    if not isinstance(safety_factors, dict):
        return []
    raw = safety_factors.get("safety_review_reasons")
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()][:3]


def _safety_review_required(safety_factors: dict[str, Any] | None) -> bool:
    """问卷是否暴露了必须人工复核的安全条件。

    注意它**不改带位**：A-D 带始终按安全减重空间的数值边界判定。红色风险、孕哺、
    青少年等条件通过强制人工复核起作用——文档内容照常按带位生成，但会被扣住不
    自动发送，由运营确认后再发。
    """

    if not isinstance(safety_factors, dict):
        return False
    return safety_factors.get("safety_review_required") is True


def _management_band_from_safe_room(safe_room_kg: float | None) -> str:
    """安全减重空间是 A/B/C/D 的唯一数值分带主轴。

    安全条件（红风险、孕哺、青少年等）不参与分带，只触发人工复核；上调后的
    保护性下限已经把这些风险折算进 safe_room，带位本身不需要再被绕过。
    """

    if safe_room_kg is None:
        return UNRESOLVED_BAND
    if safe_room_kg < 2.5:
        return BAND_A
    if safe_room_kg < 5:
        return "B带｜轻盈启动"
    if safe_room_kg < 10:
        return "C带｜明确减脂"
    return "D带｜系统减重"


# 客户分型标签：单标签、互斥、带优先级。这是在两个已有大模型结构化产出之上
# （风控 LLM 判 risk_level、画像 LLM 定 weight_loss_band）做确定性投影，
# 不是关键词硬编码规则；用于运营快速“定调”当前用户。
CUSTOMER_TYPE_DISEASE = "疾病管理型"
CUSTOMER_TYPE_WEIGHT_LOSS = "减重攻坚型"
CUSTOMER_TYPE_HEALTH_INVEST = "健康投资型"
CUSTOMER_TYPES = (
    CUSTOMER_TYPE_DISEASE,
    CUSTOMER_TYPE_WEIGHT_LOSS,
    CUSTOMER_TYPE_HEALTH_INVEST,
)


def derive_customer_type(risk_level: str | None, weight_loss_band: str | None) -> str:
    """按优先级把用户投影到唯一客户分型。

    优先级：疾病管理型(高风险) > 减重攻坚型(C/D 带) > 健康投资型(兜底)。
    带位缺失且非高风险时兜底为健康投资型，避免出现空标签。
    """
    if (risk_level or "").strip().lower() == "high":
        return CUSTOMER_TYPE_DISEASE
    band = weight_loss_band or ""
    if "C带" in band or "D带" in band:
        return CUSTOMER_TYPE_WEIGHT_LOSS
    return CUSTOMER_TYPE_HEALTH_INVEST


def weight_loss_band_from_profile(profile_json: Any) -> str | None:
    """从画像 JSON 读减重带（顶层优先，回落 overview）。"""
    if not isinstance(profile_json, dict):
        return None
    band = profile_json.get("weight_loss_band")
    if not band:
        overview = profile_json.get("overview")
        if isinstance(overview, dict):
            band = overview.get("weight_loss_band")
    return band if isinstance(band, str) and band.strip() else None


def customer_type_for_profile(profile_json: Any, risk_level: str | None) -> str:
    """便捷组合：从画像 JSON + 服务单风险等级推导客户分型。"""
    return derive_customer_type(risk_level, weight_loss_band_from_profile(profile_json))


def _format_number(value: float) -> str:
    return str(int(value)) if value.is_integer() else f"{value:g}"


def _format_safe_measurement(value: float | bool | None) -> str:
    """内部体重计算保留两位精度，避免2.49显示成2.5却仍落A带的审计歧义。"""

    if value is None or isinstance(value, bool):
        return "暂无数据"
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _join_non_empty(values: list[Any], sep: str) -> str:
    cleaned = [str(value) for value in values if value and str(value) not in {"未提供", "暂无数据"}]
    return sep.join(cleaned) if cleaned else "未提供"


def _with_unit(value: Any, unit: str) -> str:
    if not value or str(value) in {"未提供", "暂无数据"}:
        return ""
    text = str(value)
    return text if text.endswith(unit) else f"{text}{unit}"


def _recipient_name(preferred_name: str, legal_name: str, gender: str) -> str:
    preferred = (preferred_name or "").strip()
    legal = (legal_name or "").strip()
    suffix = _gender_suffix(gender)
    if preferred and legal and preferred != legal:
        base = f"{preferred}{suffix}"
        return f"{base}（{legal}）"
    if preferred:
        return f"{preferred}{suffix}"
    if legal:
        return f"{legal}{suffix}"
    return "未提供"


def _gender_suffix(gender: str) -> str:
    normalized = (gender or "").strip()
    if normalized == "女":
        return " 女士"
    if normalized == "男":
        return " 先生"
    return ""
