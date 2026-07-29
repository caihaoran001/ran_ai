from __future__ import annotations

import math
import re
from typing import Any


CUSTOM_ANSWER_LABEL = "其他（用户补充）"


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


def build_intake_fact_summary(fields: list[dict[str, Any]], answers: dict[str, Any]) -> dict[str, Any]:
    """Extract deterministic facts shared by profile and document generation."""

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
    weight_loss_band = _weight_loss_band(bmi)
    protected_lower, room_exhausted, goal_cap = _safe_reduction_facts(height, weight)

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
        # 安全余量：内部约束，只用来给目标设天花板，不得出现在交付文件里。
        # 只传下限体重（要和用户目标体重比大小）、是否已无空间、打过折的规划上限；
        # 真实上限和空间公斤数刻意不传，少一个数字就少一处可被原样引用的泄漏点。
        "protected_lower_weight_kg": f"{protected_lower:.1f}" if protected_lower else "暂无数据",
        "weight_loss_room_exhausted": "暂无数据" if room_exhausted is None else ("是" if room_exhausted else "否"),
        "day28_goal_cap_kg": f"{goal_cap:.1f}" if goal_cap is not None else "暂无数据",
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


def _safe_reduction_facts(
    height_cm: float | None, weight_kg: float | None
) -> tuple[float | None, bool | None, float | None]:
    """安全减重空间：先算这个人还能安全减多少，再谈目标。

    - protected_lower_weight_kg = 20 × 身高m²：自动方案不得突破的保护性下限，
      是系统复核线而不是医学诊断线，也不代表建议用户减到这个体重。
    - 安全空间 = 基线体重 − 保护性下限。空间本身不给模型，只给「是否已耗尽」——
      判断只需要这个布尔结论，把公斤数传过去等于多给一个可被原样引用的数字。
    - day28_goal_cap_kg = min(基线体重5%, 4kg, 安全空间) × 0.8，向下取整到 0.5kg。

    打 8 折是实测得出的：真实模型会把上限当靶心（上限 3.2 就写到 3.0），
    留缓冲让「贴着上限写」也落在安全区内；取整到 0.5kg（=1斤）也让这个数看起来
    像人定的档位，而不是可被原样抄进交付文件的计算结果。上限为 0 表示不给数字目标。

    只作为目标的天花板使用，不参与管理带判断——BMI 22 的健康体重用户也能算出
    7kg 空间，拿它分带会把正常体重的人系统性推去减脂。
    """

    if not height_cm or not weight_kg or height_cm <= 0:
        return None, None, None
    height_m = height_cm / 100
    protected_lower = 20 * height_m * height_m
    safe_room = max(0.0, weight_kg - protected_lower)
    true_cap = min(weight_kg * 0.05, 4.0, safe_room)
    goal_cap = math.floor(true_cap * 0.8 * 2) / 2
    return protected_lower, safe_room <= 0, goal_cap


def _calculate_bmi(height_cm: float | None, weight_kg: float | None) -> float | None:
    if not height_cm or not weight_kg or height_cm <= 0:
        return None
    height_m = height_cm / 100
    return weight_kg / (height_m * height_m)


def _weight_loss_band(bmi: float | None) -> str:
    if bmi is None:
        return "需补充身高体重后判断"
    if bmi < 18.5:
        return "增肌增能带"
    if bmi < 24:
        return "A带｜修复塑形"
    if bmi < 28:
        return "B带｜轻盈启动"
    if bmi < 32:
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

    优先级：疾病管理型(高风险) > 减重攻坚型(C/D 带，BMI≥28) > 健康投资型(兜底)。
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
