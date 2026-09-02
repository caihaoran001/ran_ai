from __future__ import annotations

"""入营建档：LLM 生成 intake_analysis_package，并归一化画像/文档 payload、派生打卡字段。"""

import json
import logging
import re
from functools import lru_cache
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.questionnaire import QuestionnaireSubmission
from app.models.service_case import ServiceCase
from app.models.user_document import DocumentType
from app.services.ai_service import AIService, RetryableAIContentError, parse_json_object
from app.services.document_templates import combined_prompt_schema
from app.services.intake_facts import (
    UNRESOLVED_BAND,
    _weight_kg_to_display,
    _weight_kg_to_jin,
    build_intake_fact_summary,
    sanitize_profile_json,
)
from app.services.checkin_questionnaires import personalized_checkin_fields_with_weight
from app.services.questionnaire_template import get_active_questionnaire_fields
from app.services.document_prompts import _intake_generation_system_prompt
from app.services.risk_service import is_high_risk


logger = logging.getLogger(__name__)

MANAGEMENT_BANDS = (
    "A带｜修复塑形",
    "B带｜轻盈启动",
    "C带｜明确减脂",
    "D带｜系统减重",
)

# 算不出带位时 intake_facts 给的是「需补充身高体重后判断」。那是后台哨兵值——
# dashboard 的分类统计和人群画像都按它过滤未采集用户，不能改。但它同时会被下发到
# 《诊断报告》成功路径，用户打开就看到「您当前管理带：需补充身高体重后判断」。
# 所以哨兵值留在 shared_analysis / profile_json 里，进用户可见文档时换成这句人话。
UNRESOLVED_BAND_USER_TEXT = "待补齐身高体重后确定，班主任会尽快与您核对"


def _user_visible_band(band: object) -> str:
    """把内部带位翻译成用户可见文案。

    只翻译这一个哨兵值：文案说的是「缺身高体重」，套到别的未知取值上就是错的说明。
    """

    text = str(band or "").strip()
    return UNRESOLVED_BAND_USER_TEXT if text == UNRESOLVED_BAND else text


BAND_CALCULATION_FIELDS = (
    "baseline_weight_kg",
    "baseline_weight_provisional",
    "absolute_low_weight_boundary_kg",
    "automatic_review_weight_kg",
    "protected_lower_weight_kg",
    "protected_lower_uplift_bmi",
    "protected_lower_uplift_reasons",
    "safe_reduction_room_kg",
    "safety_review_required",
    "safety_review_reasons",
    "28_day_auto_cap_kg",
    "28_day_display_cap_jin",
    "basic_target_jin_or_range",
    "challenge_target_jin_or_review_text",
    "target_generation_rule_applied",
    "high_commitment_candidate",
    "high_commitment_basis",
    "stretch_review_cap_kg",
    "stretch_review_cap_jin",
    "execution_path",
    "execution_status",
    "standard_complete_rate",
    "high_commitment_complete_rate",
)

INTAKE_DOCUMENT_MAX_ATTEMPTS = 3

# 入营问卷只沉淀会长期影响服务安全边界的明确健康风险。投诉、短期不适、
# 退款等属于群聊当下处理语义，绝不能从问卷安全提取里混进来。
PERSISTENT_INTAKE_RISK_CODES = frozenset({
    "pregnancy",
    "lactation",
    "chronic_disease",
    "long_term_medication",
    "hormone_medication",
    "liver_kidney",
    "stone",
    "severe_test_abnormality",
    "depression",
})

# 交付文件模型只需要这些用户交付事实；运营复核状态、医学自由文本和内部计算依据
# 均不得进入该模型上下文。模型拿到的是最终带位和目标，不需要知道这些结果为何被风控关注。
_DOCUMENT_MODEL_FACT_KEYS = (
    "gender",
    "age",
    "height_cm",
    "weight_kg",
    "bmi",
    "weight_loss_band",
    "stage_targets",
    "day28_targets",
    "28_day_auto_cap_kg",
    "28_day_display_cap_jin",
    "basic_target_jin_or_range",
    "challenge_target_jin_or_review_text",
    "target_generation_rule_applied",
    "waist",
    "waist_cm",
    "bodyfat",
    "adult_stable_comfort_weight_raw",
    "adult_stable_comfort_weight_kg",
    "main_needs",
    "day28_expectation",
    "adherence_worry",
    "pressure_reaction",
    "diet_pattern",
    "document_context",
)
# 文档模型不负责这些内部状态；即使它在越界输出中带回，也不能污染运营画像。
_MODEL_OWNED_INTERNAL_FIELDS = (
    "manual_review_required",
    "manual_review_reasons",
    "safety_status",
    "band_confidence",
    "provisional_band",
)

# 运动次数不是新的业务判断：它由同一轮模型写入 shared_analysis.activity_plan，
# 这里仅将该结构化结果确定性回填到三处历史自由文本，避免 2 次 / 3 次 / 4 次并存。
_WEEKLY_FREQUENCY_PATTERN = re.compile(r"每周\s*(?:[1-7]|一|二|三|四|五|六|七|两)\s*次")
_ACTIVITY_TEXT_MARKERS = (
    "分钟",
    "小时",
    "运动",
    "快走",
    "散步",
    "步行",
    "跑步",
    "慢跑",
    "游泳",
    "骑行",
    "力量",
    "抗阻",
    "训练",
    "健身",
    "瑜伽",
    "拉伸",
    "爬楼",
    "跳绳",
)


class DocumentGenerationBlocked(RuntimeError):
    """关键资料不足或明显矛盾，正式交付文件不能可靠生成。"""

    def __init__(self, reasons: list[str]):
        self.reasons = tuple(reason for reason in reasons if reason)
        self.ready_for_document_generation = False
        self.document_generation_block_reasons = list(self.reasons)
        super().__init__("；".join(self.reasons) or "关键资料尚未确认")


class RetryableIntakePackageError(RuntimeError):
    """模型正文存在结构或一致性问题，可在不改业务事实的前提下重新生成。"""


def _normalize_shared_activity_frequency(shared: dict[str, object]) -> int | None:
    """Normalize the one activity-frequency source for an intake package.

    A paused plan deliberately has no numerical frequency. Otherwise accept only
    the agreed 2/3/4-session cadence; malformed output falls back to three.
    """

    raw_plan = shared.get("activity_plan")
    plan = raw_plan if isinstance(raw_plan, dict) else {}
    status = str(plan.get("status") or "planned").strip().lower()
    if status == "paused":
        shared["activity_plan"] = {"status": "paused", "sessions_per_week": None}
        return None

    try:
        sessions = int(plan.get("sessions_per_week"))
    except (TypeError, ValueError):
        sessions = 3
    if sessions not in {2, 3, 4}:
        sessions = 3
    shared["activity_plan"] = {"status": "planned", "sessions_per_week": sessions}
    return sessions


def _sync_activity_frequency_in_text(value: object, sessions_per_week: int) -> object:
    """Replace only an existing weekly *activity* frequency, preserving its details.

    The text still comes from the model, including activity type, duration,
    intensity, time boundary and alternative. The structured shared value owns
    just the count, so a weekly weigh-in or review cadence is never rewritten.
    """

    if isinstance(value, str):
        def replace_if_activity(match: re.Match[str]) -> str:
            # 只看这次「每周X次」附近的语境。整句里后半段即使另有“计划内活动”，
            # 也不能把前面的「每周1次复测」错改成运动频次。
            nearby = value[max(0, match.start() - 8) : min(len(value), match.end() + 18)]
            if any(marker in nearby for marker in _ACTIVITY_TEXT_MARKERS):
                return f"每周{sessions_per_week}次"
            return match.group(0)

        return _WEEKLY_FREQUENCY_PATTERN.sub(replace_if_activity, value)
    if isinstance(value, list):
        return [_sync_activity_frequency_in_text(item, sessions_per_week) for item in value]
    if isinstance(value, dict):
        return {
            key: _sync_activity_frequency_in_text(item, sessions_per_week)
            for key, item in value.items()
        }
    return value


def _apply_shared_activity_frequency_to_documents(
    document_payloads: dict[str, dict],
    sessions_per_week: int | None,
) -> None:
    """Apply one shared activity count to the two user-visible document payloads."""

    if sessions_per_week is None:
        return
    for document_type in (
        DocumentType.ENERGY_RESTART_PRESCRIPTION.value,
        DocumentType.DAY28_EXECUTION_STANDARD.value,
    ):
        payload = document_payloads.get(document_type)
        if isinstance(payload, dict):
            # 频次也可能被模型写进摘要、特别提醒或四周验收标准；只处理有运动语境的
            # 文本，保证用户全文都只看到同一个次数。
            document_payloads[document_type] = _sync_activity_frequency_in_text(
                payload, sessions_per_week
            )


def _apply_shared_activity_frequency_to_profile(
    profile: dict[str, object],
    sessions_per_week: int | None,
) -> None:
    """Store and surface the same activity count in the operations-only profile."""

    status = "paused" if sessions_per_week is None else "planned"
    profile["activity_plan"] = {"status": status, "sessions_per_week": sessions_per_week}
    if sessions_per_week is None:
        return
    # 后台画像的摘要、状态、建议与 next_actions 都可能出现活动频次，不能只修一栏。
    for key, value in tuple(profile.items()):
        if key != "activity_plan":
            profile[key] = _sync_activity_frequency_in_text(value, sessions_per_week)

# 内部运营标签：只允许留在 shared_analysis / profile_json 的内部字段和后台界面里。
# 用户拿到的两份交付文件（管理带、正文、表格、打卡题面）中一个都不能出现。
INTERNAL_REVIEW_LABELS = (
    "需人工复核",
    "需要人工复核",
    "人工复核",
    "待复核",
    "需复核",
    "manual_review",
    "manual review",
    "red风险",
    "红色风险",
    "风险等级",
)


def _contains_internal_review_label(text: str) -> bool:
    lowered = text.lower()
    return any(label.lower() in lowered for label in INTERNAL_REVIEW_LABELS)


def _resolve_management_band(_shared: dict[str, object], facts: dict[str, object]) -> str:
    """最终管理带只接受安全减重空间的确定性结果，模型无权改带。"""

    return str(facts.get("weight_loss_band") or "").strip()


def _apply_deterministic_band(
    shared: dict[str, object],
    facts: dict[str, object],
) -> None:
    """把四带计算字段和结论收进 shared_analysis，供画像与两份文件共用。"""

    for key in BAND_CALCULATION_FIELDS:
        shared[key] = facts.get(key)
    band = _resolve_management_band(shared, facts)
    if band:
        shared["weight_band"] = band
        facts["weight_loss_band"] = band
    shared["band_basis"] = _deterministic_band_basis(facts, band)
    # provisional_band 是给运营挑人的信号：这个带位还需要人工确认一眼。
    # 它曾经跟着 baseline_weight_provisional 一起置真，但入营问卷只让用户填一次体重，
    # 永远拿不到客户规范要求的连续3天晨重均值，于是全员常亮、筛不出人。
    # 测量方式由 baseline_weight_provisional 如实记录，带位是否暂定只看关键数据是否缺失。
    if band not in MANAGEMENT_BANDS:
        reasons = _clean_list(shared.get("manual_review_reasons"))
        reason = "缺少可信身高或体重，暂不能计算A-D管理带"
        if reason not in reasons:
            reasons.append(reason)
        shared["manual_review_required"] = True
        shared["manual_review_reasons"] = reasons[:3]
        shared["safety_status"] = "manual_review"
        shared["provisional_band"] = True

    # 问卷暴露的安全条件（红风险、孕哺、青少年等）不改带位或目标——A-D 带和
    # 基础/挑战目标始终由安全减重空间的数值边界决定。它们在这里仅强制人工复核：
    # 文件会被扣住不自动发送，由运营确认后再发。
    # 这比 _force_high_risk_manual_review 更早生效：风控可能还没把服务单判成高风险，
    # 问卷里已经写明了孕期或术后。
    if facts.get("safety_review_required"):
        reasons = _clean_list(shared.get("manual_review_reasons"))
        for reason in _clean_list(facts.get("safety_review_reasons")) or [
            "问卷出现需要人工确认的安全条件"
        ]:
            if reason not in reasons:
                reasons.append(reason)
        shared["manual_review_required"] = True
        shared["manual_review_reasons"] = reasons[:3]
        shared["safety_status"] = "manual_review"


def _deterministic_band_basis(
    facts: dict[str, object],
    band: str,
) -> list[str]:
    baseline = str(facts.get("baseline_weight_kg") or "").strip()
    protected = str(facts.get("protected_lower_weight_kg") or "").strip()
    room = str(facts.get("safe_reduction_room_kg") or "").strip()
    if any(value in {"", "暂无数据"} for value in (baseline, protected, room)):
        return ["缺少可信身高或体重，暂不能计算安全减重空间"]
    boundaries = {
        "A带｜修复塑形": "小于2.5kg",
        "B带｜轻盈启动": "2.5kg至不足5kg",
        "C带｜明确减脂": "5kg至不足10kg",
        "D带｜系统减重": "不低于10kg",
    }
    # 这条依据会在模型没写 band_explanation 时兜底填进用户可见的核心判断，
    # 所以不能嵌基线体重、安全下限、可管理空间这些内部计算数字。
    # 具体公斤数留在 shared_analysis 的独立字段里给运营审计，不进正文。
    basis = [
        f"按可管理空间落在{boundaries.get(band, '待确认')}边界确定当前管理带",
        "管理带按可管理空间确定，用户期待斤数和执行状态不参与改带",
    ]
    uplift_reasons = _clean_list(facts.get("protected_lower_uplift_reasons"))
    if uplift_reasons:
        basis.insert(
            1,
            f"安全下限已在自动复核线基础上上调，依据：{'；'.join(uplift_reasons)}",
        )
    return basis


