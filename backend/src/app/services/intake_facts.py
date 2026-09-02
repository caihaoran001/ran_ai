from __future__ import annotations

import re
from typing import Any

from app.services.body_metrics import has_explicit_inch, has_explicit_jin, has_explicit_lbs, parse_waist_cm

CUSTOM_ANSWER_LABEL = "其他（用户补充）"

# 身高体重缺失导致算不出安全减重空间时的带位哨兵值。这是后台口径：
# dashboard 分类统计和人群画像都按它过滤未采集用户，改字面量会静默破坏那些统计。
UNRESOLVED_BAND = "需补充身高体重后判断"

BAND_A = "A带｜修复塑形"
# 会给减重数字的三个带（A 带不给数字，哨兵值更不给）。
MANAGEMENT_BANDS_WITH_NUMBERS = (
    "B带｜轻盈启动",
    "C带｜明确减脂",
    "D带｜系统减重",
)

# 2026-08-25 新增、但此前被 operator_only 全量过滤掉的 8 道交付画像题。
# 这里做的是「白名单原答案投影」，不是第三次 LLM 总结，也不把全部运营字段放开。
DOCUMENT_CONTEXT_QUESTION_KEYS = {
    "CHECKBOX17776518759860wlspec": "weight_loss_history",
    "RADIO17776518759861occnew": "occupation_identity",
    "SIMPLE17776518759862jobdet": "job_detail",
    "RADIO17776518759863rolelvl": "work_responsibility",
    "CHECKBOX17776518759864famrole": "family_roles",
    "RADIO17776518759865neatlv": "daily_activity_level",
    "CHECKBOX17776518759866loadtop": "main_energy_loads",
    "CHECKBOX17776518759867statval": "body_improvement_values",
}

