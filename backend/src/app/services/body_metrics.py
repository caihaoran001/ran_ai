"""打卡体重/腰围的权威解析：把用户自由填写的文本变成可统计的公斤/厘米。

为什么需要这一层：打卡里的体重、腰围是纯自由文本，线上真实出现过
``140.2斤``、``70.7kg``、``151.6``（其实是斤）、``68·3``（中点当小数点）、
``22.5（2尺2.5）``、``24.5``（2尺4寸半）、``0``、``本日未测``、
``腰围104    肚(臀围)围114`` 等写法。历史实现只用一个正则取第一个数字、不认单位，
于是 ``140.2斤 → 70.7kg`` 被算成「28 天掉了 70 公斤」，腰围填 ``0`` 被算成
「从 0 长到 68 厘米」，看板上的均值直接被这类值主导。

设计取舍：
- **只做确定性解析，不引入模型**。单位归一属于数据清洗，不是业务判断，
  不适用"业务判断优先用大模型"的约定。
- **能判就纠正，判不了就剔除，绝不静默猜**。每个结果都带 ``reason``，
  调用方（看板详情页）要如实展示剔除了多少条、为什么，而不是让样本悄悄变小。
- **入营问卷值是锚**。同一个人无单位的裸数字，只有和他自己的起始体重/腰围
  比较才能判断是公斤还是斤——``151.6`` 与基线 ``75.8`` 是两倍关系，
  这类污染方向正确、幅度错一倍，任何绝对值阈值都抓不到。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 解析结论。ok 之外都不参与统计，但必须能在页面上说清为什么被排除。
REASON_OK = "ok"
REASON_NO_NUMBER = "no_number"  # 未填或没有可解析数字（"本日未测""出差""没称"都落这里）
REASON_UNIT_AMBIGUOUS = "unit_ambiguous"  # 有数字但和本人基线既不像公斤也不像斤/尺
REASON_OUT_OF_RANGE = "out_of_range"  # 换算后仍不是人体可能的值（"0" 落这里）

# 绝对范围只当兜底，刻意放宽，避免误伤真实极端体型；
# 离谱值主要靠基线锚定和调用方的变化幅度上限来拦。
WEIGHT_MIN_KG = 25.0
WEIGHT_MAX_KG = 300.0
WAIST_MIN_CM = 40.0
WAIST_MAX_CM = 200.0
HEIGHT_MIN_CM = 90.0
HEIGHT_MAX_CM = 250.0

# 市制换算：1 尺 = 10 寸 = 33.333 厘米。
CM_PER_CHI = 100.0 / 3.0

# 用户在手机上常用中点、句号代替小数点（线上真实：68·3、67·9）。
_DECIMAL_LOOKALIKES = ("·", "・", "•", "．", "。")

_KG_HINTS = ("公斤", "千克", "kg", "㎏")
_JIN_HINT = "斤"
_NUMBER_PATTERN = re.compile(r"\d+(?:\.\d+)?")
# "2尺2.5"/"2尺2.5寸"/"2 尺 4 寸"
_CHI_CUN_PATTERN = re.compile(r"(\d+)\s*尺\s*(\d+(?:\.\d+)?)?\s*寸?")


@dataclass(frozen=True)
class BodyValue:
    """一次解析的结果。``value is None`` 时 ``reason`` 说明原因。

    ``unit_verified=False`` 表示这个数字被当作已是目标单位、但没有任何依据可核对
    （既没写单位，也没有本人的问卷基线可比）。它仍然参与统计——否则样本会少到
    没法看——但调用方应把这类人数单独报出来，因为万一整段都填的是斤，
    差值方向对、幅度会错一倍。
    """

    value: float | None
    reason: str
    unit_verified: bool = False
    corrected_from: str | None = None  # jin / chi / meter / millimeter

    @property
    def ok(self) -> bool:
        return self.value is not None


def normalize_decimal_text(raw: object) -> str:
    """把中点、句号等写法归一成小数点，返回去空白的文本。"""

    text = "" if raw is None else str(raw).strip()
    for token in _DECIMAL_LOOKALIKES:
        text = text.replace(token, ".")
    return text


def first_number(text: str) -> float | None:
    match = _NUMBER_PATTERN.search(text or "")
    if not match:
        return None
    try:
        return float(match.group())
    except ValueError:  # 正则已保证可转，这里只是不让解析层抛异常
        return None


_LBS_HINTS = ("lb", "lbs", "pound", "pounds", "磅")
# 英寸只认「数字紧跟单位」。``inch``/``inches``/``英寸``/双引号无歧义，直接放行；
# 裸 ``in`` 是英语介词，必须排除 "80 in the morning" 这类句子——后面还跟着单词就不算单位。
# 宁可漏判 "36 in waist"（按厘米走，会被下游腰高比矛盾检查拦下来让运营复测），
# 也不能把介词当单位，那会把 80 厘米算成 203 厘米。
_INCH_PATTERN = re.compile(
    r"\d+(?:\.\d+)?\s*(?:inch(?:es)?\b|英寸|[\"”″]|in\b(?!\s*[a-z]))",
    re.IGNORECASE,
)
# 英尺英寸连写：5'10"、5'10、5 ft 10 in、5ft10、5 feet 10 inches、5.9 ft。
_FEET_INCH_PATTERN = re.compile(
    # 单位后用 (?![a-z]) 而不是 \b：``5ft10`` 里 "t" 紧跟数字不构成词边界，用 \b 会整条漏判。
    r"(\d+(?:\.\d+)?)\s*(?:['’′]|ft(?![a-z])|feet(?![a-z])|foot(?![a-z])|英尺)\s*"
    r"(?:(\d+(?:\.\d+)?)\s*(?:inch(?:es)?\b|英寸|[\"”″]|in\b(?!\s*[a-z]))?)?",
    re.IGNORECASE,
)
_CM_PER_INCH = 2.54
_INCH_PER_FOOT = 12


def has_explicit_kg(text: str) -> bool:
    return any(hint in text.lower() for hint in _KG_HINTS)


def has_explicit_lbs(text: str) -> bool:
    return any(hint in text.lower() for hint in _LBS_HINTS)


def has_explicit_inch(text: str) -> bool:
    return bool(_INCH_PATTERN.search(text or ""))


def has_explicit_jin(text: str) -> bool:
    """写了「斤」且没同时写公斤/千克/kg/磅。"""

    return _JIN_HINT in text and not has_explicit_kg(text) and not has_explicit_lbs(text)


def waist_explicit_unit_scale(text: str) -> float | None:
    """腰围里明确写出的长度单位对应的换算系数；没写单位返回 None。

    ``cm`` 里含 ``m``、``厘米``/``毫米`` 里含``米``，所以必须按从具体到宽泛的顺序判断。
    """

    lower = (text or "").lower()
    if "mm" in lower or "毫米" in lower:
        return 0.1
    if "cm" in lower or "厘米" in lower or "公分" in lower:
        return 1.0
    if has_explicit_inch(text):
        return 2.54
    if "m" in lower or "米" in lower:
        return 100.0
    return None


def parse_feet_inches_cm(raw: object) -> float | None:
    """把英尺英寸写法换算成厘米；不是这种写法返回 None。

    英文客户填身高几乎只会写 ``5'10"``、``5 ft 10``、``5.9 ft`` 这三类。
    英尺部分必须出现单位记号，否则 ``5.9`` 到底是英尺还是米没法判——那种情况
    交给下游的无单位启发式，不在这里猜。
    """

    text = normalize_decimal_text(raw)
    match = _FEET_INCH_PATTERN.search(text)
    if not match:
        return None
    try:
        feet = float(match.group(1))
    except (TypeError, ValueError):
        return None
    inches = 0.0
    if match.group(2):
        try:
            inches = float(match.group(2))
        except ValueError:
            inches = 0.0
    if feet <= 0:
        return None
    return (feet * _INCH_PER_FOOT + inches) * _CM_PER_INCH


def parse_height_cm(raw: object) -> BodyValue:
    """把自由填写的身高解析成厘米。

    问卷题面写的是厘米，但英文客户按本地习惯填英寸/英尺，中文客户会填「1.75」。
    单位规则和体重、腰围放在同一层，问卷与打卡共用一套，不在业务代码里各写一份。

    没写单位时保留原有启发式：1.2–2.3 之间的数不可能是厘米，按米换算。
    刻意**不**按语言猜英制——同一个裸数字 ``70`` 对英文客户是英寸、对中文客户是笔误，
    猜错会把 BMI 和管理带整档算错，不如让前端把人挡在提交前、让他写清单位。
    """

    text = normalize_decimal_text(raw)
    number = first_number(text)
    if number is None:
        return BodyValue(None, REASON_NO_NUMBER)

    feet_inches = parse_feet_inches_cm(text)
    if feet_inches is not None:
        return _finish_height(feet_inches, unit_verified=True, corrected_from="feet_inches")

    if number <= 0:
        return BodyValue(None, REASON_OUT_OF_RANGE)

    lower = text.lower()
    if has_explicit_inch(text):
        return _finish_height(number * _CM_PER_INCH, unit_verified=True, corrected_from="inch")
    if "cm" in lower or "厘米" in lower or "公分" in lower:
        return _finish_height(number, unit_verified=True)
    if "mm" in lower or "毫米" in lower:
        return _finish_height(number / 10, unit_verified=True, corrected_from="millimeter")
    if "m" in lower or "米" in lower:
        return _finish_height(number * 100, unit_verified=True, corrected_from="meter")

    if 1.2 <= number <= 2.3:
        return _finish_height(number * 100, unit_verified=False, corrected_from="meter")
    return _finish_height(number, unit_verified=False)


def parse_weight_kg(raw: object, *, baseline_kg: float | None = None) -> BodyValue:
    """把一条打卡体重解析成公斤。

    ``baseline_kg`` 是这个人入营问卷填的起始体重。有它才能判断无单位的裸数字
    是公斤还是斤：``151.6`` 对基线 ``75.8`` 是两倍关系，判为斤。
    """

    text = normalize_decimal_text(raw)
    number = first_number(text)
    if number is None:
        return BodyValue(None, REASON_NO_NUMBER)
    if number <= 0:
        return BodyValue(None, REASON_OUT_OF_RANGE)

    if has_explicit_lbs(text):
        return _finish_weight(round(number * 0.45359237, 2), unit_verified=True, corrected_from="lbs")

    explicit_jin = has_explicit_jin(text)
    if explicit_jin or has_explicit_kg(text):
        primary = number / 2 if explicit_jin else number
        # 写出来的单位也会和本人起始体重打架。线上真实例子：基线 100 公斤的人写
        # 「103.7斤（排便后）」，按斤算成 51.85 公斤。他显然是随口写了个"斤"，
        # 数字本身才是公斤。所以先看另一种解释是否说得通，说得通就按那个走。
        if baseline_kg and baseline_kg > 0 and not _matches_baseline(primary, baseline_kg):
            alternative = number if explicit_jin else number / 2
            if _matches_baseline(alternative, baseline_kg):
                return _finish_weight(alternative, unit_verified=True, corrected_from="unit_typo")
            return BodyValue(None, REASON_UNIT_AMBIGUOUS)
        return _finish_weight(
            primary, unit_verified=True, corrected_from="jin" if explicit_jin else None
        )

    if baseline_kg and baseline_kg > 0:
        ratio = number / baseline_kg
        # 两个区间刻意不重叠：28 天内体重变化撑死十几公斤，所以"同量级"最多到 1.25 倍，
        # "斤"最少也是 1.5 倍。落在中间地带说明这个数解释不通，剔除比猜安全。
        if _matches_baseline(number, baseline_kg):
            return _finish_weight(number, unit_verified=True)
        if 1.5 <= ratio <= 2.5:
            return _finish_weight(number / 2, unit_verified=True, corrected_from="jin")
        return BodyValue(None, REASON_UNIT_AMBIGUOUS)

    return _finish_weight(number, unit_verified=False)


def parse_waist_cm(raw: object, *, baseline_cm: float | None = None) -> BodyValue:
    """把一条打卡腰围解析成厘米。

    市制是这里的主要麻烦：``22.5（2尺2.5）``、``24.5`` 这种写法指的是「几尺几寸」，
    数值除以 10 才是尺。好在成年人腰围不可能只有 24 厘米，所以即使没有基线，
    按尺换算也几乎不会判错——不换算的话这些人只会被当成越界值丢掉。
    """

    text = normalize_decimal_text(raw)
    chi_match = _CHI_CUN_PATTERN.search(text)
    if chi_match:
        chi = float(chi_match.group(1))
        cun = float(chi_match.group(2)) if chi_match.group(2) else 0.0
        return _finish_waist(
            (chi + cun / 10) * CM_PER_CHI, unit_verified=True, corrected_from="chi"
        )

    number = first_number(text)
    if number is None:
        return BodyValue(None, REASON_NO_NUMBER)
    if number <= 0:
        return BodyValue(None, REASON_OUT_OF_RANGE)

    scale = waist_explicit_unit_scale(text)
    if scale is not None:
        corrected = {0.1: "millimeter", 100.0: "meter"}.get(scale)
        return _finish_waist(number * scale, unit_verified=True, corrected_from=corrected)

    if baseline_cm and baseline_cm > 0:
        ratio = number / baseline_cm
        if 0.8 <= ratio <= 1.2:
            return _finish_waist(number, unit_verified=True)
        # 尺寸连写（22.5 → 2.25 尺）对厘米基线约是 0.3 倍；纯尺（2.4）约是 0.03 倍。
        chi_candidate = _chi_like_to_cm(number)
        if chi_candidate is not None and 0.8 <= chi_candidate / baseline_cm <= 1.2:
            return _finish_waist(chi_candidate, unit_verified=True, corrected_from="chi")
        return BodyValue(None, REASON_UNIT_AMBIGUOUS)

    if WAIST_MIN_CM <= number <= WAIST_MAX_CM:
        return _finish_waist(number, unit_verified=False)
    chi_candidate = _chi_like_to_cm(number)
    if chi_candidate is not None:
        return _finish_waist(chi_candidate, unit_verified=False, corrected_from="chi")
    return BodyValue(None, REASON_OUT_OF_RANGE)


def _matches_baseline(value: float, baseline: float) -> bool:
    """这个公斤数和本人起始体重是否同量级（28 天内变化撑死十几公斤）。"""

    return 0.75 <= value / baseline <= 1.25


def _chi_like_to_cm(number: float) -> float | None:
    """把疑似市制的裸数字换算成厘米：15–35 视为尺寸连写，1.5–3.5 视为纯尺。"""

    if 15 <= number <= 35:
        return number / 10 * CM_PER_CHI
    if 1.5 <= number <= 3.5:
        return number * CM_PER_CHI
    return None


def _finish_weight(
    value: float, *, unit_verified: bool, corrected_from: str | None = None
) -> BodyValue:
    if not WEIGHT_MIN_KG <= value <= WEIGHT_MAX_KG:
        return BodyValue(None, REASON_OUT_OF_RANGE)
    return BodyValue(round(value, 2), REASON_OK, unit_verified, corrected_from)


def _finish_waist(
    value: float, *, unit_verified: bool, corrected_from: str | None = None
) -> BodyValue:
    if not WAIST_MIN_CM <= value <= WAIST_MAX_CM:
        return BodyValue(None, REASON_OUT_OF_RANGE)
    return BodyValue(round(value, 2), REASON_OK, unit_verified, corrected_from)


def _finish_height(
    value: float, *, unit_verified: bool, corrected_from: str | None = None
) -> BodyValue:
    if not HEIGHT_MIN_CM <= value <= HEIGHT_MAX_CM:
        return BodyValue(None, REASON_OUT_OF_RANGE)
    return BodyValue(round(value, 2), REASON_OK, unit_verified, corrected_from)