def _force_high_risk_manual_review(
    shared: dict[str, object],
    service_case: ServiceCase,
) -> None:
    """服务单持续高风险必须稳定映射到文档复核标记，模型只能补原因、不能取消。"""

    if not is_high_risk(service_case):
        return
    reasons = _clean_list(shared.get("manual_review_reasons"))
    risk_flags = service_case.risk_flags if isinstance(service_case.risk_flags, list) else []
    for flag in risk_flags:
        if not isinstance(flag, dict) or str(flag.get("level") or "").lower() != "high":
            continue
        reason = str(flag.get("reason") or "").strip()
        if reason and reason not in reasons:
            reasons.append(reason)
    if not reasons:
        reasons.append("服务单已标记持续健康高风险，需重点人工复核")
    shared["manual_review_required"] = True
    shared["manual_review_reasons"] = reasons[:3]
    shared["safety_status"] = "manual_review"


def _apply_canonical_target_contract(
    shared: dict[str, object],
    facts: dict[str, object],
    language: str = "zh",
) -> None:
    """把系统计算目标写成唯一权威值，后续两份文档只能逐字复制。

    运营是否重点关注只影响后台审核与自动发送，不能反过来改写用户的基础或挑战目标。
    """

    base_goal = str(facts.get("basic_target_jin_or_range") or "").strip()
    challenge_goal = str(facts.get("challenge_target_jin_or_review_text") or "").strip()

    facts["basic_target_jin_or_range"] = base_goal
    facts["challenge_target_jin_or_review_text"] = challenge_goal
    if isinstance(facts.get("day28_targets"), dict):
        facts["day28_targets"]["base"] = base_goal
        facts["day28_targets"]["challenge"] = challenge_goal

    for key in (
        "28_day_auto_cap_kg",
        "28_day_display_cap_jin",
        "target_generation_rule_applied",
        "high_commitment_candidate",
        "high_commitment_basis",
        "stretch_review_cap_kg",
        "execution_path",
        "execution_status",
        "standard_complete_rate",
        "high_commitment_complete_rate",
        "document_context",
    ):
        shared[key] = facts.get(key)
    shared["basic_target_jin_or_range"] = base_goal
    shared["challenge_target_jin_or_review_text"] = challenge_goal
    shared["base_goal"] = base_goal
    shared["challenge_goal"] = challenge_goal
    shared["day28_goal"] = (
        f"Standard target: {base_goal}; high-commitment target: {challenge_goal}"
        if language == "en"
        else f"标准目标：{base_goal}；高承诺目标：{challenge_goal}"
    )
    stage_targets = facts.get("stage_targets") if isinstance(facts.get("stage_targets"), dict) else {}
    shared["week5_8_target"] = str(stage_targets.get("week5_8") or "").strip()
    shared["week9_12_target"] = str(stage_targets.get("week9_12") or "").strip()


def _document_generation_block_reasons(facts: dict[str, object]) -> list[str]:
    """只拦真正无法可靠计算的关键资料；普通复测建议不占用运营复核。"""

    reasons: list[str] = []
    if str(facts.get("height_cm") or "").strip() in {"", "未提供", "暂无数据"}:
        reasons.append("缺少可识别的身高，无法计算管理带和28天目标")
    if str(facts.get("weight_kg") or "").strip() in {"", "未提供", "暂无数据"}:
        reasons.append("缺少可识别的当前体重，无法计算管理带和28天目标")
    ratio_text = str(facts.get("waist_height_ratio") or "").strip()
    try:
        ratio = float(ratio_text)
    except (TypeError, ValueError):
        ratio = None
    if ratio is not None and (ratio < 0.2 or ratio > 1.2):
        reasons.append("腰围与身高存在明显单位或录入矛盾，请复测并确认后再生成")
    if not reasons and str(facts.get("weight_loss_band") or "") == UNRESOLVED_BAND:
        reasons.append("关键体征不足，暂时无法确定管理带")
    return reasons[:3]


def _append_waist_remeasurement_note(
    payloads: dict[str, dict],
    facts: dict[str, object],
) -> None:
    """非致命腰高比疑点只提醒用户复测，不自动改带，也不增加运营任务。"""

    if facts.get("waist_height_conflict") is not True:
        return
    energy = payloads.get(DocumentType.ENERGY_RESTART_PRESCRIPTION.value)
    if not isinstance(energy, dict):
        return
    raw = energy.get("missing_information")
    items = list(raw) if isinstance(raw, list) else ([str(raw).strip()] if str(raw or "").strip() else [])
    reason = str(facts.get("waist_height_conflict_reason") or "").strip()
    note = reason or "建议按同一单位复测身高与腰围，确认腰高比数据"
    if note not in items:
        items.append(note)
    energy["missing_information"] = items


def _document_consistency_errors(
    payloads: dict[str, dict],
    shared: dict[str, object],
    facts: dict[str, object],
) -> list[str]:
    """落库前一致性闸门：校验确定性目标确实覆盖了两份正式文件。"""

    errors: list[str] = []
    base = str(facts.get("basic_target_jin_or_range") or "").strip()
    challenge = str(facts.get("challenge_target_jin_or_review_text") or "").strip()
    band = str(facts.get("weight_loss_band") or "").strip()
    if str(shared.get("base_goal") or "").strip() != base:
        errors.append("共享分析的基础目标与系统计算不一致")
    if str(shared.get("challenge_goal") or "").strip() != challenge:
        errors.append("共享分析的挑战目标与系统计算不一致")
    if band == MANAGEMENT_BANDS[0] and (
        _WEIGHT_NUMBER_PATTERN.search(base) or _WEIGHT_NUMBER_PATTERN.search(challenge)
    ):
        errors.append("A带目标中出现减重数字")

    energy = payloads.get(DocumentType.ENERGY_RESTART_PRESCRIPTION.value, {})
    execution = payloads.get(DocumentType.DAY28_EXECUTION_STANDARD.value, {})
    goal_response = energy.get("goal_response") if isinstance(energy.get("goal_response"), dict) else {}
    success_path = energy.get("success_path") if isinstance(energy.get("success_path"), dict) else {}
    expected_day28 = str(shared.get("day28_goal") or "").strip()
    expected_long_term = str(shared.get("long_term_goal") or "").strip()
    if not expected_long_term:
        errors.append("长期目标缺失")
    checks = (
        (goal_response.get("base_goal"), base, "诊断报告基础目标"),
        (goal_response.get("challenge_goal"), challenge, "诊断报告挑战目标"),
        (execution.get("base_goal"), base, "执行文件基础目标"),
        (execution.get("challenge_goal"), challenge, "执行文件挑战目标"),
        (success_path.get("day28_goal"), expected_day28, "诊断报告28天目标"),
        (execution.get("day28_goal"), expected_day28, "执行文件28天目标"),
        (success_path.get("total_goal"), expected_long_term, "诊断报告长期目标"),
        (execution.get("path_goal"), expected_long_term, "执行文件长期目标"),
        (success_path.get("week5_8"), shared.get("week5_8_target"), "诊断报告第5-8周目标"),
        (success_path.get("week9_12"), shared.get("week9_12_target"), "诊断报告第9-12周目标"),
    )
    for actual, expected, label in checks:
        if str(actual or "").strip() != expected:
            errors.append(f"{label}不一致")
    if band == MANAGEMENT_BANDS[0]:
        a_band_target_text = _text_from_payload(
            {
                "shared_long_term_goal": shared.get("long_term_goal"),
                "shared_day28_goal": shared.get("day28_goal"),
                "shared_day28_task": shared.get("day28_task"),
                "goal_response": goal_response,
                "success_path": success_path,
                "expected_changes": energy.get("expected_changes"),
                "execution_day28_goal": execution.get("day28_goal"),
                "execution_path_goal": execution.get("path_goal"),
                "execution_base_goal": execution.get("base_goal"),
                "execution_challenge_goal": execution.get("challenge_goal"),
            }
        )
        if _WEIGHT_NUMBER_PATTERN.search(a_band_target_text):
            errors.append("A带用户可见目标字段中出现减重数字")
    errors.extend(_delivery_instruction_consistency_errors(payloads))
    return errors


_UNSUPPORTED_SPECIAL_STAGE_MARKERS = ("围绝经", "更年期", "绝经")
_PER_MEAL_VEGETABLE_AMOUNT_PATTERN = re.compile(
    r"每餐(?=[^。；\n]{0,24}蔬菜)(?=[^。；\n]{0,24}[一二三四五六七八九十\d]+拳)"
)
_BEDTIME_TARGET_PATTERN = re.compile(
    r"(?P<hour>[01]?\d|2[0-3])\s*(?:[:：]\s*(?P<minute>[0-5]\d))?\s*前\s*(?:上床|躺下|关灯|闭眼)"
)


def _delivery_instruction_consistency_errors(payloads: dict[str, dict]) -> list[str]:
    """Reject only document-format contradictions that cannot be sent as-is.

    This is not a health or product-policy decision. It verifies three explicit
    delivery contracts: do not invent a life stage, vegetable quantities are a
    daily total, and a repeated bedtime target has one value across both files.
    A rejected package is regenerated by the existing bounded retry loop.
    """

    text = _text_from_payload(payloads)
    errors: list[str] = []
    if any(marker in text for marker in _UNSUPPORTED_SPECIAL_STAGE_MARKERS):
        errors.append("交付文件出现无来源的特殊生理阶段判断")
    if _PER_MEAL_VEGETABLE_AMOUNT_PATTERN.search(text):
        errors.append("交付文件将蔬菜份量写成每餐口径")

    bedtime_targets = {
        f"{int(match.group('hour')):02d}:{match.group('minute') or '00'}"
        for match in _BEDTIME_TARGET_PATTERN.finditer(text)
    }
    if len(bedtime_targets) > 1:
        errors.append("交付文件的上床时间不一致")
    return errors


def _strip_internal_review_labels(payloads: dict[str, dict]) -> None:
    """清掉用户可见文档里的内部痕迹：内部术语换成用户话，复核标签按整句丢弃。"""

    for payload in payloads.values():
        if isinstance(payload, dict):
            _strip_internal_labels_in_place(payload)


def _strip_internal_labels_in_place(value: object) -> None:
    if isinstance(value, dict):
        for key, item in list(value.items()):
            if isinstance(item, str):
                value[key] = _scrub_internal_text(item)
            else:
                _strip_internal_labels_in_place(item)
    elif isinstance(value, list):
        for index, item in enumerate(list(value)):
            if isinstance(item, str):
                value[index] = _scrub_internal_text(item)
            else:
                _strip_internal_labels_in_place(item)


# 内部术语 → 用户能听懂的说法。这些词本身不含数字，删整句会丢掉承载结论的正文，
# 所以按词替换而不是丢句。提示词已经明令禁写它们，真实模型照样会写——「保护性下限」
# 是个自然的中文表达，模型认为这样讲用户更懂，只靠提示词封不住。
# 替换词故意不带「体重」二字：模型常写「体重保护性下限」，带上就成了「体重安全体重下限」。
INTERNAL_JARGON = {
    "保护性下限": "安全下限",
    "复核线": "安全下限",
    "安全减重空间": "可减重空间",
    "规划上限": "安全上限",
}


def _scrub_internal_text(text: str) -> str:
    """把用户可见文本里的内部痕迹清掉：术语换成用户话，复核标签整句丢弃。"""

    if not text:
        return text
    for jargon, plain in INTERNAL_JARGON.items():
        if jargon in text:
            text = text.replace(jargon, plain)
    if not _contains_internal_review_label(text):
        return text
    kept = [
        clause
        for clause in re.split(r"(?<=[。；;\n])", text)
        if clause.strip() and not _contains_internal_review_label(clause)
    ]
    return "".join(kept).strip()