_WEIGHT_RELATED_DAY28_EXPECTATIONS = {
    "身体更轻盈、腰腹部维度缩减",
    "体重/腰围出现让我满意的变化（可在“其他”填写数字目标）",
    "Lighter body, reduced waist and abdominal size",
    "Satisfying change in weight/waist",
    "Satisfying change in weight/waist (you may write numerical goals in 'Other')",
    "Satisfying change in weight/waist (you may write numerical targets in 'Other')",
}

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
        title = str(field.get("title") or field.get("label") or "").casefold()
        if key not in answers or not all(part.casefold() in title for part in title_parts):
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
            if value in ("其他", "Other", "other"):
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
            t = title.casefold()
            if key in answers and all(part.casefold() in t for part in title_parts):
                return _answer_text(answers.get(key))
        return ""

    legal_name = (
        value_for("真实姓名") or value_for("姓名") or value_for("full name") or value_for("legal name") or value_for("name")
    )
    preferred_name = (
        value_for("抖音昵称") or value_for("常用称呼") or value_for("称呼") or value_for("preferred name") or value_for("nickname")
    )
    gender = value_for("性别") or value_for("gender")
    age = value_for("年龄") or value_for("age")
    city = value_for("所在的城市") or value_for("城市") or value_for("city")
    height_raw = value_for("身高") or value_for("height")
    weight_raw = value_for("当前体重") or value_for("体重") or value_for("current weight") or value_for("weight")
    waist_raw = value_for("腰围") or value_for("waist")
    bodyfat = value_for("体脂率") or value_for("body fat")
    stable_comfort_weight_raw = value_for("成年后", "稳定") or value_for("comfort weight") or value_for("stable weight")
    health_note = value_for("健康情况") or value_for("病史") or value_for("medical") or value_for("health condition")
    main_needs = value_for("最想先解决") or value_for("primary goal") or value_for("main goal")
    day28_expectation = value_for("28 天后") or value_for("28天后") or value_for("28 days") or value_for("28-day")
    adherence_worry = value_for("坚持不下来") or value_for("adherence") or value_for("worry")
    pressure_reaction = value_for("压力大时") or value_for("stress")
    diet_pattern = value_for("长期饮食模式") or value_for("dietary pattern") or value_for("diet pattern")

    height = _normalize_height_cm(_first_number(height_raw))
    weight = _parse_weight_kg(weight_raw)
    waist = _normalize_waist_cm(waist_raw)
    stable_comfort_weight = _parse_weight_kg(stable_comfort_weight_raw)
    bmi = _calculate_bmi(height, weight)
    waist_height_ratio = waist / height if waist and height else None
    safe_reduction = _safe_reduction_facts(height, weight, safety_factors)
    safety_review = _safety_review_required(safety_factors)
    weight_loss_band = _management_band_from_safe_room(
        safe_reduction["safe_reduction_room_kg"]
    )

    selected_day28_expectations = (
        _answer_values_for_title(fields, answers, "最希望", "28 天后")
        or _answer_values_for_title(fields, answers, "最希望", "28天后")
        or _answer_values_for_title(fields, answers, "hope to see", "28 days")
        or _answer_values_for_title(fields, answers, "28 days")
        or (
            [answers.get("CHECKBOX1774104667027mn0g4239")]
            if isinstance(answers.get("CHECKBOX1774104667027mn0g4239"), str)
            else (answers.get("CHECKBOX1774104667027mn0g4239") or [])
            if isinstance(answers.get("CHECKBOX1774104667027mn0g4239"), list)
            else []
        )
    )
    high_commitment_candidate, high_commitment_basis = _high_commitment_eligibility(
        selected_day28_expectations=selected_day28_expectations,
        bmi=bmi,
        waist_height_ratio=waist_height_ratio,
        safe_reduction_room_kg=safe_reduction["safe_reduction_room_kg"],
        safety_factors=safety_factors,
        weight_loss_band=weight_loss_band,
    )
    day28_targets, stretch_review_cap_kg = _day28_targets(
        weight_loss_band,
        safe_reduction["day28_goal_cap_kg"],
        baseline_weight_kg=weight,
        safe_reduction_room_kg=safe_reduction["safe_reduction_room_kg"],
        high_commitment_candidate=high_commitment_candidate,
    )
    display_cap_jin = (
        int(safe_reduction["day28_goal_cap_kg"] * 2)
        if safe_reduction["day28_goal_cap_kg"] is not None
        else None
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
        # 12周阶段目标由代码查表并按安全空间封顶，模型只负责照抄和补个案重点。
        "stage_targets": _stage_targets(
            weight_loss_band, safe_reduction["safe_reduction_room_kg"]
        ),
        # 28天基础/挑战目标同理：查表 + 按 day28_goal_cap_kg 封顶，模型只照抄数字。
        "day28_targets": day28_targets,
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
        # V2.2 目标统一字段：两份文档只允许从这些确定性值取数，模型无权另算。
        "28_day_auto_cap_kg": _format_safe_measurement(
            safe_reduction["day28_goal_cap_kg"]
        ),
        "28_day_display_cap_jin": display_cap_jin if display_cap_jin is not None else "暂无数据",
        "basic_target_jin_or_range": day28_targets["base"],
        "challenge_target_jin_or_review_text": day28_targets["challenge"],
        "high_commitment_candidate": high_commitment_candidate,
        "high_commitment_basis": high_commitment_basis,
        "stretch_review_cap_kg": _format_safe_measurement(stretch_review_cap_kg),
        "stretch_review_cap_jin": (
            int(stretch_review_cap_kg * 2)
            if stretch_review_cap_kg is not None
            else "暂无数据"
        ),
        "execution_path": "standard",
        "execution_status": "pending",
        "standard_complete_rate": 0.60,
        "high_commitment_complete_rate": 0.85,
        "target_generation_rule_applied": (
            "A带不设减重数字"
            if weight_loss_band == BAND_A
            else "标准目标=min(基线体重×5%, 4kg, 安全减重空间)；高承诺目标=min(基线体重×7.5%, 5kg, 安全减重空间-1kg)，仅合格且群内确认后生效"
        ),
        "waist": _format_number(waist) if waist else "未提供",
        "waist_cm": _format_number(waist) if waist else "未提供",
        "waist_height_ratio": f"{waist_height_ratio:.3f}" if waist_height_ratio else "暂无数据",
        "waist_height_conflict": bool(
            isinstance(safety_factors, dict)
            and safety_factors.get("waist_height_conflict") is True
        ),
        "waist_height_conflict_reason": (
            str(safety_factors.get("waist_height_conflict_reason") or "").strip()
            if isinstance(safety_factors, dict)
            else ""
        ),
        "bodyfat": bodyfat or "未提供",
        "adult_stable_comfort_weight_raw": stable_comfort_weight_raw or "未提供",
        "adult_stable_comfort_weight_kg": (
            _format_number(stable_comfort_weight) if stable_comfort_weight else "未提供"
        ),
        "health_note": health_note or "未提供",
        "main_needs": main_needs or "未提供",
        "day28_expectation": day28_expectation or "未提供",
        "adherence_worry": adherence_worry or "未提供",
        "pressure_reaction": pressure_reaction or "未提供",
        "diet_pattern": diet_pattern or "未提供",
        "document_context": _document_context_answers(answers),
    }