SAFETY_FACTOR_SCHEMA = {
    "safety_review_required": "布尔值。命中客户规范的红色风险或安全条件时为 true："
    "心脏支架/冠心病/心律失常、未控制的高血压、明显贫血或异常血常规、甲状腺全切或功能异常、"
    "肿瘤或胃肠术后、备孕/怀孕/哺乳、严重低血糖、青少年（未成年）、进食障碍风险、"
    "低体重仍要求快速减重、胸痛/呼吸困难/黑便/便血/持续呕吐等红旗症状；"
    "以及正在长期服用处方药（降压、降糖、甲状腺、抗凝、精神类、激素类等）——"
    "饮食量和体重变化会影响这类药的剂量，方案必须由人工先核对。"
    "保健品、维生素、蛋白粉和偶尔吃的感冒药不算长期处方药，不因此置 true。"
    "它只用于强制人工复核，不改变管理带。仅仅是资料缺失、数据冲突或执行困难不算，一律 false",
    "safety_review_reasons": ["触发上一条的1-3条原因，引用问卷原文事实，只给运营看"],
    "muscle_insufficiency": "布尔值。有可靠体脂率或肌肉数据、且显示肌肉量明显不足时为 true。"
    "没有体脂/肌肉数据时一律 false，不得凭体重或BMI推断",
    "disease_or_medication_limit": "布尔值。存在重大疾病或长期用药、需要更保守的体重下限时为 true",
    "recovery_risk": "布尔值。存在月经紊乱或恢复力明显不足的证据时为 true。"
    "当前问卷未采集月经相关信息，仅当用户在健康情况自由填写里主动提到时才为 true，否则 false",
    "doctor_specified_lower_weight_kg": "数字或 null。用户明确写了医生交代的体重下限（公斤）时填该数字；"
    "没有明确数字时必须填 null，禁止自己推算",
    "waist_height_conflict": "布尔值。仅当腰围与身高存在明显测量、录入或单位矛盾时为 true。"
    "腰高比偏高本身是可能真实存在的腹型肥胖信号，不是数据冲突，必须填 false",
    "waist_height_conflict_reason": "字符串或 null。仅在上一字段为 true 时，用一句话说明建议复测什么；"
    "不要写医学诊断，也不要要求运营人工复核",
    "persistent_risk_flags": [
        {
            "code": "仅可为 pregnancy / lactation / chronic_disease / long_term_medication / "
            "hormone_medication / liver_kidney / stone / severe_test_abnormality / depression。"
            "只有问卷明确披露、会长期影响服务安全边界的事实才可输出；"
            "不确定、仅有短期不适、普通饮食限制、一般情绪压力一律不输出",
            "reason": "一句话说明为何属于持续健康风险",
            "evidence": "问卷原文中的直接证据，不得改写或补充",
        }
    ],
    "evidence_notes": ["以上每个 true 判断对应的问卷原文依据，1-4条；全部为 false 时输出空列表"],
}


def _normalize_safety_factors(payload: object) -> dict[str, object]:
    """把模型输出收敛成确定性布尔/数字，缺字段一律按最宽松（不上调）处理。

    这里刻意不做兜底推断：抽不到证据就不上调下限、不改带，让用户走正常业务带位，
    而不是靠猜把人往 A 带推。
    """

    data = payload if isinstance(payload, dict) else {}
    factors: dict[str, object] = {
        "safety_review_required": _boolean_value(data.get("safety_review_required")),
        "safety_review_reasons": _clean_list(data.get("safety_review_reasons"))[:3],
        "evidence_notes": _clean_list(data.get("evidence_notes"))[:4],
    }
    for key in ("muscle_insufficiency", "disease_or_medication_limit", "recovery_risk"):
        factors[key] = _boolean_value(data.get(key))
    factors["persistent_risk_flags"] = _normalize_persistent_risk_flags(
        data.get("persistent_risk_flags")
    )
    factors["waist_height_conflict"] = _boolean_value(data.get("waist_height_conflict"))
    conflict_reason = str(data.get("waist_height_conflict_reason") or "").strip()
    factors["waist_height_conflict_reason"] = conflict_reason if factors["waist_height_conflict"] else ""
    factors["doctor_specified_lower_weight_kg"] = _positive_number_or_none(
        data.get("doctor_specified_lower_weight_kg")
    )
    return factors


def _normalize_persistent_risk_flags(value: object) -> list[dict[str, str]]:
    """收敛问卷中的持续风险，避免把群聊风控的瞬时语义带入入营。"""

    if not isinstance(value, list):
        return []
    flags: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "").strip()
        reason = str(item.get("reason") or "").strip()
        evidence = str(item.get("evidence") or "").strip()
        # 没有可回查的原文证据就不能给用户留下持续高风险标记。
        if code not in PERSISTENT_INTAKE_RISK_CODES or not reason or not evidence:
            continue
        signature = (code, evidence)
        if signature in seen:
            continue
        seen.add(signature)
        flags.append(
            {
                "code": code,
                "level": "high",
                "reason": reason[:200],
                "evidence": evidence[:300],
                "source": "questionnaire",
            }
        )
    return flags[:4]


def _apply_persistent_intake_risk_flags(
    service_case: ServiceCase,
    safety_factors: dict[str, object],
) -> None:
    """把同一轮安全提取出的长期风险写入服务单。

    该标记供文档自动发送保护和后续群聊安全边界复用；只累加有证据的新事实，
    绝不因为一次重新生成而清掉已有风险或制造重复记录。
    """

    incoming = safety_factors.get("persistent_risk_flags")
    if not isinstance(incoming, list):
        return
    existing = service_case.risk_flags if isinstance(service_case.risk_flags, list) else []
    merged = [item for item in existing if isinstance(item, dict)]
    signatures = {
        (str(item.get("code") or ""), str(item.get("evidence") or ""))
        for item in merged
    }
    added = False
    for flag in incoming:
        if not isinstance(flag, dict):
            continue
        signature = (str(flag.get("code") or ""), str(flag.get("evidence") or ""))
        if not all(signature) or signature in signatures:
            continue
        signatures.add(signature)
        merged.append(flag)
        added = True
    if added:
        service_case.risk_flags = merged
    if incoming:
        service_case.risk_level = "high"


def _positive_number_or_none(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


async def _extract_safety_factors(
    *,
    ai: AIService,
    service_case: ServiceCase,
    readable_answers: str,
    measurement_facts: dict[str, object] | None = None,
) -> dict[str, object]:
    """先抽安全因子，再算带位——顺序不能反。

    保护性下限的上调依据（疾病、用药、医生限制）散在问卷的自由文本里，只能靠模型
    结构化抽取。而带位一旦确定就会被写进全文的目标和路径，所以必须在生成两份文件
    之前定下来；放在主调用里输出会导致正文按旧带写完、带位事后才变，全文不一致。

    抽取失败不阻断入营：退回全 false，等同于历史行为（BMI 20 下限、纯数值分带），
    并留日志。宁可少上调，也不要因为一次模型抖动把用户挡在交付之外。
    """

    try:
        text = await ai.generate_text(
            scenario="intake_safety_factors",
            temperature=0.0,
            service_case_id=service_case.id,
            system_prompt=(
                "你是健康服务的安全审阅员。只根据问卷中明确出现的事实做结构化判断，"
                "不推测、不补编、不做医学诊断。只输出一个 JSON 对象，不要解释或 Markdown。"
                "判断不确定时一律输出 false 或 null——漏判会由人工复核兜底，"
                "误判会错误增加运营复核工作，代价更大。"
            ),
            user_prompt=(
                f"请严格按这个字段结构填值：\n"
                f"{json.dumps(SAFETY_FACTOR_SCHEMA, ensure_ascii=False, indent=2)}\n\n"
                f"系统已归一化的体征数据（仅辅助核查单位与组合，不据此做医学诊断）：\n"
                f"{json.dumps(measurement_facts or {}, ensure_ascii=False, indent=2)}\n\n"
                f"问卷结果：\n{readable_answers}"
            ),
        )
        factors = _normalize_safety_factors(parse_json_object(text))
    except Exception:
        logger.exception(
            "safety factor extraction failed for case %s; falling back to no adjustment",
            service_case.id,
        )
        factors = _normalize_safety_factors(None)
    return factors


async def _generate_intake_analysis_package(
    *,
    session: AsyncSession,
    ai: AIService,
    service_case: ServiceCase,
    user_name: str,
    submission: QuestionnaireSubmission,
) -> dict[str, object]:
    case_lang = getattr(service_case, "language", "zh") or "zh"
    if case_lang not in ("zh", "en"):
        case_lang = "zh"
    fields = await get_active_questionnaire_fields(session, "intake", language=case_lang)
    readable_answers = _safety_model_readable_answers(fields, submission.answers_json)
    preliminary_facts = build_intake_fact_summary(fields, submission.answers_json, language=case_lang)
    safety_factors = await _extract_safety_factors(
        ai=ai,
        service_case=service_case,
        readable_answers=readable_answers,
        measurement_facts={
            key: preliminary_facts.get(key)
            for key in ("height_cm", "weight_kg", "bmi", "waist_cm", "waist_height_ratio")
        },
    )
    _apply_persistent_intake_risk_flags(service_case, safety_factors)
    preliminary_block_reasons = _document_generation_block_reasons(preliminary_facts)
    if preliminary_block_reasons:
        # 即使关键体征不足而不能生成文件，问卷中的明确持续风险也已被同一次安全提取保留；
        # 这里只跳过后续正式文档生成，避免继续消耗一次大模型调用。
        raise DocumentGenerationBlocked(preliminary_block_reasons)
    intake_facts = build_intake_fact_summary(fields, submission.answers_json, safety_factors, language=case_lang)
    block_reasons = _document_generation_block_reasons(intake_facts)
    if block_reasons:
        raise DocumentGenerationBlocked(block_reasons)

    document_facts = _document_model_intake_facts(intake_facts)
    document_answers = _document_model_readable_answers(fields, submission.answers_json)

    if case_lang == "en":
        base_user_prompt = (
            f"Current User: {user_name}\n"
            f"Service Case: {service_case.case_no}\n\n"
            f"CRITICAL INSTRUCTION: You MUST output all text, descriptions, analysis, rules, and fields in professional, natural ENGLISH ONLY. Do NOT output Chinese text.\n\n"
            f"Please strictly populate values following this package schema structure:\n{json.dumps(_intake_package_schema(language='en'), ensure_ascii=False, indent=2)}\n\n"
            f"intake_facts (Core baseline facts):\n{json.dumps(document_facts, ensure_ascii=False, indent=2)}\n\n"
            f"Questionnaire Answers:\n{document_answers}"
        )
    else:
        base_user_prompt = (
            f"当前用户：{user_name}\n"
            f"服务单：{service_case.case_no}\n\n"
            f"请严格按这个 package 顶层字段结构填值：\n{json.dumps(_intake_package_schema(language='zh'), ensure_ascii=False, indent=2)}\n\n"
            f"intake_facts（共同事实摘要，优先级最高）：\n{json.dumps(document_facts, ensure_ascii=False, indent=2)}\n\n"
            f"问卷结果：\n{document_answers}"
        )
    last_error: RetryableAIContentError | RetryableIntakePackageError | None = None
    # 每次尝试的拒绝原因都要留下：只留最后一次的话，"第一次为什么被判不合格"只存在于
    # 运行日志里，容器日志一轮转就永久查不到（2026-08-17 排查服务单 308 时正是如此）。
    attempt_errors: list[str] = []
    for attempt in range(1, INTAKE_DOCUMENT_MAX_ATTEMPTS + 1):
        retry_instruction = ""
        if last_error is not None:
            if case_lang == "en":
                retry_instruction = (
                    "\n\n[REGENERATION INSTRUCTION]\n"
                    f"Previous output failed integrity validation: {str(last_error)[:500]}.\n"
                    "Please regenerate the complete JSON in natural English from scratch. "
                    "Must include shared_analysis, profile, energy_restart_prescription, and day28_execution_standard. "
                    "Output ONLY valid JSON without Markdown formatting or wrappers. Ensure all text and fields are in ENGLISH."
                )
            else:
                retry_instruction = (
                    "\n\n【重新生成要求】\n"
                    f"上一次输出未通过完整性校验：{str(last_error)[:500]}。\n"
                    "请从头重新生成完整 JSON；必须同时包含 shared_analysis、profile、"
                    "energy_restart_prescription 和 day28_execution_standard，"
                    "且最终只输出完整有效的 JSON。不要复述字段结构或问卷，"
                    "在字段齐全的前提下压缩每个文本字段，避免再次截断。"
                )
        try:
            text = await ai.generate_text(
                scenario="intake_doc",
                temperature=0.3,
                service_case_id=service_case.id,
                system_prompt=_intake_generation_system_prompt(
                    language=getattr(service_case, "language", "zh") or "zh"
                ),
                user_prompt=base_user_prompt + retry_instruction,
            )
            return _normalize_intake_analysis_package(
                text=text,
                service_case=service_case,
                user_name=user_name,
                intake_facts=intake_facts,
            )
        except (RetryableAIContentError, RetryableIntakePackageError) as exc:
            last_error = exc
            attempt_errors.append(f"第{attempt}次[{type(exc).__name__}]{str(exc)[:400]}")
            logger.warning(
                "intake document content invalid; regenerating service_case_id=%s "
                "attempt=%s/%s error=%s",
                service_case.id,
                attempt,
                INTAKE_DOCUMENT_MAX_ATTEMPTS,
                exc,
            )
            if attempt >= INTAKE_DOCUMENT_MAX_ATTEMPTS:
                # 内容不合格不换模型（原因见 AIService._fallback_eligible），所以三次都在
                # 同一档模型上重试；最终失败时把每一次的原因一起抛出，供服务单备注留证。
                raise RetryableIntakePackageError(
                    f"入营交付文件 {INTAKE_DOCUMENT_MAX_ATTEMPTS} 次生成均未通过校验："
                    + "；".join(attempt_errors)
                ) from exc
    raise RuntimeError("intake document generation retry loop exited unexpectedly")


def _normalize_intake_analysis_package(
    *,
    text: str,
    service_case: ServiceCase,
    user_name: str,
    intake_facts: dict[str, object],
) -> dict[str, object]:
    """把一次模型正文收敛成可落库 package；内容问题交给上层重新生成。"""

    case_lang = getattr(service_case, "language", "zh") or "zh"
    if case_lang not in ("zh", "en"):
        case_lang = "zh"
    payload = parse_json_object(text)
    if isinstance(payload.get("intake_analysis_package"), dict):
        payload = payload["intake_analysis_package"]
    shared = payload.get("shared_analysis") if isinstance(payload.get("shared_analysis"), dict) else {}
    document_root = payload.get("documents") if isinstance(payload.get("documents"), dict) else payload
    # 运营复核是风控链的确定性结果，不接受文档模型的猜测或输出。
    for key in _MODEL_OWNED_INTERNAL_FIELDS:
        shared.pop(key, None)
    # 四带结论由安全减重空间确定性计算，LLM只负责解释和选择当前主线。
    # 持续高风险与A-D带并存；服务单高风险无条件点亮运营页面的重点关注。
    _apply_deterministic_band(shared, intake_facts)
    _force_high_risk_manual_review(shared, service_case)
    _apply_canonical_target_contract(shared, intake_facts, language=case_lang)
    activity_frequency = _normalize_shared_activity_frequency(shared)
    shared["ready_for_document_generation"] = True
    shared["document_generation_block_reasons"] = []
    document_payloads = _normalize_combined_document_payload(
        document_root,
        user_name,
        shared_analysis=shared,
    )
    if not document_payloads:
        raise RetryableIntakePackageError(
            "intake analysis package must contain both delivery documents"
        )
    _apply_intake_facts_to_document_payloads(document_payloads, intake_facts, language=case_lang)
    _append_waist_remeasurement_note(document_payloads, intake_facts)
    _apply_shared_activity_frequency_to_documents(document_payloads, activity_frequency)
    consistency_errors = _document_consistency_errors(document_payloads, shared, intake_facts)
    if consistency_errors:
        raise RetryableIntakePackageError(
            f"交付文件目标一致性校验未通过：{'；'.join(consistency_errors)}"
        )
    # 人工复核是内部运营状态，不是管理带：即使模型仍把内部标签写进正文，也要在落库前清出用户可见内容。
    _strip_internal_review_labels(document_payloads)
    # 五项打卡在这里收敛成严格 5 项结构化数组，之后画像、执行文档和 H5 问卷都读同一份结构。
    _normalize_daily_checkin_in_payloads(document_payloads, intake_facts, language=case_lang)
    profile_json = _normalize_profile_from_package(payload, document_payloads, intake_facts, language=case_lang)
    _apply_shared_activity_frequency_to_profile(profile_json, activity_frequency)
    summary = str(profile_json.get("summary") or _profile_summary_from_documents(document_payloads) or "")[:2000]
    if not summary:
        raise RetryableIntakePackageError("intake analysis package generated empty profile content")
    return {
        "profile_json": profile_json,
        "summary": summary,
        "document_payloads": document_payloads,
        "intake_facts": intake_facts,
    }


def _intake_package_schema(language: str = "zh") -> dict[str, object]:
    if language == "en":
        return {
            "shared_analysis": {
                "weight_band": "Deterministic management band from system. Must strictly be one of: Band A | Body Rejuvenation & Sculpting / Band B | Gentle Energy Kickstart / Band C | Targeted Fat Loss / Band D | Systematic Weight Loss. Do not alter or output internal review status.",
                "band_basis": ["System deterministic band basis in English, model must not rewrite"],
                "body_trend": "3-month trend from questionnaire: Improving / Basically stable / Slowly increasing / Significantly worsening / Insufficient information",
                "body_trend_summary": "One sentence explaining recent 3-month trend in English",
                "body_trend_direct_evidence": "1 direct factual evidence supporting trend direction in English",
                "body_trend_support_signals": ["1-2 supporting signals from current state in English"],
                "body_trend_confidence": "high / medium / low",
                "core_problem": "Core problem identified purely from questionnaire facts in English",
                "current_mainline": "Must be one of: Muscle Preservation & Fat Loss / Rhythm Stabilization / Sleep Recovery / Dietary Structure Repair / Systematic Management",
                "base_goal": "Verbatim copy of intake_facts.day28_targets.base (already English, figures in lbs); for Band A no numeric weight loss is allowed",
                "challenge_goal": "Verbatim copy of intake_facts.day28_targets.challenge (already English, figures in lbs)",
                "high_commitment_candidate": "Copy from intake_facts.high_commitment_candidate",
                "high_commitment_basis": ["Copy from system screening basis"],
                "stretch_review_cap_kg": "Copy from intake_facts.stretch_review_cap_kg",
                "execution_path": "Fixed 'standard' during initial generation",
                "execution_status": "Fixed 'pending'",
                "standard_complete_rate": "Fixed 0.60",
                "high_commitment_complete_rate": "Fixed 0.85",
                "document_context": "Structured projection of questionnaire answers in English",
                "28_day_auto_cap_kg": "Copy from intake_facts (kg)",
                "28_day_display_cap_jin": "Copy from intake_facts as-is — the value is already in lbs for English cases despite the internal field name. Do not convert it.",
                "basic_target_jin_or_range": "Verbatim copy from intake_facts (already English, figures in lbs)",
                "challenge_target_jin_or_review_text": "Verbatim copy from intake_facts (already English, figures in lbs)",
                "target_generation_rule_applied": "Verbatim copy from intake_facts",
                "long_term_goal": "Long-term direction in English, must include non-scale outcomes; no numeric weight loss for Band A",
                "day28_goal": "28-day goal in English; for Band A focus on stability, energy, sleep, habits",
                "day28_task": "What to solve first in these 28 days in English",
                "execution_principle": "Core execution principle in English",
                "activity_plan": {
                    "status": "planned / paused",
                    "sessions_per_week": "2, 3, or 4 when planned",
                },
                "evidence_notes": ["3-5 questionnaire evidence bullet points in English"],
            },
            "profile": {
                "summary": "Backend natural language profile summary in English, within 200 words",
                "tags": ["2-4 short English tags, e.g. Band B, Late Sleep Habit, Afternoon Fatigue, Sedentary Worker"],
                "overview": {
                    "gender_age": "Gender / Age matching intake_facts (e.g. Male / 30)",
                    "height_weight": "Height / Weight matching intake_facts (e.g. 175cm / 143.3 lbs)",
                    "bmi": "BMI matching intake_facts.bmi",
                    "waist": "Waist matching intake_facts",
                    "weight_loss_band": "Management band consistent with shared_analysis.weight_band",
                },
                "state": "Current state assessment in English",
                "main_problem": "Core main problem in English",
                "day28_goal": "28-day goal in English",
                "need_analysis": {
                    "surface_needs": ["User's stated surface needs in English"],
                    "deep_needs": ["Underlying real service needs inferred from questionnaire evidence in English"],
                    "priority_need": "Top priority need in English",
                    "system_outputs": ["System delivery directions in English"],
                    "next_actions": ["Top 1-3 immediate actions in English; exercise frequency must match shared_analysis.activity_plan.sessions_per_week"],
                    "evidence_notes": ["2-4 questionnaire evidence bullet points in English"],
                },
                "notes": "Coaching tips for human operators in English",
                "occupation_fit": "How occupation and lifestyle context impact execution in English",
                "history_reuse": "Reusable experiences and methods to avoid in English",
                "outcome_dashboard": "Tracking summary for 1 main outcome and up to 2 supporting outcomes in English",
                "execution_contract_summary": "Summary of 60% standard / 85% high commitment check-in agreement in English",
            },
            "documents": combined_prompt_schema(language="en"),
        }

    return {
        "shared_analysis": {
            "weight_band": "系统已经按安全减重空间算好的最终业务管理带，只能逐字复制：A带｜修复塑形 / B带｜轻盈启动 / C带｜明确减脂 / D带｜系统减重。"
            "模型不得改带；不输出任何风险、人工复核或内部运营状态",
            "band_basis": ["系统给出的管理带解释依据，模型不得改写"],
            "body_trend": "仅依据入营问卷反映的近3个月趋势：正在改善 / 基本稳定 / 缓慢变沉 / 明显恶化 / 信息不足",
            "body_trend_summary": "一句话解释近3个月趋势；不得写入营后的变化",
            "body_trend_direct_evidence": "1条能证明变化方向的直接趋势事实，优先来自问卷「最近3个月体重变化」。"
            "当前BMI/体重/腰围是单点状态，不能放在这里；问卷没有前后对比信息时写明未提供",
            "body_trend_support_signals": ["1-2条当前状态支持信号：腰腹体感、睡眠、精力、食欲边界、晚间失控、活动量等"],
            "body_trend_confidence": "high / medium / low",
            "core_problem": "核心问题，只基于问卷事实",
            "current_mainline": "当前唯一主线，只能取：保肌减脂 / 节律稳定 / 睡眠修复 / 饮食结构修复 / 系统化管理；主要依据睡眠、精力、饮食、食欲边界、晚间失控、运动和恢复状态选择",
            "base_goal": "标准目标；逐字复制系统目标；用户没有明确数字时不得编造斤数；A带禁止任何减重数字",
            "challenge_goal": "高承诺目标；仅候选人展示并逐字复制系统目标",
            "high_commitment_candidate": "逐字复制 intake_facts.high_commitment_candidate，模型不得自行筛选",
            "high_commitment_basis": ["逐字复制系统给出的筛选依据，不得补充职业推断"],
            "stretch_review_cap_kg": "逐字复制 intake_facts.stretch_review_cap_kg，模型不得重算",
            "execution_path": "文档生成时固定 standard，待用户在群内明确确认后才可变更",
            "execution_status": "文档生成时固定 pending",
            "standard_complete_rate": "固定0.60",
            "high_commitment_complete_rate": "固定0.85",
            "document_context": "系统对白名单问卷答案的结构化投影；只据原答案概括，不新增事实或因果",
            "28_day_auto_cap_kg": "逐字复制 intake_facts.28_day_auto_cap_kg，模型不得重算",
            "28_day_display_cap_jin": "逐字复制 intake_facts.28_day_display_cap_jin，模型不得重算",
            "basic_target_jin_or_range": "逐字复制 intake_facts.basic_target_jin_or_range",
            "challenge_target_jin_or_review_text": "逐字复制 intake_facts.challenge_target_jin_or_review_text",
            "target_generation_rule_applied": "逐字复制 intake_facts.target_generation_rule_applied",
            "long_term_goal": "长期方向，必须包含非体重结果；A带禁止任何减重数字",
            "day28_goal": "28天目标；A带只写体重稳定或恢复、体态、力量、睡眠、精力和执行目标，不写减重数字",
            "day28_task": "这28天先解决什么",
            "execution_principle": "执行总原则",
            "activity_plan": {
                "status": "planned / paused；只有问卷明确需要暂停活动时才为 paused",
                "sessions_per_week": "planned 时只能取2、3或4；结合当前运动基础与可执行性选一个，28天内不分首周/后续周。profile和两份文件必须逐字使用这个次数",
            },
            "evidence_notes": ["关键判断对应的问卷证据，3-5条"],
        },
        "profile": {
            "summary": "后台自然语言画像摘要，200字以内",
            "tags": ["用于高层特征识别的标签列表，包含2-4个短词，例如：B带、晚睡习惯、下午困倦、久坐人群"],
            "overview": {
                "gender_age": "性别 / 年龄，必须与 intake_facts 一致",
                "height_weight": "身高 / 体重，必须与 intake_facts 一致",
                "bmi": "必须与 intake_facts.bmi 一致",
                "waist": "腰围，必须与 intake_facts 一致",
                "weight_loss_band": "与 shared_analysis.weight_band 一致（系统按安全减重空间确定的最终管理带）",
            },
            "state": "当前状态评估",
            "main_problem": "核心主要问题",
            "day28_goal": "28天目标",
            "need_analysis": {
                "surface_needs": ["用户表面表达的需求，例如减重、睡好、精神好"],
                "deep_needs": ["表层需求背后的真实服务需求，只基于问卷事实推断"],
                "priority_need": "当前最优先满足的一个需求",
                "system_outputs": ["系统应交付的方案、动作、产品调优、陪跑或复盘方向"],
                "next_actions": ["接下来最该推进的1-3个动作；涉及运动时频次只能使用 shared_analysis.activity_plan.sessions_per_week"],
                "evidence_notes": ["对应的问卷证据，2-4条"],
            },
            "notes": "AI陪跑建议，供运营参考",
            "occupation_fit": "职业与家庭场景如何影响执行，只基于白名单原答案",
            "history_reuse": "既往经验中可复用与不建议重复的部分",
            "outcome_dashboard": "1项主要成果、最多2项辅助成果的简短跟踪摘要",
            "execution_contract_summary": "标准档60%、高承诺档85%的完整打卡约定摘要",
        },
        "documents": combined_prompt_schema(language="zh"),
    }


def _normalize_profile_from_package(
    payload: dict,
    document_payloads: dict[str, dict],
    intake_facts: dict[str, object],
    language: str = "zh",
) -> dict[str, object]:
    profile = payload.get("profile") if isinstance(payload.get("profile"), dict) else {}
    if not profile:
        profile = _profile_from_document_payloads(document_payloads)
    shared = payload.get("shared_analysis") if isinstance(payload.get("shared_analysis"), dict) else {}
    if shared:
        profile.setdefault("main_problem", shared.get("core_problem"))
        profile.setdefault("day28_goal", shared.get("day28_goal"))
    profile = sanitize_profile_json(dict(profile), intake_facts, language=language)
    # 风险与复核属于运营风控链，不能由交付文件模型生成或在画像自由文本中混写。
    profile.pop("risks", None)
    if shared:
        profile["safety_status"] = str(shared.get("safety_status") or "standard")
        profile["management_band"] = str(
            shared.get("weight_band")
            or intake_facts.get("weight_loss_band")
            or ("To be assessed with height and weight" if language == "en" else "需补充身高体重后判断")
        )
        profile["body_trend"] = str(shared.get("body_trend") or ("Insufficient information" if language == "en" else "信息不足"))
        direct_evidence, support_signals = _trend_evidence_parts(shared)
        profile["body_trend_direct_evidence"] = direct_evidence
        profile["body_trend_support_signals"] = support_signals
        # 兼容字段：历史消费方（后台画像展示、阶段报告上下文）仍读这个合并列表。
        profile["body_trend_evidence"] = [item for item in [direct_evidence, *support_signals] if item]
        # 内部运营字段：只给后台和人工复核看，不进入用户交付文件正文。
        profile["manual_review_required"] = _boolean_value(
            shared.get("manual_review_required")
        )
        profile["manual_review_reasons"] = _clean_list(shared.get("manual_review_reasons"))
        profile["ready_for_document_generation"] = _boolean_value(
            shared.get("ready_for_document_generation")
        )
        profile["document_generation_block_reasons"] = _clean_list(
            shared.get("document_generation_block_reasons")
        )
        profile["band_confidence"] = str(shared.get("band_confidence") or "").strip()
        profile["provisional_band"] = _boolean_value(shared.get("provisional_band"))
        for key in BAND_CALCULATION_FIELDS:
            profile[key] = shared.get(key)
    profile["need_analysis"] = _normalize_need_analysis(profile.get("need_analysis"), intake_facts, shared, language=language)
    profile.setdefault("notes", "Provide light-touch coaching around execution friction points without overloading actions." if language == "en" else "建议围绕问卷中明确的执行卡点进行轻量陪跑，避免一次性增加太多动作。")
    tags = payload.get("profile", {}).get("tags") if isinstance(payload.get("profile"), dict) else None
    profile["tags"] = _clean_list(tags) if isinstance(tags, list) else []
    profile["personalized_checkin_fields"] = (
        _personalized_checkin_fields_from_documents(document_payloads, language=language)
        or _fallback_personalized_checkin_fields(intake_facts, language=language)
    )
    return profile


def _profile_from_document_payloads(document_payloads: dict[str, dict]) -> dict[str, object]:
    energy = document_payloads.get(DocumentType.ENERGY_RESTART_PRESCRIPTION.value, {})
    execution = document_payloads.get(DocumentType.DAY28_EXECUTION_STANDARD.value, {})
    success_path = energy.get("success_path") if isinstance(energy.get("success_path"), dict) else {}
    summary = _text_from_payload(energy.get("one_sentence")) or _text_from_payload(energy.get("current_state"))
    return {
        "summary": summary,
        "state": _text_from_payload(energy.get("current_state")),
        "main_problem": _text_from_payload(energy.get("priority_direction")) or summary,
        "day28_goal": _text_from_payload(success_path.get("day28_goal") or execution.get("day28_goal")),
        "notes": _text_from_payload(energy.get("special_reminder")) or "建议围绕问卷中的核心执行卡点进行陪跑。",
    }


def _normalize_need_analysis(value: object, facts: dict[str, object], shared: dict[str, object], language: str = "zh") -> dict[str, object]:
    """Keep the need engine structured even when the LLM omits optional fields.

    This is intentionally not a keyword classifier. It preserves the user's
    own stated needs and lets the generation prompt infer deeper needs from
    questionnaire evidence.
    """

    analysis = value if isinstance(value, dict) else {}
    is_en = language == "en"
    main_needs = _split_need_text(facts.get("main_needs"))
    expectation = _split_need_text(facts.get("day28_expectation"))
    surface_needs = _clean_list(analysis.get("surface_needs")) or main_needs or expectation
    deep_needs = _clean_list(analysis.get("deep_needs"))
    outputs = _clean_list(analysis.get("system_outputs"))
    actions = _clean_list(analysis.get("next_actions"))
    evidence = _clean_list(analysis.get("evidence_notes"))

    if is_en:
        evidence_fallback = _clean_list(
            [
                f"Service needs: {facts.get('main_needs')}",
                f"28-day expectation: {facts.get('day28_expectation')}",
                f"Execution worry: {facts.get('adherence_worry')}",
            ]
        )
    else:
        evidence_fallback = _clean_list(
            [
                f"服务需求：{facts.get('main_needs')}",
                f"28天期待：{facts.get('day28_expectation')}",
                f"执行担心：{facts.get('adherence_worry')}",
            ]
        )

    return {
        "surface_needs": surface_needs or (["To be supplemented"] if is_en else ["待补充"]),
        "deep_needs": deep_needs or (["To be observed via daily check-ins"] if is_en else ["待结合聊天和打卡继续识别"]),
        "priority_need": str(
            analysis.get("priority_need")
            or shared.get("core_problem")
            or facts.get("main_needs")
            or ("To be supplemented" if is_en else "待补充")
        ),
        "system_outputs": outputs or _clean_list(
            [
                shared.get("day28_goal"),
                shared.get("execution_principle"),
            ]
        ) or (["To be refined based on plan execution"] if is_en else ["待根据方案生成结果补充"]),
        "next_actions": actions or (["Complete first 3 days of plan and share feedback"] if is_en else ["按已生成方案先完成入营前3天执行与反馈"]),
        "evidence_notes": evidence or evidence_fallback,
    }


def _trend_evidence_parts(shared: dict[str, object]) -> tuple[str, list[str]]:
    """拆出「1条直接趋势证据」与「1-2条当前状态支持信号」。

    直接趋势证据要能证明变化方向（优先来自近3个月体重变化）；当前 BMI/腰围这类单点
    状态只能作为支持信号。历史输出只有合并的 body_trend_evidence，此时把第一条当作
    趋势证据、其余当作支持信号，保持旧文档可读。
    """

    direct = str(shared.get("body_trend_direct_evidence") or "").strip()
    supports = _clean_list(shared.get("body_trend_support_signals"))[:2]
    if direct or supports:
        return direct, supports
    legacy = _clean_list(shared.get("body_trend_evidence"))
    if not legacy:
        return "", []
    return legacy[0], legacy[1:3]


def _split_need_text(value: object) -> list[str]:
    text = str(value or "").strip()
    if not text or text in {"未提供", "暂无数据"}:
        return []
    return [item.strip() for item in re.split(r"[、,，；;|/]+", text) if item.strip()]


def _clean_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return _split_need_text(value)
    return []


def _boolean_value(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    if isinstance(value, (int, float)):
        return value != 0
    return False


def _profile_summary_from_documents(document_payloads: dict[str, dict]) -> str:
    profile = _profile_from_document_payloads(document_payloads)
    return str(profile.get("summary") or profile.get("main_problem") or "")


def _fallback_personalized_checkin_fields(
    facts: dict[str, object],
    language: str = "zh",
) -> list[dict[str, object]]:
    weight = _blank_if_missing(facts.get("weight_kg"))
    weight_kg = None
    if weight:
        try:
            weight_kg = float(weight)
        except (ValueError, TypeError):
            weight_kg = None
    return personalized_checkin_fields_with_weight(weight_kg, language=language)


def personalized_checkin_fields_from_document_payloads(
    document_payloads: dict[str, dict],
    language: str = "zh",
) -> list[dict[str, object]]:
    return _personalized_checkin_fields_from_documents(document_payloads, language=language)


def _personalized_checkin_fields_from_documents(
    document_payloads: dict[str, dict],
    language: str = "zh",
) -> list[dict[str, object]]:
    """把执行建议书的五项打卡转成问卷字段。

    读取路径：不套用生成期的文风规则、也不补兜底题，拿不到完整 5 项就返回空列表，
    让上层继续回落画像字段或默认清单——这样历史文档和历史打卡展示保持原样。
    """

    execution = document_payloads.get(DocumentType.DAY28_EXECUTION_STANDARD.value, {})
    if not isinstance(execution, dict):
        return []
    daily_items = normalize_daily_checkin_items(
        _daily_checkin_value_from_execution(execution),
        strict_style=False,
        allow_fallback=False,
        language=language,
    )
    fields = []
    for index, item in enumerate(daily_items, start=1):
        answer_type = str(item.get("answer_type") or "text")
        title = str(item.get("title") or "")
        fields.append(
            {
                "id": f"personalized_checkin_{index}",
                "key": f"personalized_checkin_{index}",
                "type": checkin_field_type_for(answer_type),
                "title": title,
                "label": title,
                "subtitle": "",
                "required": True,
                "options": checkin_options_for(answer_type, language=language),
                "answer_type": answer_type,
                "category": str(item.get("category") or ""),
                "order": index,
                "example_answer": str(item.get("example_answer") or ""),
            }
        )
    return fields


DAILY_CHECKIN_SIZE = 5
CHECKIN_ANSWER_TYPES = ("yes_no", "score_1_5", "number", "text")
CHECKIN_CATEGORIES = ("product", "diet", "activity", "sleep", "state")
CHECKIN_YES_NO_OPTIONS = ["是", "否"]
CHECKIN_YES_NO_OPTIONS_EN = ["Yes", "No"]
CHECKIN_SCORE_OPTIONS = ["1", "2", "3", "4", "5"]
MAX_CHECKIN_TITLE_CHARS = 80

# 「问题的形状」标记：用来判断一条题面是不是真的能被回答，而不是栏目标题或说明文字。
# 这不是业务分类，只是把模型输出约束成可转成问卷字段的结构；类别一律由 category 字段承载。
_JUDGEMENT_MARKERS = ("是否", "有无", "有没有", "did you", "have you", "are you", "is there", "were you", "whether")
_SCORE_MARKERS = ("评分", "打分", "score", "rate", "rating", "1-5")
# 问"多少"就是要填数字。英文题面里这类问法几乎一定同时带 "did you / have you"
# （"How many minutes of walking did you complete?"），所以数量标记必须优先于判断标记，
# 否则数字题会被判成是否题、或因"类型与题面不匹配"整条丢掉换成兜底题。
_QUANTITY_MARKERS = (
    "多少",
    "几点",
    "几次",
    "几分",
    "how many",
    "how much",
    "how long",
    "what time",
    "number of",
)
_REQUEST_MARKERS = ("请", *_QUANTITY_MARKERS, "please", "?", "？")
# 一条题面里出现两个及以上回答意图就是复合问题，必须拆开或只留优先级更高的那个。
_SECOND_INTENT_MARKERS = ("请记录", "请填写", "并填写", "并记录", "同时记录", "同时填写", "并写下")
# 并列连词把两个独立动作串成一题（如「20点前完成并且主食≤1拳」同时问时间和饮食结构），
# 必须拆开或只留优先级更高的一个。「或」是同一动作的多种达标方式，不算复合。
_COMPOSITE_CONJUNCTIONS = ("并且", "同时", "以及", "，并", ",并", "并且要", "而且")
# 栏目标题/说明文字，不是题目。这是唯一需要显式识别的形状，其余判断都靠上面的回答意图标记。
_CHECKIN_HEADING_MARKERS = ("每日固定打卡", "每日打卡", "打卡清单", "打卡内容", "打卡项", "项打卡", "打卡如下", "daily check-in", "daily checkin", "checkin list")


@lru_cache(maxsize=512)
def _marker_pattern(marker: str) -> re.Pattern[str]:
    """英文标记词按整词匹配，中文按子串匹配。

    英文必须带词边界：``rate`` 作为子串会命中 ``carbohydrates``，把
    "Did you control dinner carbohydrates to at most 1 fist?" 判成评分题，
    再因为「判断+评分双意图」整条丢掉换成兜底题——这道题正是提示词里给的示例。
    边界只加在字母的那一头，``1-5``、``?`` 这类标记本来就没有词边界可言。
    """

    prefix = r"(?<![a-z])" if marker[:1].isalpha() else ""
    suffix = r"(?![a-z])" if marker[-1:].isalpha() else ""
    return re.compile(prefix + re.escape(marker) + suffix)


def _has_marker(text: str, markers: tuple[str, ...]) -> bool:
    """题面里是否出现任一标记词。``text`` 必须已 casefold。"""

    for marker in markers:
        if not marker.isascii():
            if marker in text:
                return True
            continue
        if _marker_pattern(marker).search(text):
            return True
    return False
# 兜底题池是代码写死的默认五项，类别按 key 直接标注，不靠倒猜。
_FALLBACK_CHECKIN_CATEGORIES = {
    "personalized_weight": "state",
    "personalized_water": "diet",
    "personalized_sleep": "sleep",
    "personalized_diet": "diet",
    "personalized_exercise": "activity",
}
_RELATIVE_TIME_WORDS = ("今天", "今日", "今早", "今晚", "昨天", "昨日", "昨晚", "当天", "本日", "today", "yesterday", "tonight", "this morning")
_FORBIDDEN_CHECKIN_EXAMPLE_MARKERS = (
    "示例回答",
    "请填写",
    "按实际情况填写",
    "按当天实际情况",
    "答：",
    "答:",
    "example answer",
    "please fill",
)
_CHECKIN_NUMBER_UNIT_PATTERN = re.compile(
    r"kg|公斤|斤|lbs?|pounds?|cm|厘米|inch|inches|mm|毫米|ml|毫升|l|升|步|steps?|次|times?|分钟|mins?|minutes?|小时|hours?|hrs?|克|g|拳|份|点",
    re.IGNORECASE,
)


def _normalize_daily_checkin_in_payloads(
    payloads: dict[str, dict],
    facts: dict[str, object],
    language: str = "zh",
) -> None:
    execution = payloads.get(DocumentType.DAY28_EXECUTION_STANDARD.value)
    if not isinstance(execution, dict):
        return
    normalized = normalize_daily_checkin_items(
        execution.get("daily_checkin"),
        facts=facts,
        language=language,
    )
    errors = _generated_daily_checkin_errors(normalized)
    if errors:
        raise RetryableIntakePackageError(
            f"执行建议书五项打卡与示例答案校验未通过：{'；'.join(errors)}"
        )
    execution["daily_checkin"] = normalized


def normalize_daily_checkin_items(
    value: object,
    *,
    facts: dict[str, object] | None = None,
    strict_style: bool = True,
    allow_fallback: bool = True,
    language: str = "zh",
) -> list[dict[str, object]]:
    """把 daily_checkin 收敛成正好 5 项、order 1-5 的结构化数组。

    接受三种输入：新的结构化数组、历史的字符串列表、历史的整段文本。校验不合格的项
    （栏目标题、复合问题、答题类型与题面不匹配）被丢弃，而不是直接截前5条；
    数量不足时用确定性兜底题补齐，保证画像、执行文档和 H5 问卷永远拿到同一份 5 项。

    生成链路用默认参数（strict_style + 兜底补齐）。读取历史文档时传
    strict_style=False, allow_fallback=False：历史题面本来就带「今日/昨晚」等相对时间词，
    不能因为新的文风规则把已发出去的打卡清单判成无效，也不能悄悄换成兜底题。
    """

    accepted: list[dict[str, object]] = []
    seen: set[str] = set()
    rejected: list[str] = []
    duplicated: list[str] = []
    for candidate in _daily_checkin_candidates(value):
        item = _validated_checkin_item(candidate, strict_style=strict_style, language=language)
        if item is None:
            rejected.append(_candidate_title_for_log(candidate))
            continue
        key = _checkin_dedupe_key(str(item["title"]))
        if key in seen:
            duplicated.append(str(item["title"]))
            continue
        seen.add(key)
        accepted.append(item)
        if len(accepted) >= DAILY_CHECKIN_SIZE:
            break

    substituted: list[str] = []
    if allow_fallback and len(accepted) < DAILY_CHECKIN_SIZE:
        for fallback in _fallback_checkin_items(facts or {}, language=language):
            key = _checkin_dedupe_key(str(fallback["title"]))
            if key in seen:
                continue
            seen.add(key)
            accepted.append(fallback)
            substituted.append(str(fallback["title"]))
            if len(accepted) >= DAILY_CHECKIN_SIZE:
                break

    # 新生成文件的答案必须来自同一次模型输出。题面被兜底替换时，原答案已经不再
    # 对应新题；将其清空让生成契约触发整包重试，绝不能把答非所问的组合落库。
    if substituted:
        for item in accepted:
            item.pop("example_answer", None)

    # 换题必须留痕：兜底题一顶上来，个性化打卡就被通用题替掉了，
    # 而后台只看得到最终这 5 项，看不出哪一项不是模型挑的。
    if rejected or duplicated or substituted:
        logger.warning(
            "daily_checkin normalized with substitutions: rejected=%s duplicated=%s fallback_used=%s",
            rejected,
            duplicated,
            substituted,
        )

    if allow_fallback and len(accepted) < DAILY_CHECKIN_SIZE:
        # 兜底题池就是5项，走到这里说明兜底配置被改坏了，宁可让上层报空也不发半套清单。
        return []
    for order, item in enumerate(accepted[:DAILY_CHECKIN_SIZE], start=1):
        item["order"] = order
    return accepted[:DAILY_CHECKIN_SIZE]


def _candidate_title_for_log(candidate: object) -> str:
    if isinstance(candidate, dict):
        return str(candidate.get("title") or candidate.get("label") or candidate.get("text") or "")[:80]
    return str(candidate or "")[:80]


def _daily_checkin_candidates(value: object) -> list[object]:
    """结构化数组直接用；历史字符串格式退回按行/序号拆分。"""

    if isinstance(value, list) and any(isinstance(item, dict) for item in value):
        ordered = [item for item in value if isinstance(item, dict)]
        ordered.sort(key=lambda item: _checkin_order_value(item.get("order")))
        return list(ordered)
    return list(_parse_daily_checkin_items(value))


def _checkin_order_value(value: object) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return float("inf")


def _validated_checkin_item(
    candidate: object,
    *,
    strict_style: bool = True,
    language: str = "zh",
) -> dict[str, object] | None:
    if isinstance(candidate, dict):
        title = _clean_checkin_item_text(
            candidate.get("title") or candidate.get("label") or candidate.get("text")
        )
        answer_type = str(candidate.get("answer_type") or "").strip()
        category = str(candidate.get("category") or "").strip()
        example_answer = _clean_checkin_example_answer(candidate.get("example_answer"))
    else:
        title = _clean_checkin_item_text(candidate)
        answer_type = ""
        category = ""
        example_answer = ""

    if not _is_valid_checkin_title(title, strict_style=strict_style):
        return None
    if answer_type not in CHECKIN_ANSWER_TYPES:
        answer_type = _infer_checkin_answer_type(title)
    if not _answer_type_matches_title(answer_type, title):
        return None
    item: dict[str, object] = {
        "order": 0,
        "category": category if category in CHECKIN_CATEGORIES else _infer_checkin_category(answer_type),
        "title": title,
        "answer_type": answer_type,
        "options": checkin_options_for(answer_type, language=language),
    }
    if example_answer:
        item["example_answer"] = example_answer
    return item


def _clean_checkin_example_answer(value: object) -> str:
    """只清理展示噪声，不根据题面替用户编答案。"""

    text = str(value or "").strip()
    text = re.sub(r"^\s*[-•*]\s*", "", text)
    return " ".join(text.split())


def _generated_daily_checkin_errors(items: list[dict[str, object]]) -> list[str]:
    """新生成文件的打卡契约；历史读取不经过这道闸门。"""

    errors: list[str] = []
    if len(items) != DAILY_CHECKIN_SIZE:
        return ["必须正好生成5项"]
    if str(items[0].get("category") or "") != "product":
        errors.append("第1项必须是个案产品项")
    if sum(item.get("category") == "activity" for item in items) != 1:
        errors.append("必须恰好包含1项运动项")
    for index, item in enumerate(items, start=1):
        answer = str(item.get("example_answer") or "").strip()
        if not answer:
            errors.append(f"第{index}项缺少已填写答案")
            continue
        if any(marker in answer for marker in _FORBIDDEN_CHECKIN_EXAMPLE_MARKERS):
            errors.append(f"第{index}项答案仍是填写提示")
            continue
        answer_type = str(item.get("answer_type") or "")
        category = str(item.get("category") or "")
        title = str(item.get("title") or "")
        answer_lower = answer.lower()
        if category == "product":
            has_chinese_slots = all(name in answer for name in ("早C", "午B", "晚A"))
            has_english_slots = (
                ("morning" in answer_lower or "early" in answer_lower or "breakfast" in answer_lower or " c" in answer_lower or answer_lower.startswith("c"))
                and ("noon" in answer_lower or "lunch" in answer_lower or "midday" in answer_lower or " b" in answer_lower)
                and ("evening" in answer_lower or "night" in answer_lower or "dinner" in answer_lower or " a" in answer_lower)
            ) or ("morning c" in answer_lower or "noon b" in answer_lower or "evening a" in answer_lower)
            if not (has_chinese_slots or has_english_slots):
                errors.append(f"第{index}项答案没有分别写清早C、午B、晚A")
            has_chinese_status = any(marker in answer for marker in ("未完成", "暂停", "已完成", "按方案"))
            has_english_status = any(
                marker in answer_lower
                for marker in (
                    "incomplete",
                    "paused",
                    "skipped",
                    "missed",
                    "not completed",
                    "completed",
                    "done",
                    "as planned",
                    "finished",
                )
            )
            if not (has_chinese_status or has_english_status):
                errors.append(f"第{index}项答案没有展示未完成或按方案暂停")
        elif answer_type == "yes_no" and not (
            answer.startswith(("是", "否"))
            or answer_lower.startswith(("yes", "no", "y", "n", "true", "false"))
        ):
            errors.append(f"第{index}项判断题答案没有明确是或否")
        elif answer_type == "score_1_5" and not (
            re.match(r"^[1-5]\s*分\s*[,，。；;].+", answer)
            or re.match(r"^[1-5](\s*分|\s*/\s*5|\s*pts?|)\s*[,，。；;: -].+", answer, re.IGNORECASE)
        ):
            errors.append(f"第{index}项评分答案不是已填写的1-5分答案")
        elif answer_type == "number":
            units = _CHECKIN_NUMBER_UNIT_PATTERN.findall(title)
            if not units:
                if not re.search(r"\d", answer):
                    errors.append(f"第{index}项数字答案缺少数字")
            elif not re.search(r"\d", answer) or not any(
                unit.casefold() in answer.casefold() for unit in units
            ):
                errors.append(f"第{index}项数字答案没有带题目要求的单位")
    return errors


def _is_valid_checkin_title(title: str, *, strict_style: bool = True) -> bool:
    if not title or len(title) > MAX_CHECKIN_TITLE_CHARS:
        return False
    title_lower = title.casefold()
    if _has_marker(title_lower, _CHECKIN_HEADING_MARKERS):
        return False
    if strict_style and _has_marker(title_lower, _RELATIVE_TIME_WORDS):
        return False
    # 分号说明这一项塞了两个独立句子，拆不开就丢弃。
    if "；" in title or ";" in title:
        return False
    if _has_marker(title_lower, _COMPOSITE_CONJUNCTIONS):
        return False
    intents = sum(
        [
            _has_marker(title_lower, _JUDGEMENT_MARKERS),
            _has_marker(title_lower, _SCORE_MARKERS),
            _has_marker(title_lower, _SECOND_INTENT_MARKERS),
        ]
    )
    return intents <= 1


def _answer_type_matches_title(answer_type: str, title: str) -> bool:
    """题面形状必须和答题类型对得上，否则转成问卷字段就会答非所问。

    刻意**不**用「以问号结尾就算是否题」：英文题面所有问法都以问号结尾，
    那样 "How many minutes did you walk?" 会被判成是/否题，而正确标了 number
    的同一道题反而被判不匹配、整条丢掉换成兜底题。
    """

    title_lower = title.casefold()
    has_judgement = _has_marker(title_lower, _JUDGEMENT_MARKERS)
    has_score = _has_marker(title_lower, _SCORE_MARKERS)
    has_quantity = _has_marker(title_lower, _QUANTITY_MARKERS)
    if answer_type == "yes_no":
        return has_judgement and not has_quantity
    if answer_type == "score_1_5":
        return has_score
    if answer_type == "number":
        # 填数字的题只要问的是数量、或不和别的答题类型冲突就放行。曾经要求题面必须带
        # 单位括号，结果把「夜间醒来次数」这类完全合法的题判废、换成通用兜底题，白丢个性化。
        # 单位该不该写、写什么由提示词管（体重题必须写清 kg），代码不做事后审核也不猜。
        return has_quantity or not (has_judgement or has_score)
    return _looks_like_question(title)


def _looks_like_question(title: str) -> bool:
    if title.endswith(("？", "?")):
        return True
    if "（" in title or "(" in title:
        return True
    title_lower = title.casefold()
    return _has_marker(
        title_lower,
        (
            *_JUDGEMENT_MARKERS,
            *_SCORE_MARKERS,
            *_REQUEST_MARKERS,
            # 自由填写题的引导词。只收明确要求作答的动词，不收 "time"/"intake" 这类
            # 名词——它们会命中 "sometimes" 之类的普通词，把栏目标题也放行。
            "describe",
            "list",
            "notes",
        ),
    )


def infer_checkin_answer_type(title: str) -> str:
    """按题面形状推断答题类型；只用于没有结构化定义的人工改写清单。"""

    return _infer_checkin_answer_type(title)


def _infer_checkin_answer_type(title: str) -> str:
    title_lower = title.casefold()
    if _has_marker(title_lower, _SCORE_MARKERS):
        return "score_1_5"
    # 数量优先于判断：英文的数字题几乎都写成 "How many ... did you ...?"，
    # 先看判断标记会把它们全推成是/否题。
    if _has_marker(title_lower, _QUANTITY_MARKERS):
        return "number"
    if _has_marker(title_lower, _JUDGEMENT_MARKERS):
        return "yes_no"
    if "（" in title or "(" in title:
        return "number"
    return "text"


def _infer_checkin_category(answer_type: str) -> str:
    """类别只由模型的 category 字段承载，不从题面或答题类型倒猜（猜出来一定是错的）。

    历史字符串格式没有这个字段，留空表示未知；消费方目前只用 category 做展示分组。
    """

    return ""


def checkin_options_for(answer_type: str, language: str = "zh") -> list[str]:
    if answer_type == "yes_no":
        return list(CHECKIN_YES_NO_OPTIONS_EN if language == "en" else CHECKIN_YES_NO_OPTIONS)
    if answer_type == "score_1_5":
        return list(CHECKIN_SCORE_OPTIONS)
    return []


def checkin_field_type_for(answer_type: str) -> str:
    return "RADIO" if answer_type in {"yes_no", "score_1_5"} else "SIMPLE"


def _checkin_dedupe_key(title: str) -> str:
    return re.sub(r"[\s，,。！？!?；;：:（）()]+", "", title).casefold()


def _fallback_checkin_items(
    facts: dict[str, object],
    language: str = "zh",
) -> list[dict[str, object]]:
    """确定性兜底：沿用默认五项个性化打卡（含按体重换算的饮水量），并去掉相对时间词。

    ``language`` 必须跟着服务单走：模型题面一旦被判废就是这份题顶上去，
    出错语言等于让英文客户每天面对一张中文打卡表。
    """

    items: list[dict[str, object]] = []
    for field in _fallback_personalized_checkin_fields(facts, language=language):
        title = _clean_checkin_item_text(field.get("title") or field.get("label"))
        for word in _RELATIVE_TIME_WORDS:
            title = title.replace(word, "")
        title = title.strip(" ，,。")
        if not title:
            continue
        answer_type = "yes_no" if _has_marker(title.casefold(), _JUDGEMENT_MARKERS) else "number"
        items.append(
            {
                "order": 0,
                # 兜底题是代码写死的，类别直接按 key 标注：这不是从题面倒猜模型的分类，
                # 而是我们自己定义的题本来就属于哪一类。
                "category": _FALLBACK_CHECKIN_CATEGORIES.get(str(field.get("key") or ""), ""),
                "title": title,
                "answer_type": answer_type,
                "options": checkin_options_for(answer_type, language=language),
            }
        )
    return items


def _daily_checkin_value_from_execution(execution: dict[str, object]) -> object:
    """Read daily check-ins from either the LLM payload or rendered document JSON."""

    daily_checkin = execution.get("daily_checkin")
    if daily_checkin:
        return daily_checkin

    fields = execution.get("fields")
    if isinstance(fields, dict) and fields.get("daily_checkin"):
        return fields["daily_checkin"]

    blocks = execution.get("blocks")
    if not isinstance(blocks, list):
        return None

    fallback_value: object = None
    for block in blocks:
        if not isinstance(block, dict):
            continue
        key = str(block.get("key") or "").strip()
        title = str(block.get("title") or "").strip()
        # 结构化项优先：渲染后的 content 只剩「1. 题面」文本，answer_type/options 会丢。
        value = (
            block.get("checkin_items")
            or block.get("content")
            or block.get("items")
            or block.get("value")
            or block.get("text")
        )
        if not value:
            continue
        if key == "daily_checkin" or "每日固定打卡" in title:
            return value
        if fallback_value is None and "打卡" in title:
            fallback_value = value
    return fallback_value


def _parse_daily_checkin_items(value: object) -> list[str]:
    if isinstance(value, list):
        candidates: list[object] = value
    else:
        text = _text_from_payload(value)
        candidates = re.split(r"[\n；;]+", text) if text else []
        # LLM 有时把多项写在同一行、用 ①②③ 圈号或 1.2.3. 编号分隔，换行拆不开时按序号再拆
        if len([c for c in candidates if str(c).strip()]) <= 1 and text:
            circled = [p for p in re.split(r"(?=[①②③④⑤⑥⑦⑧⑨⑩⑪⑫])", text) if p.strip()]
            if len(circled) > 1:
                candidates = circled
            else:
                numbered = [p for p in re.split(r"(?=\d+\s*[\.、\)）])", text) if p.strip()]
                if len(numbered) > 1:
                    candidates = numbered

    items: list[str] = []
    for candidate in candidates:
        if isinstance(candidate, dict):
            raw = candidate.get("title") or candidate.get("label") or candidate.get("value") or candidate.get("content")
        else:
            raw = candidate
        text = _clean_checkin_item_text(raw)
        if text:
            items.append(text)
    return items


def _clean_checkin_item_text(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"^\s*[-•*]\s*", "", text)
    text = re.sub(r"^\s*[①②③④⑤⑥⑦⑧⑨⑩⑪⑫]\s*", "", text)
    text = re.sub(r"^\s*\d+\s*[\.、\)\）]\s*", "", text)
    text = re.sub(r"（\s*(正向动作|限制动作)\s*）", "", text)
    text = re.sub(r"\(\s*(正向动作|限制动作)\s*\)", "", text)
    return text.strip(" ：:；;，,。")


def _text_from_payload(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "；".join(str(item) for item in value if str(item).strip())
    if isinstance(value, dict):
        return "；".join(str(item) for item in value.values() if str(item).strip())
    return str(value).strip()


def _normalize_combined_document_payload(
    payload: dict,
    user_name: str,
    *,
    shared_analysis: dict[str, object] | None = None,
) -> dict[str, dict]:
    energy_key = DocumentType.ENERGY_RESTART_PRESCRIPTION.value
    execution_key = DocumentType.DAY28_EXECUTION_STANDARD.value
    energy_payload = payload.get(energy_key)
    execution_payload = payload.get(execution_key)
    if not isinstance(energy_payload, dict) and isinstance(payload.get("energy"), dict):
        energy_payload = payload.get("energy")
    if not isinstance(execution_payload, dict) and isinstance(payload.get("execution"), dict):
        execution_payload = payload.get("execution")
    if not isinstance(energy_payload, dict) or not isinstance(execution_payload, dict):
        return {}
    energy_payload.setdefault("recipient", user_name)
    execution_payload.setdefault("recipient", user_name)
    shared = shared_analysis or {}
    shared_path = energy_payload.get("success_path") if isinstance(energy_payload.get("success_path"), dict) else {}
    overview = energy_payload.get("overview") if isinstance(energy_payload.get("overview"), dict) else {}
    core_conclusions = (
        energy_payload.get("core_conclusions")
        if isinstance(energy_payload.get("core_conclusions"), dict)
        else {}
    )
    goal_response = (
        energy_payload.get("goal_response")
        if isinstance(energy_payload.get("goal_response"), dict)
        else {}
    )

    # 带位下发到两份用户可见文件时统一走用户文案；shared_analysis 保留内部哨兵值给后台。
    visible_band = _user_visible_band(shared.get("weight_band"))

    _sync_if_missing(core_conclusions, "management_band", visible_band)
    _sync_if_missing(core_conclusions, "band_explanation", _first_clean_item(shared.get("band_basis")))
    _sync_if_missing(core_conclusions, "body_trend", shared.get("body_trend"))
    _sync_if_missing(core_conclusions, "trend_explanation", shared.get("body_trend_summary"))
    shared_direct, shared_supports = _trend_evidence_parts(shared)
    _sync_if_missing(core_conclusions, "trend_direct_evidence", shared_direct)
    _sync_if_missing(core_conclusions, "trend_support_signals", shared_supports)
    _sync_if_missing(core_conclusions, "day28_task", shared.get("day28_task"))
    if core_conclusions:
        energy_payload["core_conclusions"] = core_conclusions

    _sync_if_missing(goal_response, "base_goal", shared.get("base_goal"))
    _sync_if_missing(goal_response, "challenge_goal", shared.get("challenge_goal"))
    if goal_response:
        energy_payload["goal_response"] = goal_response

    _sync_if_missing(shared_path, "weight_band", visible_band)
    _sync_if_missing(shared_path, "total_goal", shared.get("long_term_goal"))
    _sync_if_missing(shared_path, "day28_goal", shared.get("day28_goal"))
    _sync_if_missing(execution_payload, "weight_band", shared_path.get("weight_band"))
    _sync_if_missing(execution_payload, "path_goal", shared_path.get("total_goal"))
    _sync_if_missing(execution_payload, "day28_goal", shared_path.get("day28_goal"))
    _sync_if_missing(execution_payload, "body_trend", shared.get("body_trend"))
    _sync_if_missing(execution_payload, "base_goal", shared.get("base_goal"))
    _sync_if_missing(execution_payload, "challenge_goal", shared.get("challenge_goal"))
    _sync_if_missing(execution_payload, "long_term_goal", shared.get("long_term_goal"))
    current_mainline = _text_from_payload(shared.get("current_mainline"))
    if not current_mainline:
        current_mainline = _text_from_payload(
            overview.get("current_focus")
            or execution_payload.get("current_mainline")
            or energy_payload.get("priority_direction")
        )
        if current_mainline:
            # 兼容旧模型输出：先收回共享分析，再由同一来源下发到两份文件。
            shared["current_mainline"] = current_mainline
    _sync_if_missing(overview, "current_focus", current_mainline)
    _sync_if_missing(execution_payload, "current_mainline", current_mainline)
    _sync_if_missing(execution_payload, "principle", shared.get("execution_principle"))
    _sync_if_missing(
        execution_payload,
        "product_schedule",
        energy_payload.get("product_schedule"),
    )
    _sync_if_missing(shared_path, "weight_band", execution_payload.get("weight_band"))
    _sync_if_missing(shared_path, "total_goal", execution_payload.get("path_goal"))
    _sync_if_missing(shared_path, "day28_goal", execution_payload.get("day28_goal"))

    # 两份文件里必须逐字一致的事实，以 shared_analysis 为唯一来源强制覆写，
    # 而不是靠模型自觉在两处各写一遍。模型独立生成同一事实时会同义改写
    # （「腰腹缩围和精力转折」/「腰腹围度收紧和精力转向」），既冗余又会带数字漂移。
    # 这和 BMI 由 intake_facts 强制覆写是同一套做法。
    # 执行建议书的目标摘要由渲染层压到 48 字内，所以覆写更长的权威文案不会顶到第二页。
    for target, key, source in (
        (core_conclusions, "management_band", visible_band),
        (shared_path, "weight_band", visible_band),
        (execution_payload, "weight_band", visible_band),
        (goal_response, "base_goal", shared.get("base_goal")),
        (goal_response, "challenge_goal", shared.get("challenge_goal")),
        (execution_payload, "base_goal", shared.get("base_goal")),
        (execution_payload, "challenge_goal", shared.get("challenge_goal")),
        (execution_payload, "execution_path", shared.get("execution_path")),
        (
            execution_payload,
            "commitment_rule",
            "标准档累计完整打卡率60%；高承诺档85%，均在群内确认后生效；当天五项都有回答才计为1个完整打卡日。",
        ),
        (shared_path, "day28_goal", shared.get("day28_goal")),
        (execution_payload, "day28_goal", shared.get("day28_goal")),
        (shared_path, "total_goal", shared.get("long_term_goal")),
        (shared_path, "week5_8", shared.get("week5_8_target")),
        (shared_path, "week9_12", shared.get("week9_12_target")),
        (execution_payload, "path_goal", shared.get("long_term_goal")),
        (execution_payload, "long_term_goal", shared.get("long_term_goal")),
        (execution_payload, "body_trend", shared.get("body_trend")),
        (core_conclusions, "day28_task", shared.get("day28_task")),
        (overview, "current_focus", shared.get("current_mainline")),
        (execution_payload, "current_mainline", shared.get("current_mainline")),
    ):
        _force_sync(target, key, source)
    _apply_a_band_user_visible_contract(shared, core_conclusions, goal_response)
    # 模型的整段 content 可能保留旧数字。删除后由渲染层使用已被代码覆写的结构化
    # 基础/挑战字段，既保留个案判断，也保证两份文档逐字一致。
    goal_response.pop("content", None)
    # core_conclusions 可能在上面因为全空而没挂回 energy_payload，强制覆写后要重新挂。
    if core_conclusions:
        energy_payload["core_conclusions"] = core_conclusions
    if goal_response:
        energy_payload["goal_response"] = goal_response
    if overview:
        energy_payload["overview"] = overview

    if shared_path:
        energy_payload["success_path"] = shared_path
    if _flatten_payload_text_length(energy_payload) < 80 or _flatten_payload_text_length(execution_payload) < 50:
        return {}
    return {energy_key: energy_payload, execution_key: execution_payload}


# 用户可见字段里的减重数字。业务判断仍以模型的 goal_type 为准，这个只做最后一道
# 格式兜底：A带的整个话术契约存在的理由就是提示词封不住数字，所以放行前再查一次。
# 必须同时认英制：英文服务单的目标文案本来就以 lbs 给出，只认中文单位等于对
# 英文 A 带客户完全不设防（"Lose 5 lbs" 会被原样放过去）。
_WEIGHT_NUMBER_PATTERN = re.compile(
    r"\d+(?:\.\d+)?\s*(?:斤|公斤|千克|kg|lbs?|pounds?)",
    re.IGNORECASE,
)
# 概览表里"腰围/体脂率"这类量测值：纯数字或数字区间才算填了数值，才配拼上单位。
_MEASUREMENT_VALUE_PATTERN = re.compile(
    r"^\d+(?:\.\d+)?(?:\s*[-–~〜至]\s*\d+(?:\.\d+)?)?$"
)
_ANY_DIGIT_PATTERN = re.compile(r"\d")


def _has_specific_weight_goal(goal_response: dict[str, object]) -> bool:
    if _text_from_payload(goal_response.get("goal_type")).strip().lower() == "specific":
        return True
    return bool(
        _WEIGHT_NUMBER_PATTERN.search(_text_from_payload(goal_response.get("user_goal")))
    )


def _apply_a_band_user_visible_contract(
    shared: dict[str, object],
    core_conclusions: dict[str, object],
    goal_response: dict[str, object],
) -> None:
    """A带正文只给定性结论，不让模型复述用户斤数或内部安全空间数字。

    话术按用户**实际填了什么**分两种，不能一套通吃：

    - specific（用户写了减重斤数或目标体重）：先复述诉求再给判断。复述必须去掉数字——
      这个数字是要被否掉的，白纸黑字留在诊断报告里只会变成用户记住的锚点，
      还容易顺着"全文数字一致"的要求扩散到成功路径和预期变化里。
    - general（用户只勾了睡眠、精力、体态这类期待）：直接给判断和方向，不写"您希望…"。
      这类用户压根没提减重，替他复述一个减重诉求，等于先编一个目标再当面否掉它，
      而他真正提的诉求反而没被回应。此时 user_goal 保留模型写的那份——
      模型看得到问卷，知道他要的是睡眠还是精力。

    goal_type 缺失或非法时按 general 处理：最坏结果只是少复述一句，
    比编造用户没说过的诉求轻。但 general 分支会保留模型写的 user_goal，
    所以先用 _has_specific_weight_goal 兜一层——模型把斤数写进 user_goal 却漏填
    goal_type 时，仍要走 specific 的脱敏改写，不能让数字从这里漏进报告。
    """

    if _text_from_payload(shared.get("weight_band")) != "A带｜修复塑形":
        return
    core_conclusions["band_explanation"] = (
        "当前更适合修复塑形，不设置体重下降指标，重点观察围度、体态和身体状态变化。"
    )
    goal_response["judgement_type"] = "needs_adjustment"
    if _has_specific_weight_goal(goal_response):
        goal_response.update(
            {
                "content": (
                    "您希望尽快看到体重和体态变化。结合当前状态，这28天不建议设置减重数字目标，"
                    "更适合先保持体重基本稳定，把重点放在体态、睡眠、精力、恢复和执行一致性上。"
                    "当前不设置减重挑战目标；基础动作稳定完成、睡眠和精力没有变差后，"
                    "再与班主任评估下一阶段。"
                ),
                "user_goal": "希望尽快看到体重和体态变化",
                "judgement": "当前阶段不建议设置减重数字目标，更适合先保持体重基本稳定并修复体态与状态。",
                "conditions": "基础动作稳定完成、睡眠和精力没有变差后，再与班主任评估下一阶段。",
            }
        )
        return
    goal_response.update(
        {
            "goal_type": "general",
            "content": (
                "结合您在问卷中填写的情况，这28天更适合先把身体状态和执行节律稳住，"
                "不设置体重下降指标，重点观察围度体态、力量、睡眠、精力和执行一致性的变化。"
                "当前不设置减重挑战目标；基础动作稳定完成、睡眠和精力持续改善后，"
                "再与班主任评估下一阶段。"
            ),
            "judgement": "当前阶段更适合先修复身体状态和执行节律，不设置减重数字目标。",
            "conditions": "基础动作稳定完成、睡眠和精力持续改善后，再与班主任评估下一阶段。",
        }
    )


def _first_clean_item(value: object) -> str:
    if isinstance(value, list):
        for item in value:
            text = str(item or "").strip()
            if text:
                return text
    return str(value or "").strip() if isinstance(value, str) else ""


def _apply_intake_facts_to_document_payloads(
    payloads: dict[str, dict],
    facts: dict[str, object],
    language: str = "zh",
) -> None:
    for document_type, payload in list(payloads.items()):
        if isinstance(payload, dict):
            try:
                enum_value = DocumentType(document_type)
            except ValueError:
                continue
            _apply_intake_facts_to_single_payload(payload, enum_value, facts, language=language)


def _apply_intake_facts_to_single_payload(
    payload: dict[str, object],
    document_type: DocumentType,
    facts: dict[str, object],
    language: str = "zh",
) -> None:
    # facts 里存的是内部带位（可能是哨兵值），写进用户可见文档前翻译一次。
    weight_band = _user_visible_band(facts.get("weight_loss_band"))
    bmi = str(facts.get("bmi") or "").strip()
    if bmi and bmi != "暂无数据":
        _replace_bmi_mentions(payload, bmi)
    if not weight_band:
        return
    if document_type == DocumentType.ENERGY_RESTART_PRESCRIPTION:
        success_path = payload.get("success_path")
        if not isinstance(success_path, dict):
            success_path = {}
            payload["success_path"] = success_path
        success_path["weight_band"] = weight_band
        overview = payload.get("overview")
        if isinstance(overview, dict):
            overview["bmi"] = bmi or str(overview.get("bmi") or ("N/A" if language == "en" else "暂无数据"))
            # 用户可见展示：中文用「斤」，英文用「lbs」（内部计算与结构化字段仍用 kg）。
            overview["height_weight"] = _join_fact_values(
                _with_unit(facts.get("height_cm"), "cm"),
                _weight_kg_to_display(facts.get("weight_kg"), language=language),
            ) or overview.get("height_weight")
            overview["waist_bodyfat"] = _format_waist_bodyfat(
                facts.get("waist"),
                facts.get("bodyfat"),
                language=language,
            )
    elif document_type == DocumentType.DAY28_EXECUTION_STANDARD:
        payload["weight_band"] = weight_band


def _replace_bmi_mentions(value: object, bmi: str) -> None:
    """Keep generated document text aligned with deterministic intake facts."""

    if isinstance(value, dict):
        for key, item in list(value.items()):
            if isinstance(item, str):
                value[key] = _replace_bmi_text(item, bmi)
            else:
                _replace_bmi_mentions(item, bmi)
    elif isinstance(value, list):
        for index, item in enumerate(list(value)):
            if isinstance(item, str):
                value[index] = _replace_bmi_text(item, bmi)
            else:
                _replace_bmi_mentions(item, bmi)


def _replace_bmi_text(text: str, bmi: str) -> str:
    return re.sub(r"BMI\s*[:：]?\s*\d+(?:\.\d+)?", f"BMI {bmi}", text)


def _join_fact_values(*values: object) -> str:
    cleaned = [str(value) for value in values if value and str(value) not in {"未提供", "暂无数据"}]
    return " / ".join(cleaned)


def _blank_if_missing(value: object) -> str:
    text = str(value or "")
    return "" if text in {"未提供", "暂无数据"} else text


def _format_waist_bodyfat(waist: object, bodyfat: object, language: str = "zh") -> str:
    """Render the two measurements as independently labeled facts.

    They share one overview cell for layout only. Dropping an absent waist used
    to leave a bare ``30`` beside the combined label, which made a supplied
    body-fat value look like a waist value. A missing measurement is part of
    the visible fact and must stay visible.

    单位只跟在真正的数值后面。用户经常把整句话填进数值栏（"不知道"、"后续告知"、
    "暂时没测量在出差"），固定短语清单挡不住这种自由文本，早先无条件拼单位会在报告
    首屏印出"体脂率暂时没测量在出差%"。
    """

    missing_values = {"", "未提供", "暂无数据", "暂未测量", "待补充", "待测量"}
    is_en = language == "en"
    missing_text = "not provided" if is_en else "未提供"
    sep = ": " if is_en else ""

    def display(value: object, *, label: str, unit: str) -> str:
        text = str(value or "").strip()
        if text in missing_values:
            return f"{label}{sep}{missing_text}"
        measurement = text[: -len(unit)].strip() if text.endswith(unit) else text
        if _MEASUREMENT_VALUE_PATTERN.match(measurement):
            return f"{label}{sep}{measurement}{unit}"
        if _ANY_DIGIT_PATTERN.search(text):
            # 有数值但夹着说明（"30 左右，去年测的"）：原样保留信息，只是不再拼单位。
            return f"{label}{sep}{text}"
        return f"{label}{sep}{missing_text}"

    return " / ".join(
        (
            display(waist, label="Waist" if is_en else "腰围", unit="cm"),
            display(bodyfat, label="Body fat" if is_en else "体脂率", unit="%"),
        )
    )


def _with_unit(value: object, unit: str) -> str:
    text = _blank_if_missing(value)
    if not text:
        return ""
    return text if text.endswith(unit) else f"{text}{unit}"


def _sync_if_missing(target: dict, key: str, value: object) -> None:
    if not target.get(key) and value:
        target[key] = value


def _force_sync(target: dict, key: str, value: object) -> None:
    """单一来源：source 非空就覆写，不管目标原来写了什么。

    只用于两份交付文件必须逐字一致的事实。source 为空时保留原值，
    宁可用模型在文档里写的那一份，也不要把字段清空。
    """

    if not isinstance(target, dict) or not value:
        return
    if isinstance(value, str) and not value.strip():
        return
    target[key] = value


def _flatten_payload_text_length(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, dict):
        return sum(_flatten_payload_text_length(item) for item in value.values())
    if isinstance(value, list):
        return sum(_flatten_payload_text_length(item) for item in value)
    return len(str(value).strip())


def _readable_answers(fields: list[dict], answers: dict) -> str:
    title_by_key = {field.get("key") or field.get("id"): field.get("title") or field.get("label") for field in fields}
    lines: list[str] = []
    for key, value in answers.items():
        title = title_by_key.get(key, key)
        if isinstance(value, list):
            display = "、".join(str(item) for item in value)
        else:
            display = str(value).replace("||", "、")
        lines.append(f"- {title}: {display or '未提供'}")
    return "\n".join(lines)


def _document_model_intake_facts(intake_facts: dict[str, object]) -> dict[str, object]:
    """投影出交付模型可见的事实，运营复核与医疗原文只留在风控链。

    这不是重新判断用户信息：带位和目标仍使用安全引擎已经算出的最终值；只是
    不让负责写交付文档的模型看到“为什么运营要重点关注”的内部细节。
    """

    return {
        key: intake_facts[key]
        for key in _DOCUMENT_MODEL_FACT_KEYS
        if key in intake_facts
    }


def _document_model_readable_answers(
    fields: list[dict[str, Any]],
    answers: dict[str, Any],
) -> str:
    """移除运营专用健康自由文本后，才将问卷交给交付文件模型。

    活跃问卷早于 ``operator_only`` 元数据，因此兼容旧模板时沿用入营事实提取的
    「健康情况 / 病史」题干语义识别。新模板可直接标 ``operator_only=true``。
    """

    visible_answers = dict(answers)
    for field in fields:
        key = str(field.get("key") or field.get("id") or "")
        title = str(field.get("title") or field.get("label") or "")
        is_legacy_health_field = "健康情况" in title or "病史" in title
        is_marketing_field = (
            "最早是在哪里认识加速燃" in title
            or "让您最终愿意购买并进群" in title
        )
        is_upload_field = str(field.get("type") or "").upper() == "FILE"
        if (
            field.get("operator_only") is True
            or is_legacy_health_field
            or is_marketing_field
            or is_upload_field
        ):
            visible_answers.pop(key, None)
    return _readable_answers(fields, visible_answers)


def _safety_model_readable_answers(
    fields: list[dict[str, Any]],
    answers: dict[str, Any],
) -> str:
    """安全抽取保留健康原文，但排除只供运营统计的新增画像题。"""

    visible_answers = {
        key: value
        for key, value in answers.items()
        if not any(
            str(field.get("key") or field.get("id") or "") == key
            and field.get("safety_hidden") is True
            for field in fields
        )
    }
    return _readable_answers(fields, visible_answers)