def _document_context_answers(answers: dict[str, Any]) -> dict[str, object]:
    """原样投影 8 道白名单题；列表仍是列表，自由文本仍是原文。"""

    projected: dict[str, object] = {}
    for key, label in DOCUMENT_CONTEXT_QUESTION_KEYS.items():
        if key not in answers:
            continue
        value = answers.get(key)
        if isinstance(value, list):
            cleaned = [str(item).strip() for item in value if str(item).strip()]
            if cleaned:
                projected[label] = cleaned
        else:
            text = str(value or "").strip()
            if text:
                projected[label] = [
                    item.strip() for item in text.split("||") if item.strip()
                ] if "||" in text else text
    return projected


def _answer_values_for_title(
    fields: list[dict[str, Any]], answers: dict[str, Any], *title_parts: str
) -> list[str]:
    for field in fields:
        key = str(field.get("key") or field.get("id") or "")
        title = str(field.get("title") or field.get("label") or "").casefold()
        if key not in answers or not all(part.casefold() in title for part in title_parts):
            continue
        raw = answers.get(key)
        if isinstance(raw, list):
            return [str(item).strip() for item in raw if str(item).strip()]
        return [item.strip() for item in str(raw or "").split("||") if item.strip()]
    return []


def _high_commitment_eligibility(
    *,
    selected_day28_expectations: list[str],
    bmi: float | None,
    waist_height_ratio: float | None,
    safe_reduction_room_kg: object,
    safety_factors: dict[str, Any] | None,
    weight_loss_band: str,
) -> tuple[bool, list[str]]:
    """最小版四条件筛选；已按业务决定取消独立的三个行动窗口条件。"""

    goal_supported = any(
        item in _WEIGHT_RELATED_DAY28_EXPECTATIONS
        for item in selected_day28_expectations[:3]
    )
    body_supported = bool(
        (bmi is not None and bmi >= 24)
        or (waist_height_ratio is not None and waist_height_ratio >= 0.5)
    )
    try:
        safe_room_supported = float(safe_reduction_room_kg) >= 7.5
    except (TypeError, ValueError):
        safe_room_supported = False
    factors = safety_factors if isinstance(safety_factors, dict) else {}
    no_safety_limit = not any(
        factors.get(key) is True
        for key in (
            "safety_review_required",
            "muscle_insufficiency",
            "disease_or_medication_limit",
            "recovery_risk",
        )
    )
    checks = [
        (goal_supported, "减重或腰腹改善属于前三项28天期待"),
        (body_supported, "BMI≥24或腰高比≥0.5"),
        (safe_room_supported, "安全减重空间≥7.5kg并预留1kg缓冲"),
        (no_safety_limit, "未触发安全、肌肉或恢复限制"),
        (weight_loss_band in MANAGEMENT_BANDS_WITH_NUMBERS, "当前管理带允许数字减重目标"),
    ]
    return all(passed for passed, _ in checks), [
        f"{'满足' if passed else '不满足'}：{label}" for passed, label in checks
    ]


def sanitize_profile_json(profile: dict[str, Any], facts: dict[str, Any] | None) -> dict[str, Any]:
    if not facts:
        return profile
    overview = profile.setdefault("overview", {})
    if isinstance(overview, dict):
        overview["gender_age"] = _join_non_empty([facts.get("gender"), facts.get("age")], " / ")
        # 用户可见展示统一用「斤」；同时把结构化 kg 值单独存一份，供 _extract_initial_weight
        # 等读取端稳定取数，不再依赖从展示串里按「kg」关键字解析（换单位会静默切断那条链路）。
        overview["height_weight"] = _join_non_empty(
            [_with_unit(facts.get("height_cm"), "cm"), _weight_kg_to_jin(facts.get("weight_kg"))],
            " / ",
        )
        weight_kg_value = facts.get("weight_kg")
        if weight_kg_value and str(weight_kg_value) not in {"未提供", "暂无数据"}:
            overview["weight_kg"] = weight_kg_value
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


def _parse_weight_kg(value: str) -> float | None:
    """把问卷里的 kg/公斤/斤/lbs 统一成公斤。

    输入框题面虽然标了 kg，但用户经常直接写「200斤」或「165 lbs」。旧逻辑只取首个数字，
    会把它当成 200kg 或 165kg，进而把 BMI、管理带和目标全部放大一倍。
    """

    number = _first_number(value)
    if number is None or number <= 0:
        return None
    val_str = str(value or "")
    if has_explicit_lbs(val_str):
        return round(number * 0.45359237, 2)
    if has_explicit_jin(val_str):
        return round(number / 2, 2)
    return number


def _normalize_waist_cm(value: str) -> float | None:
    """腰围统一成厘米。单位规则统一由 ``body_metrics`` 定义，问卷与打卡共用一套。

    相比历史实现多认了市尺（「2尺4」「24.5」按 33.3cm/尺 换算）与英寸（「28 inch」换算），
    问卷腰围会进腰高比和交付文件，填 24.5 当成 24.5 厘米会让腰高比变成 0.15。
    """

    val_str = str(value or "")
    number = _first_number(val_str)
    if number is None or number <= 0:
        return None
    if has_explicit_inch(val_str) and not any(u in val_str.lower() for u in ("cm", "厘米")):
        return round(number * 2.54, 2)
    parsed = parse_waist_cm(value)
    if parsed.value is not None:
        return parsed.value
    return number


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


# 12周阶段路径（客户 V2.1 §7.2-7.4）。这里是查表 + 封顶的确定性计算，不交给模型：
# 让模型自己"查表→判累计→校验安全空间→空间不够时收窄"要写十几行约束才勉强围得住，
# 真实验证里 D 带两个样例仍然全部越界。代码算好一句话给它照抄，规则就不需要了。
#
# 区间单位是斤，从入营当天起算累计。None 表示这一格不给数字。
_STAGE_RANGES = {
    "B带｜轻盈启动": {"week5_8": (5, 8), "week9_12": (7, 10)},
    "C带｜明确减脂": {"week5_8": (8, 12), "week9_12": (10, 16)},
    "D带｜系统减重": {"week5_8": None, "week9_12": (16, 20)},
}

_STAGE_SUFFIX = {"week9_12": "，或提前达到适宜目标后转入维持"}

# 不给数字的那几格照抄客户表述，模型不需要自己组织。
_STAGE_TEXT_A_BAND = {
    "week5_8": "不设减重指标，以体态与力量提升为主",
    "week9_12": "不设减重指标，稳定体重、改善身体组成，达到条件后再评估是否转入B带节奏",
}
_STAGE_TEXT_D_WEEK5_8 = "不预设累计斤数，按第一个28天的实际结果、耐受度和肌肉保护情况继续推进"
_STAGE_TEXT_UNKNOWN = "待补齐身高体重后确定"


def _stage_targets(band: str, safe_room_kg: float | None) -> dict[str, str]:
    """按管理带生成两个阶段的目标表述，并用安全减重空间封顶。

    封顶是这里唯一的判断：默认区间的上界可能超出这个人的安全空间（例如 C 带下沿
    只有 5kg 空间，而 C 带第9-12周默认到 16 斤＝8kg），此时必须收窄。
    """

    if band == BAND_A:
        return dict(_STAGE_TEXT_A_BAND)
    ranges = _STAGE_RANGES.get(band)
    if ranges is None:
        return {"week5_8": _STAGE_TEXT_UNKNOWN, "week9_12": _STAGE_TEXT_UNKNOWN}

    room_jin = safe_room_kg * 2 if safe_room_kg is not None else None
    targets: dict[str, str] = {}
    for stage, bounds in ranges.items():
        if bounds is None:
            targets[stage] = _STAGE_TEXT_D_WEEK5_8
            continue
        low, high = bounds
        suffix = _STAGE_SUFFIX.get(stage, "")
        if room_jin is None or high <= room_jin:
            targets[stage] = f"从入营起算累计{low}-{high}斤{suffix}"
        elif low < int(room_jin):
            # 取整后仍高于下界才给区间；否则会写出「累计7-7斤」这种退化区间。
            targets[stage] = f"从入营起算累计{low}-{int(room_jin)}斤{suffix}"
        else:
            # 安全空间连区间下界都撑不住：不给区间，只给上限。
            targets[stage] = f"从入营起算累计不超过{int(room_jin)}斤，达到条件后再评估"
    return targets


# 28天基础/挑战目标：从 day28_goal_cap_kg 推，不查带位斤数表。
#
# 【这里没有照搬客户 §7.2-7.4，理由】客户规范给了两套 28 天口径，它们对不上：
#   - §6.4：28天自动目标上限 = min（基线体重5%，4kg，安全减重空间）
#   - §7.2-7.4：B带基础3-5斤/挑战5-7斤、C带5-8/8-10、D带6-10/8-12
# 不是边界擦碰，是整段越界。C 带按定义 safe_room 5-10kg，对应体重就在 60-70kg，
# 一个月 5% 只有 6-7 斤，而挑战目标要 8-10 斤；要让 10 斤（5kg）落进 5%，用户得
# 有 100kg，可 100kg 的人 safe_room 早就超 10kg、按 §7.5 属于 D 带了——所以 C 带
# 那个 10 斤在数学上不可达，不是取值保守与否的问题。
#
# 选择以 5% 为准，依据三条：
#   1. 客户 2026-07-31 明确重申"是一个月减 5%哦，每个带是 3 个月的"——带位那套
#      斤数（带名 5/10/20 斤、12 周累计 7-10/10-16/16-20 斤）是 3 个月路径的量级，
#      不该拿来当 28 天的数；12 周那三段仍照 §7.2-7.4 走，见 _stage_targets。
#   2. §7.2-7.4 每条挑战目标自己都跟着"但不得突破28天自动上限"，客户已把优先级
#      判给上限。
#   3. 5% 是跟体重挂钩的相对口径，对任何基数都安全；带位斤数是绝对值，对小体重
#      用户系统性过激。
#
# 4kg 硬顶保留：客户这次只强调 5%、没说取消它，且它只在 ≥80kg 时才绑定，
# 4kg/月对任何人都是安全上限，没有理由自作主张放宽。
#
_DAY28_TEXT_A_BAND = {
    "base": "不设置必须下降的体重硬指标，优先改善体态、睡眠、精力、恢复和执行一致性",
    "challenge": "当前不开放高承诺减重目标，优先修复塑形",
}
_DAY28_TEXT_UNKNOWN = {
    "base": "待补齐身高体重后与班主任确认目标",
    "challenge": "待补齐身高体重后判断是否开放高承诺目标",
}


def _day28_targets(
    band: str,
    goal_cap_kg: float | None,
    *,
    baseline_weight_kg: float | None = None,
    safe_reduction_room_kg: object = None,
    high_commitment_candidate: bool = False,
) -> tuple[dict[str, str], float | None]:
    """给出标准目标和条件性高承诺目标。

    单位换算一次：cap 是公斤，用户可见文案用斤。cap 向下取整，宁可少给。
    """

    if band == BAND_A:
        return dict(_DAY28_TEXT_A_BAND), None
    if band not in MANAGEMENT_BANDS_WITH_NUMBERS or goal_cap_kg is None:
        return dict(_DAY28_TEXT_UNKNOWN), None

    cap_jin = int(goal_cap_kg * 2)
    if cap_jin <= 0:
        # 空间归零但带位不是 A 带，理论上不该出现；真出现了也不能给数字。
        return dict(_DAY28_TEXT_A_BAND), None

    # 标准档吸收旧「基础 + 向上限推进」两层，用户只看到一套标准目标。
    base_low = max(1, cap_jin - 2)
    standard_target = _day28_jin_text(base_low, cap_jin)
    stretch_cap_kg = _stretch_review_cap_kg(
        baseline_weight_kg=baseline_weight_kg,
        safe_reduction_room_kg=safe_reduction_room_kg,
    )
    challenge_target = "当前不开放高承诺目标"
    if high_commitment_candidate and stretch_cap_kg is not None:
        stretch_jin = int(stretch_cap_kg * 2)
        high_low = cap_jin + 1
        if high_low <= stretch_jin:
            challenge_target = (
                f"28天向{high_low}斤推进"
                if high_low == stretch_jin
                else f"28天向{high_low}-{stretch_jin}斤推进"
            )
        else:
            high_commitment_candidate = False
    return {
        "base": standard_target,
        "challenge": challenge_target,
    }, stretch_cap_kg if high_commitment_candidate else None


def _stretch_review_cap_kg(
    *, baseline_weight_kg: float | None, safe_reduction_room_kg: object
) -> float | None:
    if baseline_weight_kg is None:
        return None
    try:
        safe_room = float(safe_reduction_room_kg)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(baseline_weight_kg * 0.075, 5.0, safe_room - 1.0))


def _day28_jin_text(low: int, high: int) -> str:
    """区间退化成一个数时不写成「3-3斤」。"""

    return f"28天减重{low}斤" if low >= high else f"28天减重{low}-{high}斤"


def _safety_review_reasons(safety_factors: dict[str, Any] | None) -> list[str]:
    if not isinstance(safety_factors, dict):
        return []
    raw = safety_factors.get("safety_review_reasons")
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()][:3]


def _safety_review_required(safety_factors: dict[str, Any] | None) -> bool:
    """问卷是否暴露了必须人工复核的安全条件。

    注意它**不改带位或目标**：A-D 带和基础/挑战目标始终按安全减重空间的数值边界判定。
    红色风险、孕哺、青少年等条件只点亮人工复核，文件会被扣住不自动发送，由运营确认后再发。
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


def _weight_kg_to_display(value: Any, language: str = "zh") -> str:
    """把 kg 体重录入值换算成用户展示的「斤」（中文）或「lbs」（英文）。

    只做展示换算：内部计算与结构化字段仍用 kg，这里只影响文档/画像里给用户看的那一行。
    解析失败时回退成原 kg 展示，绝不吞掉数据或返回空造成体重丢失。
    """

    if not value or str(value) in {"未提供", "暂无数据"}:
        return ""
    try:
        val_f = float(str(value))
    except (TypeError, ValueError):
        return _with_unit(value, "kg")
    if language == "en":
        lbs = round(val_f * 2.20462262, 1)
        lbs_text = f"{int(lbs)}" if lbs == int(lbs) else f"{lbs:.1f}"
        return f"{lbs_text} lbs"
    jin = val_f * 2
    text = f"{int(jin)}" if jin == int(jin) else f"{jin:.1f}"
    return f"{text}斤"


def _weight_kg_to_jin(value: Any) -> str:
    return _weight_kg_to_display(value, language="zh")


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
    normalized = (gender or "").strip().lower()
    if normalized in ("女", "female", "woman"):
        return " 女士"
    if normalized in ("男", "male", "man"):
        return " 先生"
    return ""
