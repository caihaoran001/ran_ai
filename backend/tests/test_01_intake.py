from __future__ import annotations

import json
import logging
import re
import secrets
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models.outcome import OutcomeEvent, OutcomeStageState
from app.models.questionnaire import QuestionnaireSubmission
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user_document import DocumentStatus, DocumentType, UserDocument
from app.models.user_document_original import UserDocumentOriginal
from app.models.user_profile import UserProfile
from app.services.document_service import (
    _apply_intake_facts_to_document_payloads,
    _intake_generation_system_prompt,
)
from app.services.document_delivery import _document_filename
from app.services.document_prompts import CONCISENESS_RULE
from app.services.document_templates import (
    _adjust_stop_conditions_text,
    _daily_checkin_text,
    build_document_content_json,
    combined_prompt_schema,
)
from app.services.intake_generation import (
    CHECKIN_CATEGORIES,
    UNRESOLVED_BAND_USER_TEXT,
    _apply_a_band_user_visible_contract,
    _apply_canonical_target_contract,
    _apply_deterministic_band,
    _apply_persistent_intake_risk_flags,
    _document_model_intake_facts,
    _document_model_readable_answers,
    _document_generation_block_reasons,
    _document_consistency_errors,
    _delivery_instruction_consistency_errors,
    _extract_safety_factors,
    _force_high_risk_manual_review,
    _apply_shared_activity_frequency_to_documents,
    _apply_shared_activity_frequency_to_profile,
    _normalize_shared_activity_frequency,
    _safety_model_readable_answers,
    _generated_daily_checkin_errors,
    _normalize_safety_factors,
    _resolve_management_band,
    _scrub_internal_text,
    _normalize_combined_document_payload,
    checkin_options_for,
    normalize_daily_checkin_items,
)
from app.services.intake_facts import (
    UNRESOLVED_BAND,
    _management_band_from_safe_room,
    build_intake_fact_summary,
)
from app.services.questionnaire_template import default_questionnaire_template


INTAKE_SYSTEM_PROMPT_PREFIX = "你是加速燃「高燃人生28天训练营」的首席能量教练"
SAFETY_SYSTEM_PROMPT_PREFIX = "你是健康服务的安全审阅员。"


def _make_intake_mock_response(
    *,
    weight_band: str = "B带｜轻盈启动",
    bmi: str = "21.2",
    manual_review: bool = False,
) -> str:
    """构造入营 package。

    manual_review=True 模拟「用药需要人工复核」样本，并故意复刻真实模型的两种越界行为：
    把「需人工复核」写成管理带、把内部标签漏进用户可见正文。用来验证确定性兜底。
    """

    safety_fields = (
        {
            "safety_status": "manual_review",
            "manual_review_required": True,
            "manual_review_reasons": ["问卷填写当前长期用药", "关键指标需医生确认后才能加码"],
            "band_confidence": "low",
            "provisional_band": True,
            # 模型仍把内部标签当带位写 → 必须被回退成业务带位
            "weight_band": "需人工复核",
        }
        if manual_review
        else {
            "safety_status": "standard",
            "manual_review_required": False,
            "weight_band": weight_band,
        }
    )
    return json.dumps({
        "intake_analysis_package": {
            "shared_analysis": {
                **safety_fields,
                "band_basis": [
                    "当前基线体重65kg与安全下限61.25kg之间的可管理空间为3.75kg",
                    "安全减重空间落在2.5kg至不足5kg边界",
                ],
                "body_trend": "基本稳定",
                "body_trend_summary": "近3个月体重基本稳定，当前更需要改善节律和精力，而不是追求快速掉秤",
                "body_trend_evidence": [
                    "最近3个月体重基本稳定",
                    "自述晚餐后基本能收口",
                    "当前运动仅为偶尔散步快走",
                ],
                "body_trend_confidence": "high",
                "core_problem": "晚睡压力导致的情绪性进食和执行波动",
                "base_goal": "稳定睡眠节律和晚间收口，体重保持在健康范围",
                "challenge_goal": "不设置减重挑战目标",
                "long_term_goal": "保持健康体重，同时改善腰围体态、力量、睡眠和精力",
                "day28_goal": "保持健康体重，重点优化睡眠节律和晚餐收口",
                "day28_task": "先把入睡时间和晚间收口稳定下来",
                "long_term_defense": "用规律睡眠、饮食结构和力量训练守住健康体重与精力",
                "execution_principle": "先稳节律再抓减脂，守住晚间收口和睡眠时间",
                "activity_plan": {"status": "planned", "sessions_per_week": 3},
                "evidence_notes": [
                    "入睡时间23:30-00:30 → 晚睡会拉高夜间皮质醇和食欲",
                    "BMI 21.2 → 当前已在健康体重范围",
                    "情绪不稳、压力大容易乱 → 压力是当前执行最大干扰变量",
                ],
            },
            "profile": {
                "summary": (
                    "30岁男性，BMI 21.2（B带），白班作息，主要挑战是晚睡压力导致的"
                    "情绪性进食和执行波动。当前体重健康，核心方向不是减重而是重建稳定节律"
                    "和减少晚间失控。"
                ),
                "tags": ["B带", "晚睡习惯", "下午犯困"],
                "overview": {
                    "gender_age": "男 / 30",
                    "height_weight": "175cm / 65kg",
                    "bmi": bmi,
                    "waist": "未提供",
                    "weight_loss_band": weight_band,
                },
                "state": "当前处于功能勉强够用的状态，白天靠咖啡因提神，下午容易犯困",
                "main_problem": "晚睡压力导致的情绪性进食和执行波动",
                "day28_goal": "保持健康体重，重点优化睡眠节律和晚餐收口",
                "risks": "暂无明确风险",
                "notes": "建议围绕晚间收口、睡眠时间和情绪管理三个维度进行轻量陪跑",
            },
            "documents": {
                "energy_restart_prescription": {
                    "recipient": "蔡浩然",
                    "overview": {
                        "name": "蔡浩然",
                        "gender_age": "男 / 30岁",
                        "height_weight": "175cm / 65kg",
                        "bmi": f"BMI {bmi}（{weight_band}）",
                        "waist_bodyfat": "暂未测量 / 暂未测量",
                        "recent_change": "基本稳定",
                        "schedule": "固定白班，23:30-00:30入睡，8:00-9:00起床",
                        "exercise": "偶尔散步/快走",
                        "main_need": "从能量和白天状态先改",
                        "current_focus": "保肌减脂、稳定节律",
                    },
                    "one_sentence": (
                        "您的核心挑战不是减重，而是在高压白班生活里，给身体一个稳定的"
                        "启动-续航-收口信号，让精力不再来得快去得也快。"
                    ),
                    "goal_response": {
                        "goal_type": "general",
                        "judgement_type": "reasonable",
                        "title": "3. 对您目标的直接回应",
                        "content": "您希望白天更有精神、睡眠更稳。当前更适合先稳定睡眠和晚间收口，不设置减重挑战目标；连续执行达到标准后再评估体态变化。",
                        "base_goal": "稳定睡眠节律和晚间收口",
                        "challenge_goal": "不设置减重挑战目标",
                    },
                    "judgement_points": [
                        f"BMI {bmi} → 已在健康体重范围，当前重点不是减重而是稳定节律",
                        "入睡23:30-00:30 → 长期晚睡会干扰瘦素敏感度和次日食欲节律",
                        "情绪不稳时容易乱吃 → 压力管理是当前执行最大的风险变量",
                    ],
                    "current_state": "30岁男性，BMI 21.2，身高175cm，体重65kg。白班作息，晚睡。",
                    "insulin_resistance": "饭后容易困、碳水偏好倾向 → 提示存在胰岛素敏感度下降的可能趋势",
                    "leptin_sensitivity": "晚餐后基本能收口但压力大时会打破 → 提示压力状态下瘦素信号可能被抑制",
                    "hormone_stage": "30岁男性，长期高压白班可能加速恢复能力下滑",
                    "priority_direction": "保肌减脂、稳定节律",
                    "priority_items": [
                        "把入睡时间前移并稳定下来",
                        "守住晚餐后的进食边界",
                        "加入低门槛规律活动",
                    ],
                    "success_path": {
                        "intro": "12周成功路径，先稳定再微调",
                        "weight_band": weight_band,
                        "total_goal": "12周维持体重、优化腰围体态、重建稳定睡眠节律",
                        "day28_goal": "保持健康体重，睡眠时间提前至23:30前",
                        "week5_8": "0-3斤微幅调整，腰围开始收紧",
                        "week9_12": "维持稳定，腰围体态进一步优化",
                    },
                    "constitution": "当前体重在健康范围，但节律和情绪性进食是核心卡点",
                    "module_diagnosis": {
                        "rhythm": "入睡偏晚需前移，起床时间尚可",
                        "diet": "三餐按时、晚餐后基本不吃是优势，午餐碳水比例可能偏高",
                        "sleep": "基本不会夜醒但入睡时间偏晚",
                        "pressure": "情绪不稳压力大容易乱吃，压力已靠近边界",
                        "activity": "偶尔散步快走，可从轻度规律运动开始",
                    },
                    "expected_changes": {
                        "day7": "第4-7天睡眠提前后白天更清醒，咖啡因依赖下降",
                        "day14_28": "第14天睡眠趋于稳定，晚上失控次数明显减少",
                    },
                    "non_negotiable": {
                        "title": "晚餐后不再加水果坚果零食甜饮",
                        "content": "给身体一个完整的夜间代谢窗口，这是瘦素敏感度和胰岛素稳定性的底线动作",
                    },
                    "week_actions": [
                        "每天晚餐后不再吃水果坚果零食甜饮，只喝白水或常温柠檬水",
                        "午餐主食控制在1拳内，先吃蔬菜和蛋白质再吃主食",
                        "每周3次30分钟快走，避开睡前3小时",
                        "22:30手机熄屏，23:30前关灯",
                    ],
                    "product_schedule": {
                        "morning_c": "早C随早餐",
                        "noon_b": "午B随午餐，咖啡因敏感者从半份起",
                        "night_a": "晚A随晚餐",
                        "summary": "早C随早餐，午B随午餐，晚A随晚餐",
                    },
                    "product_meaning": "饮食和运动本身有效；产品只用于帮助执行个案节律，不是减重捷径。",
                    "special_reminder": (
                        # 模型把内部复核标签漏进用户可见正文 → 必须被整句剔除，且不留残句
                        "本方案需人工复核后才能执行。"
                        if manual_review
                        else ""
                    ) + "您的体重已经在健康范围，不要追求更瘦。这28天的目标是让身体在高压白班下也能稳定运转。",
                    "missing_information": ["腰围和体脂率待补测"],
                },
                "day28_execution_standard": {
                    "recipient": "蔡浩然",
                    "weight_band": weight_band,
                    "path_goal": "12周维持体重、优化腰围体态、重建稳定睡眠节律",
                    "day28_goal": "保持健康体重，睡眠时间提前至23:30前，减少咖啡因依赖",
                    "current_phase_goal": "第1-4周以稳定节律和建立晚间收口为核心",
                    "principle": "先稳节律再抓减脂，第1-2周只抓睡眠和晚餐收口两个动作",
                    "product_schedule": {
                        "morning_c": "随早餐",
                        "noon_b": "随午餐，咖啡因敏感者从半份起",
                        "night_a": "随晚餐",
                    },
                    "diet_rules": [
                        "午餐主食控制在1拳内",
                        "每天至少吃2拳蔬菜",
                        "晚餐后不再加水果、坚果、甜饮和零食",
                    ],
                    "exercise_rules": [
                        "每周3次30分钟快走，中低强度，以能正常说话为准",
                        "忙碌日改为晚餐后步行15分钟",
                    ],
                    "weekly_plan": [
                        {"week": "第1周", "core_task": "稳住底盘",
                         "acceptance_standard": "晚餐后不再吃任何东西；22:30熄屏23:30前关灯"},
                        {"week": "第2周", "core_task": "提高一致性",
                         "acceptance_standard": "继续维持晚间收口；午B从半份起"},
                        {"week": "第3周", "core_task": "抓结果指标",
                         "acceptance_standard": "每周3次30分钟快走；午餐主食控制在1拳内"},
                        {"week": "第4周", "core_task": "稳定复现",
                         "acceptance_standard": "综合执行前三周所有核心动作"},
                    ],
                    "daily_checkin": [
                        {"order": 1, "category": "product", "title": "个案产品排班是否按方案完成",
                         "answer_type": "yes_no", "options": ["是", "否"],
                         "example_answer": "早C已完成；午B未完成；晚A已完成"},
                        {"order": 2, "category": "sleep", "title": "是否在23:30前上床",
                         "answer_type": "yes_no", "options": ["是", "否"],
                         "example_answer": "是，已完成"},
                        {"order": 3, "category": "diet", "title": "蔬菜总量是否达到2拳",
                         "answer_type": "yes_no", "options": ["是", "否"],
                         "example_answer": "是，已完成"},
                        {"order": 4, "category": "activity", "title": "是否完成7000步或有氧30分钟",
                         "answer_type": "yes_no", "options": ["是", "否"],
                         "example_answer": "是，已完成"},
                        {"order": 5, "category": "state", "title": "饭后困评分（1分很困、5分不困）",
                         "answer_type": "score_1_5", "options": ["1", "2", "3", "4", "5"],
                         "example_answer": "4分，饭后精神尚可"},
                    ],
                    "weekly_standard": "个案产品排班和晚间收口至少完成5天；完成计划运动；至少记录1次体重或围度/体感",
                    "adjust_stop_conditions": "持续心慌或睡眠明显恶化时暂停并联系班主任；胸痛、呼吸困难或明显不适时及时就医。",
                },
            },
        }
    })


def _manual_review_safety_response() -> str:
    """模拟风控链确认“长期处方药待运营核对”，不让文档模型参与判定。"""

    return json.dumps(
        {
            "safety_review_required": True,
            "safety_review_reasons": ["问卷填写当前长期用药"],
            "muscle_insufficiency": False,
            "disease_or_medication_limit": False,
            "recovery_risk": False,
            "doctor_specified_lower_weight_kg": None,
            "waist_height_conflict": False,
            "waist_height_conflict_reason": None,
            "persistent_risk_flags": [
                {
                    "code": "long_term_medication",
                    "reason": "问卷填写当前长期用药",
                    "evidence": "当前长期服用处方降压药",
                }
            ],
            "evidence_notes": ["健康情况填写：当前长期服用处方降压药"],
        },
        ensure_ascii=False,
    )


def test_intake_prompt_keeps_goal_rules_free_of_contradictions():
    """关键目标优先级必须保留，但接口细节和重复规则不能再塞回提示词。"""
    prompt = _intake_generation_system_prompt()

    assert len(prompt) < 4_000
    assert "最终只输出一个完整有效的 JSON 对象" in prompt
    assert "shared_analysis、profile、documents" in prompt
    assert "reasoning_content" not in prompt
    assert "content 中" not in prompt
    assert sum(prompt.count(word) for word in ("禁止", "不得", "严禁", "不要")) < 30
    assert prompt.count("示例") + prompt.count("例如") <= 3

    assert "intake_facts > 问卷原文" in prompt
    assert "shared_analysis 是唯一判断源" in prompt
    assert "运动频次只能使用 shared_analysis.activity_plan" in prompt
    assert "adult_stable_comfort_weight_kg" in prompt
    assert "你不会获得风险等级、人工复核状态、复核原因或医疗自由文本" in prompt
    assert "D7复评" not in prompt
    assert "manual_review_required" not in prompt
    assert "【A带的无数字目标】" in prompt
    assert "所有用户可见字段都不得出现减重斤数" in prompt
    assert "不得用 BMI、用户期待或生活方式事实改带" in prompt
    assert "年龄不能单独推出围绝经、更年期、绝经等阶段" in prompt
    assert "必须逐字使用同一个量和时间" in prompt
    assert CONCISENESS_RULE in prompt
    assert "两者冲突时一律先保信息完整" in prompt


def test_intake_prompt_requires_five_independent_daily_checkin_questions():
    prompt = _intake_generation_system_prompt()
    instructions = prompt + json.dumps(combined_prompt_schema(), ensure_ascii=False)

    # 字段类型与格式只在 schema 定义，system prompt 只保留因人出题的选择原则。
    assert "唯一、连续、严格递增；数组长度必须正好5项，禁止4项或6项" in instructions
    assert "禁止写「每日固定打卡」「5项打卡」「打卡清单」等栏目标题或说明文字" in instructions
    assert "序号由程序按 order 渲染" in instructions
    assert "今天/今日/今早/今晚/昨天/昨日/昨晚/当天/本日」等相对时间词" in instructions
    assert "也禁止一道题同时问时间和饮食结构" in instructions
    assert "只能取 yes_no / score_1_5 / number / text" in instructions
    assert "只能取 product / diet / activity / sleep / state" in instructions
    assert "蔬菜总量是否至少2拳" in instructions

    assert "禁止所有用户都输出同一套固定五题" in prompt
    assert "恰好 1 项 category=activity" in prompt
    assert "至少2项设计成正向动作" in prompt
    assert "禁止把两个问题合并成一项来腾名额" in prompt
    assert "「情绪稳定度评分」与「饭后困评分」是两个独立候选问题" in prompt
    assert "若两项同时入选 daily_checkin，必须各占 1 项" in prompt
    assert "评分题必须写明「1分=」和「5分=」各自含义" in prompt

    english_prompt = _intake_generation_system_prompt(language="en")
    assert "Any score_1_5 item must state what 1 and 5 mean" in english_prompt

    assert "weight_band 永远只能逐字复制系统算好的业务带位" in prompt
    assert "不得用 BMI、用户期待或生活方式事实改带" in prompt
    assert "【证据职责分开】" in prompt
    assert "week5_8、week9_12 必须逐字复制 intake_facts.stage_targets" in prompt
    assert "adult_stable_comfort_weight_kg" in prompt
    assert "当前 BMI、体重、腰围只是单点状态" in prompt
    assert "主食和蔬菜可放在同一道饮食题或同一句" in prompt
    assert "是否做到每餐主食控制在1拳内，且全天蔬菜至少2拳？" in prompt
    assert "蔬菜统一写「全天至少2拳」" in prompt
    assert "产品答案只包含早C、午B、晚A及各自一个状态" in prompt

    assert "priority_items 严格3项" in prompt
    assert "week_actions 写4-5条" in prompt
    assert "【一页硬预算】" in prompt
    assert "diet_rules 最多3条，每条≤34字" in prompt
    assert "exercise_rules 最多2条，每条≤44字" in prompt

    schema = combined_prompt_schema()
    energy_spec = schema[DocumentType.ENERGY_RESTART_PRESCRIPTION.value]
    execution_spec = schema[DocumentType.DAY28_EXECUTION_STANDARD.value]
    assert "core_conclusions" in energy_spec
    assert "body_trend" in energy_spec["core_conclusions"]
    assert "trend_direct_evidence" in energy_spec["core_conclusions"]
    assert "trend_support_signals" in energy_spec["core_conclusions"]
    assert "需人工复核" not in energy_spec["core_conclusions"]["management_band"].replace("禁止写「需人工复核」等内部运营标签", "")
    assert "A带只能定性说明更适合修复塑形" in energy_spec["core_conclusions"]["band_explanation"]
    assert "A带只能定性概括数字减重诉求" in energy_spec["goal_response"]["content"]
    assert "A带不得复述具体减重斤数" in energy_spec["goal_response"]["user_goal"]
    assert "A带只做定性判断" in energy_spec["goal_response"]["judgement"]
    assert "A带禁止任何减重数字" in energy_spec["goal_response"]["base_goal"]
    assert "不得仅凭年龄推测围绝经、更年期、绝经等特殊生理阶段" in energy_spec["hormone_stage"]
    assert "D7复评" not in energy_spec["goal_response"]["challenge_goal"]
    assert "diet_rules" in execution_spec
    assert "exercise_rules" in execution_spec
    assert "activity_plan.sessions_per_week" in energy_spec["week_actions"][0]
    assert "activity_plan.sessions_per_week" in execution_spec["exercise_rules"][0]
    # V2.2 要求执行文件也能独立核对管理带、基础/挑战和长期方向。
    assert "weight_band" in execution_spec
    assert "base_goal" in execution_spec
    assert "challenge_goal" in execution_spec
    assert "path_goal" in execution_spec

    daily_checkin_spec = execution_spec["daily_checkin"]
    assert isinstance(daily_checkin_spec, list) and len(daily_checkin_spec) == 1
    item_spec = daily_checkin_spec[0]
    assert set(item_spec) == {"order", "category", "title", "answer_type", "options", "example_answer"}
    assert "数组长度必须正好5项，禁止4项或6项" in item_spec["order"]
    assert "第1项固定 product，5项中必须有1项 activity" in item_spec["category"]
    assert "严禁合并成一项综合评分" in item_spec["title"]
    assert "禁止写「每餐蔬菜至少2拳」" in item_spec["title"]
    assert "只能取 yes_no / score_1_5 / number / text" in item_spec["answer_type"]
    assert "title 必须写明「1分=」和「5分=」各自含义" in item_spec["answer_type"]
    assert "早C、午B、晚A" in item_spec["example_answer"]
    assert "早C已完成；午B未完成；晚A已完成" in item_spec["example_answer"]
    assert "不是用户实际完成记录" in item_spec["example_answer"]
    assert "主食和蔬菜可在同一条规则中" in execution_spec["diet_rules"][0]

    english_schema = combined_prompt_schema(language="en")
    english_execution_spec = english_schema[DocumentType.DAY28_EXECUTION_STANDARD.value]
    assert "Score titles must state what 1 and 5 mean" in english_execution_spec["daily_checkin"][0]["title"]

    fallback = _daily_checkin_text(None)
    assert "个案产品排班是否按方案完成" in fallback
    assert "饭后困评分（1分很困、5分不困）" in fallback
    assert "情绪稳定度和饭后困综合评分" not in fallback


def test_delivery_overview_keeps_missing_waist_and_labels_bodyfat():
    payloads = {
        DocumentType.ENERGY_RESTART_PRESCRIPTION.value: {
            "overview": {"waist_bodyfat": "30"},
        }
    }

    _apply_intake_facts_to_document_payloads(
        payloads,
        {
            "weight_loss_band": "B带｜轻盈启动",
            "height_cm": "165",
            "weight_kg": "66",
            "bmi": "24.2",
            "waist": "未提供",
            "bodyfat": "30",
        },
    )

    overview = payloads[DocumentType.ENERGY_RESTART_PRESCRIPTION.value]["overview"]
    assert overview["waist_bodyfat"] == "腰围未提供 / 体脂率30%"


def test_delivery_instruction_contract_rejects_unfounded_stage_per_meal_vegetables_and_bedtime_conflict():
    shared = {
        "weight_band": "B带｜轻盈启动",
        "base_goal": "基础目标",
        "challenge_goal": "挑战目标",
        "day28_goal": "28天任务",
        "long_term_goal": "长期方向",
        "week5_8_target": "第5-8周方向",
        "week9_12_target": "第9-12周方向",
    }
    payloads = {
        DocumentType.ENERGY_RESTART_PRESCRIPTION.value: {
            "goal_response": {"base_goal": "基础目标", "challenge_goal": "挑战目标"},
            "success_path": {
                "weight_band": "B带｜轻盈启动",
                "day28_goal": "28天任务",
                "total_goal": "长期方向",
                "week5_8": "第5-8周方向",
                "week9_12": "第9-12周方向",
            },
            "week_actions": ["50岁处于围绝经期，23:00前上床"],
        },
        DocumentType.DAY28_EXECUTION_STANDARD.value: {
            "base_goal": "基础目标",
            "challenge_goal": "挑战目标",
            "day28_goal": "28天任务",
            "path_goal": "长期方向",
            "diet_rules": ["每餐保证1拳蔬菜"],
            "daily_checkin": [{"title": "23:30前上床"}],
        },
    }
    facts = {
        "basic_target_jin_or_range": "基础目标",
        "challenge_target_jin_or_review_text": "挑战目标",
        "weight_loss_band": "B带｜轻盈启动",
    }

    errors = _document_consistency_errors(payloads, shared, facts)

    assert "交付文件出现无来源的特殊生理阶段判断" in errors
    assert "交付文件将蔬菜份量写成每餐口径" in errors
    assert "交付文件的上床时间不一致" in errors


@pytest.mark.asyncio
async def test_questionnaire_submit_creates_profile_and_documents(
    admin_client,
    create_case,
    db_session,
    mock_ai,
    cai_haoran_answers,
    wait_background,
):
    mock_ai._responses = {
        INTAKE_SYSTEM_PROMPT_PREFIX: _make_intake_mock_response(),
    }

    group_id = f"test-group-intake-{secrets.token_hex(6)}"
    resp = await create_case({
        "group_id": group_id,
        "user": {"name": "蔡浩然"},
    })
    assert resp.status_code == 201, resp.text
    sc = resp.json()
    token = sc["questionnaire_token"]
    case_id = sc["id"]

    submit_resp = await admin_client.post(
        f"/questionnaires/{token}/submit?template=intake",
        json={"answers": cai_haoran_answers},
    )
    assert submit_resp.status_code == 201, submit_resp.text
    await wait_background()

    # 重新生成必须只记录本次任务耗时，不能用首次问卷时间累计；旧算法会在超过约24.8天后溢出 int32。
    submission = await db_session.scalar(
        select(QuestionnaireSubmission).where(
            QuestionnaireSubmission.service_case_id == case_id,
            QuestionnaireSubmission.template_key == "intake",
        )
    )
    assert submission is not None
    submission.created_at = datetime.now(timezone.utc) - timedelta(days=60)
    await db_session.commit()

    gen_resp = await admin_client.post(
        f"/service-cases/{case_id}/generate-documents"
    )
    assert gen_resp.status_code == 200, gen_resp.text
    await wait_background()

    profile = await db_session.scalar(
        select(UserProfile).where(UserProfile.service_case_id == case_id)
    )
    assert profile is not None
    overview = (profile.profile_json or {}).get("overview") or {}
    assert overview.get("bmi") == "21.2"
    assert profile.profile_json.get("management_band") == "B带｜轻盈启动"
    assert profile.profile_json.get("body_trend") == "基本稳定"
    assert profile.profile_json.get("manual_review_required") is False
    tags = (profile.profile_json or {}).get("tags") or []
    assert tags == ["B带", "晚睡习惯", "下午犯困"]
    personalized_fields = (profile.profile_json or {}).get("personalized_checkin_fields") or []
    assert len(personalized_fields) == 5

    # 内容准确性验证：Profile 字段与结构化 daily_checkin 完全一致（顺序、标题、类型、选项）
    expected_fields = [
        ("个案产品排班是否按方案完成", "RADIO", ["是", "否"]),
        ("是否在23:30前上床", "RADIO", ["是", "否"]),
        ("蔬菜总量是否达到2拳", "RADIO", ["是", "否"]),
        ("是否完成7000步或有氧30分钟", "RADIO", ["是", "否"]),
        # 评分题由 answer_type=score_1_5 决定，选项固定 1-5，不再退化成自由填空
        ("饭后困评分（1分很困、5分不困）", "RADIO", ["1", "2", "3", "4", "5"]),
    ]
    for i, (pf, (label, field_type, options)) in enumerate(zip(personalized_fields, expected_fields)):
        assert pf["label"] == label, f"第 {i + 1} 项标签不一致"
        assert pf["type"] == field_type, f"第 {i + 1} 项类型不一致"
        assert pf["options"] == options, f"第 {i + 1} 项选项不一致"
        assert pf["required"] is True

    # 验证打卡链接 API 返回与 Profile 一致
    resp = await admin_client.get(f"/questionnaires/{token}?template=personalized_checkin")
    assert resp.status_code == 200
    api_fields = resp.json()["fields"]
    assert len(api_fields) == 5
    for i, (af, pf) in enumerate(zip(api_fields, personalized_fields)):
        assert af["label"] == pf["label"], f"API 第 {i+1} 项与 Profile 不一致"
        assert af["type"] == pf["type"], f"API 第 {i+1} 项类型与 Profile 不一致"
        assert af["options"] == pf["options"], f"API 第 {i+1} 项选项与 Profile 不一致"
        assert af["required"] == pf["required"]

    docs = list(await db_session.scalars(
        select(UserDocument).where(UserDocument.service_case_id == case_id)
    ))
    doc_types = {d.document_type for d in docs}
    assert DocumentType.ENERGY_RESTART_PRESCRIPTION in doc_types
    assert DocumentType.DAY28_EXECUTION_STANDARD in doc_types
    docs_by_type = {document.document_type: document for document in docs}
    diagnosis = docs_by_type[DocumentType.ENERGY_RESTART_PRESCRIPTION]
    execution = docs_by_type[DocumentType.DAY28_EXECUTION_STANDARD]
    assert diagnosis.title == "个人能量重启诊断报告"
    assert diagnosis.content_json["title"] == "个人能量重启诊断报告"
    assert "近3个月身体趋势：基本稳定" in diagnosis.content_md
    assert [block["title"] for block in diagnosis.content_json["blocks"]] == [
        "1. 用户信息概览表",
        "2. 一句话结论与两项核心判断",
        "3. 对您目标的直接回应",
        "4. 为什么我会这样判断",
        "5. 您的成功路径，不是只看这28天",
        "6. 您真正需要优先解决的三件事",
        "7. 代谢节律与恢复状态评估",
        "8. 模块诊断：节律 / 饮食 / 睡眠 / 压力 / 身体活动",
        "9. 早C、午B、晚A对您的意义",
        "10. 本周直接执行",
        "11. 您可能先看到的变化",
        "12. 特别提醒",
        "13. 需要补充的信息",
        "14. 出现这些情况请立刻停下",
    ]
    # 本周直接执行放宽到最多5条，第4条不再被截断——参考设计要求3-5条覆盖三件事之外的维度
    assert "22:30手机熄屏，23:30前关灯" in diagnosis.content_md
    assert execution.title == "28天高燃人生执行建议书"
    assert execution.content_json["title"].endswith("｜28天高燃人生执行建议书")
    execution_block_keys = [block["key"] for block in execution.content_json["blocks"]]
    assert execution_block_keys == [
        "goal_summary",
        "current_phase_goal",
        "principle",
        "product_schedule",
        "diet_rules",
        "exercise_rules",
        "weekly_plan",
        "daily_checkin",
        "weekly_standard",
        "adjust_stop_conditions",
    ]
    assert "fixed_rhythm" not in execution_block_keys
    for document in (diagnosis, execution):
        download_resp = await admin_client.get(f"/documents/{document.id}/download")
        assert download_resp.status_code == 200, download_resp.text
        assert download_resp.headers["content-type"].startswith(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        assert download_resp.content.startswith(b"PK")
        assert len(download_resp.content) > 1000

    updated_case = await db_session.get(ServiceCase, case_id)
    assert updated_case is not None
    assert updated_case.status in (ServiceCaseStatus.REVIEWING, ServiceCaseStatus.IN_SERVICE)
    assert updated_case.questionnaire_to_llm_output_ms is not None
    assert 0 <= updated_case.questionnaire_to_llm_output_ms < 60_000

    originals = list(
        await db_session.scalars(
            select(UserDocumentOriginal).where(
                UserDocumentOriginal.document_id.in_([document.id for document in docs])
            )
        )
    )
    assert len(originals) == len(docs)
    originals_by_document_id = {original.document_id: original for original in originals}
    original_execution = originals_by_document_id[execution.id]
    assert original_execution.title == execution.title
    assert original_execution.content_md == execution.content_md
    assert original_execution.content_json == execution.content_json

    edited_content_json = deepcopy(execution.content_json)
    edited_content_json["blocks"][0]["content"] = "运营审核后的当前稿"
    edit_resp = await admin_client.patch(
        f"/documents/{execution.id}",
        json={"content_json": edited_content_json},
    )
    assert edit_resp.status_code == 200, edit_resp.text
    await db_session.refresh(execution)
    await db_session.refresh(original_execution)
    assert "运营审核后的当前稿" in execution.content_md
    assert original_execution.content_md != execution.content_md
    assert original_execution.content_json != execution.content_json


@pytest.mark.asyncio
async def test_generating_status_on_submit(
    admin_client,
    create_case,
    mock_ai,
    cai_haoran_answers,
):
    """验证问卷提交后 status 被设置为 generating——用 AsyncMock 阻止后台任务执行，保持中间状态"""
    mock_ai._responses = {
        INTAKE_SYSTEM_PROMPT_PREFIX: _make_intake_mock_response(),
    }

    group_id = f"test-group-genstatus-{secrets.token_hex(6)}"
    resp = await create_case({
        "group_id": group_id,
        "user": {"name": "Test Status User"},
    })
    assert resp.status_code == 201, resp.text
    sc = resp.json()
    token = sc["questionnaire_token"]
    case_id = sc["id"]

    with patch(
        "app.api.routes.questionnaires._generate_profile_and_documents",
        new_callable=AsyncMock,
    ):
        submit_resp = await admin_client.post(
            f"/questionnaires/{token}/submit?template=intake",
            json={"answers": cai_haoran_answers},
        )
        assert submit_resp.status_code == 201, submit_resp.text

    get_resp = await admin_client.get(f"/service-cases/{case_id}")
    assert get_resp.status_code == 200, get_resp.text
    assert get_resp.json()["status"] == "generating"


@pytest.mark.asyncio
async def test_intake_submit_records_profile_completed_outcome(
    admin_client,
    create_case,
    db_session,
    cai_haoran_answers,
):
    group_id = f"test-group-intake-outcome-{secrets.token_hex(6)}"
    resp = await create_case({
        "group_id": group_id,
        "user": {"name": "Outcome Intake User"},
    })
    assert resp.status_code == 201, resp.text
    sc = resp.json()
    token = sc["questionnaire_token"]
    case_id = sc["id"]

    with patch(
        "app.api.routes.questionnaires._generate_profile_and_documents",
        new_callable=AsyncMock,
    ):
        submit_resp = await admin_client.post(
            f"/questionnaires/{token}/submit?template=intake",
            json={"answers": cai_haoran_answers},
        )
    assert submit_resp.status_code == 201, submit_resp.text

    event = await db_session.scalar(
        select(OutcomeEvent).where(
            OutcomeEvent.service_case_id == case_id,
            OutcomeEvent.milestone == "day1",
            OutcomeEvent.field_key == "profile_completed",
        )
    )
    assert event is not None
    assert event.event_type == "completed"
    assert event.source == "questionnaire"
    assert event.confidence == 1.0

    state = await db_session.scalar(
        select(OutcomeStageState).where(
            OutcomeStageState.service_case_id == case_id,
            OutcomeStageState.milestone == "day1",
        )
    )
    assert state is not None
    assert "profile_completed" in (state.collected_fields or {})
    assert "profile_completed" not in (state.missing_fields or [])


@pytest.mark.asyncio
async def test_personalized_checkin_uses_latest_document_daily_checkin_block(admin_client, create_case, db_session):
    group_id = f"test-personalized-checkin-{secrets.token_hex(6)}"
    resp = await create_case({
        "group_id": group_id,
        "user": {"name": "旧画像用户"},
    })
    assert resp.status_code == 201, resp.text
    service_case = resp.json()
    case_id = service_case["id"]
    token = service_case["questionnaire_token"]

    db_session.add(
        UserProfile(
            service_case_id=case_id,
            profile_json={
                "summary": "旧画像",
                "personalized_checkin_fields": [
                    {
                        "id": "old_weight",
                        "key": "old_weight",
                        "type": "SIMPLE",
                        "title": "今日晨起体重（kg）",
                        "label": "今日晨起体重（kg）",
                        "required": True,
                        "options": [],
                    }
                ] + [
                    {
                        "id": f"old_default_{index}",
                        "key": f"old_default_{index}",
                        "type": "RADIO",
                        "title": f"旧默认项 {index}",
                        "label": f"旧默认项 {index}",
                        "required": True,
                        "options": ["是", "否"],
                    }
                    for index in range(2, 6)
                ],
            },
            summary="旧画像",
        )
    )
    db_session.add(
        UserDocument(
            service_case_id=case_id,
            document_type=DocumentType.DAY28_EXECUTION_STANDARD,
            title="28天执行标准与打卡要求",
            content_md="ok",
            content_json={
                "blocks": [
                    {
                        "key": "daily_checkin",
                        "title": "每日固定打卡（每天只打这5项）",
                        "kind": "text",
                        "content": (
                            "1. 今日是否完成早C/午B/晚A？（是/否）\n"
                            "2. 今日是否在23:30前上床？（是/否）\n"
                            "3. 今日蔬菜摄入是否达到2拳？（是/否）\n"
                            "4. 今日是否完成7000步或有氧30分钟？（是/否）\n"
                            "5. 今日饭后困评分（1-5分，1很困5分不困）"
                        ),
                    }
                ]
            },
        )
    )
    await db_session.commit()

    resp = await admin_client.get(f"/questionnaires/{token}?template=personalized_checkin")
    assert resp.status_code == 200, resp.text
    fields = resp.json()["fields"]
    assert len(fields) == 5
    # 历史文档是字符串格式、题面带「今日」等相对时间词：新的文风规则不能把它判成无效，
    # 已发出去的打卡清单必须原样可读。
    assert fields[0]["title"] == "今日是否完成早C/午B/晚A？（是/否）"
    assert fields[0]["type"] == "RADIO"
    assert fields[0]["options"] == ["是", "否"]
    # 第5项是评分题：现在按 1-5 分制给固定选项，不再退化成自由填空
    assert fields[4]["title"] == "今日饭后困评分（1-5分，1很困5分不困）"
    assert fields[4]["type"] == "RADIO"
    assert fields[4]["options"] == ["1", "2", "3", "4", "5"]
    assert all(not field["title"].startswith("旧默认项") for field in fields)


@pytest.mark.asyncio
async def test_intake_facts_bmi_calculation(cai_haoran_answers):
    template = default_questionnaire_template("intake")
    fields = template.get("fields", [])

    facts = build_intake_fact_summary(fields, cai_haoran_answers)

    assert facts["bmi"] == "21.2", f"Expected BMI '21.2', got '{facts['bmi']}'"
    assert facts["weight_loss_band"] == "B带｜轻盈启动", (
        f"Expected B带, got '{facts['weight_loss_band']}'"
    )


@pytest.mark.parametrize(
    ("safe_room_kg", "expected"),
    [
        (0.0, "A带｜修复塑形"),
        (2.49, "A带｜修复塑形"),
        (2.5, "B带｜轻盈启动"),
        (4.99, "B带｜轻盈启动"),
        (5.0, "C带｜明确减脂"),
        (9.99, "C带｜明确减脂"),
        (10.0, "D带｜系统减重"),
    ],
)
def test_safe_reduction_room_uses_exact_management_band_boundaries(
    safe_room_kg: float,
    expected: str,
):
    assert _management_band_from_safe_room(safe_room_kg) == expected


def test_model_and_user_goal_cannot_override_safe_room_band():
    facts = {
        "weight_loss_band": "B带｜轻盈启动",
        "safe_reduction_room_kg": "3.75",
        "day28_expectation": "28天减重10斤",
    }

    assert (
        _resolve_management_band({"weight_band": "D带｜系统减重"}, facts)
        == "B带｜轻盈启动"
    )


def test_service_case_high_risk_forces_manual_review_even_when_model_cancels_it():
    service_case = ServiceCase(
        risk_level="high",
        risk_flags=[
            {
                "code": "pregnancy",
                "level": "high",
                "reason": "用户明确处于孕期",
                "evidence": "当前怀孕",
                "source": "questionnaire",
            }
        ],
    )
    shared = {
        "safety_status": "standard",
        "manual_review_required": False,
        "manual_review_reasons": [],
        "weight_band": "B带｜轻盈启动",
    }

    _force_high_risk_manual_review(shared, service_case)

    assert shared["weight_band"] == "B带｜轻盈启动"
    assert shared["manual_review_required"] is True
    assert shared["safety_status"] == "manual_review"
    assert shared["manual_review_reasons"] == ["用户明确处于孕期"]


def test_manual_review_keeps_calculated_challenge_target():
    facts = build_intake_fact_summary(_band_fields(), {"h": "165", "w": "70"})
    shared: dict[str, object] = {"manual_review_required": True}

    _apply_canonical_target_contract(shared, facts)

    assert shared["base_goal"] == "28天减重5-7斤"
    assert shared["challenge_goal"] == "当前不开放高承诺目标"
    assert facts["challenge_target_jin_or_review_text"] == "当前不开放高承诺目标"


def test_manual_review_does_not_replace_unresolved_target_contract():
    facts = build_intake_fact_summary(_band_fields(), {"h": "165", "w": "暂未测量"})
    shared: dict[str, object] = {"manual_review_required": True}

    _apply_canonical_target_contract(shared, facts)

    assert shared["base_goal"] == facts["basic_target_jin_or_range"]
    assert shared["challenge_goal"] == facts["challenge_target_jin_or_review_text"]


def test_document_model_input_excludes_review_state_and_operator_health_text():
    fields = [
        {"key": "need", "title": "您最想先解决什么问题"},
        {
            "key": "health",
            "title": "过敏 / 正在用药 / 既往病史补充",
            "operator_only": True,
        },
    ]
    facts = {
        "weight_loss_band": "B带｜轻盈启动",
        "basic_target_jin_or_range": "28天减重5-6斤",
        "challenge_target_jin_or_review_text": "向7斤推进",
        "health_note": "正在服用降压药",
        "safety_review_required": True,
        "safety_review_reasons": ["问卷填写当前长期用药"],
        "protected_lower_weight_kg": "58",
    }

    document_facts = _document_model_intake_facts(facts)
    document_answers = _document_model_readable_answers(
        fields,
        {"need": "晚间收口", "health": "正在服用降压药"},
    )

    assert document_facts == {
        "weight_loss_band": "B带｜轻盈启动",
        "basic_target_jin_or_range": "28天减重5-6斤",
        "challenge_target_jin_or_review_text": "向7斤推进",
    }
    assert "晚间收口" in document_answers
    assert "降压药" not in document_answers


def test_default_v2_template_preserves_existing_keys_and_hides_new_answers():
    fields = default_questionnaire_template("intake")["fields"]
    keys = [str(field.get("key") or "") for field in fields]

    assert len(fields) == 82
    assert len(keys) == len(set(keys))
    assert keys.count("CHECKBOX1774104915668mn0g9dxy") == 1
    assert "CHECKBOX17776518759860wlspec" in keys
    assert "RADIO17776518759861occnew" in keys
    assert keys.index("SIMPLE1774699899282mnaahy8j") < keys.index(
        "SIMPLE17776518759868srcopen"
    )

    hidden_keys = {
        str(field.get("key") or "")
        for field in fields
        if field.get("safety_hidden") is True
    }
    assert len(hidden_keys) == 9
    assert all(
        next(field for field in fields if str(field.get("key") or "") == key).get(
            "operator_only"
        )
        is True
        for key in hidden_keys
    )

    answers = {
        str(field.get("key") or ""): f"answer-{field.get('key')}"
        for field in fields
        if field.get("key")
    }
    health_key = "SIMPLE1774108071693mn0i515a"
    health_answer = "正在服用降压药"
    answers[health_key] = health_answer

    safety_answers = _safety_model_readable_answers(fields, answers)
    document_answers = _document_model_readable_answers(fields, answers)
    assert health_answer in safety_answers
    assert health_answer not in document_answers
    assert all(f"answer-{key}" not in safety_answers for key in hidden_keys)
    assert all(f"answer-{key}" not in document_answers for key in hidden_keys)


def test_weight_units_stable_comfort_weight_and_waist_ratio_are_normalized():
    fields = [
        {"key": "h", "title": "您的身高（cm/厘米）"},
        {"key": "w", "title": "您当前体重（kg/公斤）"},
        {"key": "waist", "title": "您当前腰围（cm）"},
        {"key": "stable", "title": "成年后让您轻盈且稳定过1年以上的体重"},
    ]

    facts = build_intake_fact_summary(
        fields,
        {"h": "170", "w": "260斤", "waist": "0.85m", "stable": "150斤"},
    )

    assert facts["weight_kg"] == "130"
    assert facts["adult_stable_comfort_weight_kg"] == "75"
    assert facts["waist_cm"] == "85"
    assert facts["waist_height_ratio"] == "0.500"
    assert facts["28_day_auto_cap_kg"] == "4"
    assert facts["28_day_display_cap_jin"] == 8


def test_default_intake_questionnaire_includes_stable_comfort_weight_after_current_weight():
    fields = default_questionnaire_template("intake")["fields"]
    keys = [str(field.get("key") or "") for field in fields]
    current_index = keys.index("SIMPLE1774104087629mn0frn0t")
    stable_index = keys.index("SIMPLE_ADULT_STABLE_COMFORT_WEIGHT_KG")
    stable = fields[stable_index]

    assert stable_index == current_index + 1
    assert stable["required"] is True
    assert "稳定过1年以上" in stable["title"]
    assert "没有" in stable["subtitle"] and "不确定" in stable["subtitle"]


def test_generation_block_reasons_only_cover_critical_measurement_failures():
    missing = build_intake_fact_summary(_band_fields(), {"h": "165", "w": "未测"})
    assert _document_generation_block_reasons(missing) == [
        "缺少可识别的当前体重，无法计算管理带和28天目标"
    ]

    fields = [*_band_fields(), {"key": "waist", "title": "您当前腰围（cm）"}]
    conflict = build_intake_fact_summary(fields, {"h": "165", "w": "70", "waist": "8"})
    assert _document_generation_block_reasons(conflict) == [
        "腰围与身高存在明显单位或录入矛盾，请复测并确认后再生成"
    ]

    plausible = build_intake_fact_summary(fields, {"h": "165", "w": "110", "waist": "110"})
    assert _document_generation_block_reasons(plausible) == []


@pytest.mark.asyncio
async def test_intake_facts_expose_safe_reduction_room(cai_haoran_answers):
    """175cm/65kg → 空间3.75kg，正常BMI仍按唯一数值主轴进入B带。"""

    template = default_questionnaire_template("intake")
    facts = build_intake_fact_summary(template.get("fields", []), cai_haoran_answers)

    assert facts["baseline_weight_kg"] == "65"
    assert facts["baseline_weight_provisional"] is True
    assert facts["absolute_low_weight_boundary_kg"] == "56.66"
    assert facts["automatic_review_weight_kg"] == "61.25"
    assert facts["protected_lower_weight_kg"] == "61.25"
    assert facts["safe_reduction_room_kg"] == "3.75"
    assert facts["weight_loss_band"] == "B带｜轻盈启动"
    assert facts["weight_loss_room_exhausted"] == "否"
    # 客户公式：min(65*5%=3.25, 4, 3.75)=3.25，不再混入旧的0.8折扣。
    assert facts["day28_goal_cap_kg"] == "3.25"
    assert "day28_max_loss_kg" not in facts


def test_intake_facts_room_exhausted_blocks_numeric_goal():
    """空间耗尽（BMI 18.6 的真实用户）→ 标记为「是」且规划上限归零。"""

    fields = [
        {"key": "h", "title": "您的身高（cm/厘米）"},
        {"key": "w", "title": "您当前体重（kg/公斤）"},
    ]
    facts = build_intake_fact_summary(fields, {"h": "166", "w": "51.3"})

    assert facts["weight_loss_room_exhausted"] == "是"
    assert facts["safe_reduction_room_kg"] == "0"
    assert facts["weight_loss_band"] == "A带｜修复塑形"
    assert facts["day28_goal_cap_kg"] == "0"


def test_intake_facts_safe_room_missing_without_height_or_weight():
    facts = build_intake_fact_summary(
        [{"key": "h", "title": "您的身高（cm/厘米）"}],
        {"h": "暂未测量"},
    )

    assert facts["protected_lower_weight_kg"] == "暂无数据"
    assert facts["safe_reduction_room_kg"] == "暂无数据"
    assert facts["weight_loss_band"] == "需补充身高体重后判断"
    assert facts["weight_loss_room_exhausted"] == "暂无数据"
    assert facts["day28_goal_cap_kg"] == "暂无数据"


def _band_fields() -> list[dict[str, object]]:
    return [
        {"key": "h", "title": "您的身高（cm/厘米）"},
        {"key": "w", "title": "您当前体重（kg/公斤）"},
    ]


def _stage_targets_for(height: str, weight: str) -> dict[str, str]:
    return build_intake_fact_summary(_band_fields(), {"h": height, "w": weight})["stage_targets"]


def test_stage_targets_follow_the_band_table():
    """区间由代码查表，客户 V2.1 §7.2-7.4 的数字逐带核对。"""

    # 165cm：BMI20 线 54.45kg
    b_band = _stage_targets_for("165", "58")  # room 3.55kg → B带
    assert "累计5-7斤" in b_band["week5_8"]  # 上界被 3.55kg=7.1斤 封顶
    # 封顶后上界取整正好等于下界（7）时不能写成「7-7斤」——真实验证里出现过。
    assert b_band["week9_12"] == "从入营起算累计不超过7斤，达到条件后再评估"
    c_band = _stage_targets_for("165", "62")  # room 7.55kg → C带
    assert c_band["week5_8"] == "从入营起算累计8-12斤"
    assert c_band["week9_12"] == "从入营起算累计10-15斤，或提前达到适宜目标后转入维持"
    d_band = _stage_targets_for("170", "95")  # room 37.2kg → D带
    assert d_band["week5_8"] == "不预设累计斤数，按第一个28天的实际结果、耐受度和肌肉保护情况继续推进"
    assert d_band["week9_12"] == "从入营起算累计16-20斤，或提前达到适宜目标后转入维持"


def test_stage_targets_capped_by_safe_room():
    """默认区间上界可能超出这个人的安全空间，必须收窄——C带下沿就会踩到。"""

    # 165cm/60kg：room 5.55kg = 11.1斤。C带第9-12周默认 10-16 斤，上界超了。
    targets = _stage_targets_for("165", "60")
    assert targets["week9_12"] == "从入营起算累计10-11斤，或提前达到适宜目标后转入维持"

    # 空间连下界都撑不住时不给区间，只给上限。165cm/57.5kg：room 3.05kg = 6.1斤 < 7。
    targets = _stage_targets_for("165", "57.5")
    assert targets["week9_12"] == "从入营起算累计不超过6斤，达到条件后再评估"


def _day28_targets_for(height: str, weight: str) -> dict[str, str]:
    return build_intake_fact_summary(_band_fields(), {"h": height, "w": weight})["day28_targets"]


def test_day28_targets_derive_from_the_monthly_cap_not_the_band_table():
    """28天目标按 day28_goal_cap_kg（一个月最多5%）推，不查 §7.2-7.4 的带位斤数表。

    客户 2026-07-31 确认"一个月减5%、每个带是3个月的"，且 §7.2-7.4 的 28 天区间与
    §6.4 的 5% 上限整段越界（C带挑战10斤需要100kg，而100kg早已是D带），无法调和。
    """

    # 165cm/62kg → C带，cap 3.1kg = 6.2斤 → 展示上限 6 斤。
    c_band = _day28_targets_for("165", "62")
    assert c_band["base"] == "28天减重4-6斤"
    assert c_band["challenge"] == "当前不开放高承诺目标"

    # 170cm/95kg → D带，cap 撞 4.0kg 硬顶 = 8 斤。
    d_band = _day28_targets_for("170", "95")
    assert d_band["base"] == "28天减重6-8斤"
    assert d_band["challenge"] == "当前不开放高承诺目标"

    # 4kg 硬顶保留：120kg 的用户 5% 是 6kg，仍被压到 4kg，结果与 95kg 相同。
    assert _day28_targets_for("175", "120") == d_band

    # 带位不决定28天斤数（A带除外）：同一体重、同一展示上限的 B/C 带拿到同一目标。
    assert _day28_targets_for("168", "60") == _day28_targets_for("165", "60")

    # 未满足筛选条件时不展示高承诺数字，标准档直接覆盖原基础档到常规上限。
    assert all(
        _day28_targets_for(height, weight)["challenge"] == "当前不开放高承诺目标"
        for height, weight in (("165", "57"), ("165", "62"), ("165", "70"), ("170", "85"))
    )


def test_day28_targets_never_exceed_the_cap():
    """封顶是硬约束：扫一批体重，任何出现的斤数都不得超过 cap 换算的斤数。"""

    for height in ("155", "165", "175", "185"):
        for weight in ("50", "55", "60", "65", "70", "80", "95", "110"):
            facts = build_intake_fact_summary(_band_fields(), {"h": height, "w": weight})
            cap_text = facts["day28_goal_cap_kg"]
            targets = facts["day28_targets"]
            numbers = [
                int(match)
                for text in targets.values()
                for match in re.findall(r"(\d+)\s*斤", text)
            ]
            if cap_text == "暂无数据":
                assert not numbers, f"{height}/{weight} cap 暂无数据却给了斤数 {targets}"
                continue
            cap_jin = float(cap_text) * 2
            for number in numbers:
                assert number <= cap_jin, (
                    f"{height}cm/{weight}kg cap={cap_text}kg({cap_jin}斤) 却出现 {number} 斤：{targets}"
                )


def test_day28_targets_for_band_a_and_unknown_band_carry_no_numbers():
    a_band = _day28_targets_for("175", "63")  # room 1.75kg → A带
    assert a_band["base"] == "不设置必须下降的体重硬指标，优先改善体态、睡眠、精力、恢复和执行一致性"
    assert a_band["challenge"] == "当前不开放高承诺减重目标，优先修复塑形"
    assert not re.search(r"\d+\s*斤", a_band["base"] + a_band["challenge"])

    # 空间归零的 A 带（cap=0）同样一个数字都不给
    exhausted = _day28_targets_for("165", "54")
    assert not re.search(r"\d+\s*斤", exhausted["base"] + exhausted["challenge"])

    unknown = build_intake_fact_summary(
        [{"key": "h", "title": "您的身高（cm/厘米）"}], {"h": "暂未测量"}
    )["day28_targets"]
    assert unknown["base"] == "待补齐身高体重后与班主任确认目标"
    assert not re.search(r"\d+\s*斤", unknown["base"] + unknown["challenge"])


def test_high_commitment_target_requires_deterministic_eligibility():
    fields = [
        {"key": "h", "title": "您的身高（cm/厘米）"},
        {"key": "w", "title": "您当前体重（kg/公斤）"},
        {"key": "goal", "title": "您最希望 28 天后看到哪些变化？"},
    ]
    facts = build_intake_fact_summary(
        fields,
        {
            "h": "165",
            "w": "90",
            "goal": ["身体更轻盈、腰腹部维度缩减"],
        },
    )
    assert facts["high_commitment_candidate"] is True
    assert facts["day28_targets"]["base"] == "28天减重6-8斤"
    assert facts["day28_targets"]["challenge"] == "28天向9-10斤推进"


def test_intake_prompt_delegates_day28_numbers_to_the_facts():
    """提示词只剩照抄，不再自带带位默认区间——否则和 day28_targets 形成两个数字来源。"""

    prompt = _intake_generation_system_prompt()
    assert "B/C/D 带的 base_goal、challenge_goal 必须逐字复制 intake_facts.day28_targets" in prompt
    assert "week5_8、week9_12 必须逐字复制 intake_facts.stage_targets" in prompt
    assert "不能把个案描述拼进权威目标值" in prompt
    assert "auto_cap=" not in prompt
    assert "2.5kg ≤ safe_reduction_room_kg" not in prompt
    assert "默认28天基础目标" not in prompt


def test_stage_targets_for_band_a_and_unknown_band_carry_no_numbers():
    a_band = _stage_targets_for("175", "63")  # room 1.75kg → A带
    assert a_band["week5_8"] == "不设减重指标，以体态与力量提升为主"
    assert "减重指标" in a_band["week9_12"] and "转入B带节奏" in a_band["week9_12"]
    assert not re.search(r"\d+\s*斤", a_band["week5_8"] + a_band["week9_12"])

    unknown = build_intake_fact_summary(
        [{"key": "h", "title": "您的身高（cm/厘米）"}], {"h": "暂未测量"}
    )["stage_targets"]
    assert unknown["week5_8"] == "待补齐身高体重后确定"


def test_protected_lower_uplift_moves_user_down_a_band():
    """165cm/70kg：无因子时空间 15.55kg 判D带；两类风险抬高下限后落到 C 带。"""

    answers = {"h": "165", "w": "70"}
    plain = build_intake_fact_summary(_band_fields(), answers)
    assert plain["protected_lower_weight_kg"] == "54.45"
    assert plain["safe_reduction_room_kg"] == "15.55"
    assert plain["weight_loss_band"] == "D带｜系统减重"
    assert plain["protected_lower_uplift_bmi"] == "0"

    adjusted = build_intake_fact_summary(
        _band_fields(),
        answers,
        {"muscle_insufficiency": True, "disease_or_medication_limit": True},
    )
    # BMI 20 → 21，165cm 上是 54.45kg → 57.17kg，空间收窄 2.72kg。
    assert adjusted["protected_lower_uplift_bmi"] == "1"
    assert adjusted["protected_lower_weight_kg"] == "57.17"
    assert adjusted["safe_reduction_room_kg"] == "12.83"
    assert adjusted["protected_lower_uplift_reasons"] == [
        "可靠体脂/肌肉数据显示肌肉量明显不足",
        "存在重大疾病或长期用药限制",
    ]
    # 自动复核线是未上调的 BMI 20 线，不能跟着抬——它是文档要求单独输出的字段。
    assert adjusted["automatic_review_weight_kg"] == "54.45"


def test_protected_lower_uplift_is_capped():
    """三类风险全中也只抬 1.5 个 BMI，避免算术副作用把人推进 A 带。"""

    facts = build_intake_fact_summary(
        _band_fields(),
        {"h": "165", "w": "70"},
        {
            "muscle_insufficiency": True,
            "disease_or_medication_limit": True,
            "recovery_risk": True,
        },
    )

    assert facts["protected_lower_uplift_bmi"] == "1.5"
    assert facts["protected_lower_weight_kg"] == "58.53"


def test_doctor_specified_floor_overrides_uplift_when_more_conservative():
    facts = build_intake_fact_summary(
        _band_fields(),
        {"h": "165", "w": "70"},
        {"muscle_insufficiency": True, "doctor_specified_lower_weight_kg": 62},
    )

    assert facts["protected_lower_weight_kg"] == "62"
    assert facts["safe_reduction_room_kg"] == "8"
    assert facts["weight_loss_band"] == "C带｜明确减脂"
    assert "医生明确的体重下限" in facts["protected_lower_uplift_reasons"]


def test_doctor_specified_floor_ignored_when_looser_or_invalid():
    """医生给的线比档位结果宽松，或根本解析不出来时，取更保守的档位结果。"""

    for doctor_value in (50, "无", None, -3, True):
        facts = build_intake_fact_summary(
            _band_fields(),
            {"h": "165", "w": "70"},
            {
                "muscle_insufficiency": True,
                "doctor_specified_lower_weight_kg": doctor_value,
            },
        )
        assert facts["protected_lower_weight_kg"] == "55.81"


def test_safety_review_does_not_change_the_band():
    """安全条件只强制复核，不改带位：空间 15.55kg 仍是 D 带。"""

    facts = build_intake_fact_summary(
        _band_fields(),
        {"h": "165", "w": "70"},
        {
            "safety_review_required": True,
            "safety_review_reasons": ["问卷填写当前处于哺乳期"],
        },
    )

    assert facts["weight_loss_band"] == "D带｜系统减重"
    assert facts["safety_review_required"] is True
    assert facts["safe_reduction_room_kg"] == "15.55"
    assert facts["safety_review_reasons"] == ["问卷填写当前处于哺乳期"]


def test_safety_review_forces_manual_review_marker():
    """带位照常给，但文档会被扣住不自动发送，原因写给运营看。"""

    facts = build_intake_fact_summary(
        _band_fields(),
        {"h": "165", "w": "70"},
        {
            "safety_review_required": True,
            "safety_review_reasons": ["问卷填写当前处于哺乳期"],
        },
    )
    shared: dict[str, object] = {}
    _apply_deterministic_band(shared, facts)

    assert shared["weight_band"] == "D带｜系统减重"
    assert shared["manual_review_required"] is True
    assert shared["safety_status"] == "manual_review"
    assert "问卷填写当前处于哺乳期" in shared["manual_review_reasons"]
    # 分带依据只讲可管理空间，安全条件不参与带位。
    assert "可管理空间" in shared["band_basis"][0]


def test_safety_extraction_persists_only_evidence_backed_continuing_risks():
    factors = _normalize_safety_factors(
        {
            "persistent_risk_flags": [
                {
                    "code": "chronic_disease",
                    "reason": "明确填写甲减",
                    "evidence": "既往病史：甲减",
                },
                {
                    "code": "long_term_medication",
                    "reason": "明确填写长期服用处方药",
                    "evidence": "正在用药：左甲状腺素",
                },
                {
                    "code": "other_risk",
                    "reason": "普通饮食限制不能成为持续高风险",
                    "evidence": "不吃辣",
                },
                {
                    "code": "pregnancy",
                    "reason": "没有证据不能持久标记",
                    "evidence": "",
                },
            ]
        }
    )
    case = ServiceCase(case_no="TEST-INTAKE-RISK", group_id="g", questionnaire_token="t")

    _apply_persistent_intake_risk_flags(case, factors)
    _apply_persistent_intake_risk_flags(case, factors)

    assert case.risk_level == "high"
    assert case.risk_flags == [
        {
            "code": "chronic_disease",
            "level": "high",
            "reason": "明确填写甲减",
            "evidence": "既往病史：甲减",
            "source": "questionnaire",
        },
        {
            "code": "long_term_medication",
            "level": "high",
            "reason": "明确填写长期服用处方药",
            "evidence": "正在用药：左甲状腺素",
            "source": "questionnaire",
        },
    ]


def _a_band_goal_response(goal_type: object, **extra: object) -> dict[str, object]:
    payload: dict[str, object] = {"user_goal": "希望睡眠更好、白天更有精神"}
    if goal_type is not None:
        payload["goal_type"] = goal_type
    payload.update(extra)
    return payload


def test_a_band_specific_goal_restates_intent_without_the_number():
    """用户写了斤数：复述诉求但去掉数字，避免数字变成留在报告里的锚点。"""

    core_conclusions: dict[str, object] = {}
    goal_response = _a_band_goal_response("specific", user_goal="希望28天减10斤")

    _apply_a_band_user_visible_contract(
        {"weight_band": "A带｜修复塑形"}, core_conclusions, goal_response
    )

    assert goal_response["user_goal"] == "希望尽快看到体重和体态变化"
    assert "10斤" not in goal_response["content"]
    assert "希望尽快看到体重和体态变化" in goal_response["content"]
    assert goal_response["judgement_type"] == "needs_adjustment"


def test_a_band_general_goal_does_not_invent_a_weight_target():
    """用户只勾了睡眠和精力：不能替他复述一个没提过的减重诉求。"""

    core_conclusions: dict[str, object] = {}
    goal_response = _a_band_goal_response("general")

    _apply_a_band_user_visible_contract(
        {"weight_band": "A带｜修复塑形"}, core_conclusions, goal_response
    )

    assert "希望尽快看到体重" not in goal_response["content"]
    assert "您希望" not in goal_response["content"]
    # 用户真实期待由模型写，代码不覆盖——它看得到问卷，知道要的是睡眠还是精力。
    assert goal_response["user_goal"] == "希望睡眠更好、白天更有精神"
    assert "不设置体重下降指标" in goal_response["content"]
    assert goal_response["judgement_type"] == "needs_adjustment"


def test_a_band_missing_goal_type_falls_back_to_general():
    """判不出类型时宁可少复述一句，也不要编造用户没说过的诉求。"""

    for goal_type in (None, "", "unknown", 123):
        goal_response = _a_band_goal_response(goal_type)
        _apply_a_band_user_visible_contract(
            {"weight_band": "A带｜修复塑形"}, {}, goal_response
        )
        assert "希望尽快看到体重" not in goal_response["content"]
        assert goal_response["goal_type"] == "general"


def test_a_band_weight_number_in_user_goal_forces_the_desensitised_copy():
    """模型漏填 goal_type 却把斤数写进 user_goal：仍走脱敏改写，数字不能漏进报告。"""

    for raw_goal in ("28天减重10斤", "目标体重 55kg", "想减 5 公斤", "希望减3千克"):
        goal_response = _a_band_goal_response(None, user_goal=raw_goal)
        _apply_a_band_user_visible_contract(
            {"weight_band": "A带｜修复塑形"}, {}, goal_response
        )
        assert goal_response["user_goal"] == "希望尽快看到体重和体态变化"

    # 不带重量单位的数字不该误触发脱敏，否则泛化期待用户又被扣回编造话术。
    goal_response = _a_band_goal_response("general", user_goal="希望每天7000步、睡够7小时")
    _apply_a_band_user_visible_contract(
        {"weight_band": "A带｜修复塑形"}, {}, goal_response
    )
    assert goal_response["user_goal"] == "希望每天7000步、睡够7小时"


def test_non_a_band_goal_response_is_untouched():
    core_conclusions: dict[str, object] = {}
    goal_response = _a_band_goal_response("specific", content="C带原文", user_goal="希望减10斤")

    _apply_a_band_user_visible_contract(
        {"weight_band": "C带｜明确减脂"}, core_conclusions, goal_response
    )

    assert goal_response["content"] == "C带原文"
    assert goal_response["user_goal"] == "希望减10斤"
    assert core_conclusions == {}


class _StubAI:
    def __init__(self, text: str | Exception):
        self._text = text
        self.scenarios: list[str] = []

    async def generate_text(self, *, scenario, **kwargs):
        self.scenarios.append(scenario)
        if isinstance(self._text, Exception):
            raise self._text
        return self._text


@pytest.mark.asyncio
async def test_extract_safety_factors_parses_model_output():
    ai = _StubAI(
        json.dumps(
            {
                "safety_review_required": True,
                "safety_review_reasons": ["问卷填写当前处于哺乳期"],
                "muscle_insufficiency": False,
                "disease_or_medication_limit": True,
                "recovery_risk": False,
                "doctor_specified_lower_weight_kg": "58",
                "persistent_risk_flags": [
                    {
                        "code": "lactation",
                        "reason": "明确填写当前处于哺乳期",
                        "evidence": "当前处于哺乳期",
                    }
                ],
                "evidence_notes": ["健康情况填写：哺乳期，医生建议不低于58kg"],
            },
            ensure_ascii=False,
        )
    )

    factors = await _extract_safety_factors(
        ai=ai, service_case=SimpleNamespace(id=1, risk_level=None), readable_answers="- 健康情况: 哺乳期"
    )

    assert ai.scenarios == ["intake_safety_factors"]
    assert factors["safety_review_required"] is True
    assert factors["safety_review_reasons"] == ["问卷填写当前处于哺乳期"]
    assert factors["disease_or_medication_limit"] is True
    assert factors["doctor_specified_lower_weight_kg"] == 58.0
    assert factors["persistent_risk_flags"] == [
        {
            "code": "lactation",
            "level": "high",
            "reason": "明确填写当前处于哺乳期",
            "evidence": "当前处于哺乳期",
            "source": "questionnaire",
        }
    ]


@pytest.mark.asyncio
async def test_extract_safety_factors_does_not_block_intake_on_failure():
    """抽取挂掉不能挡住交付：退回不上调、不标复核，等同历史行为。"""

    ai = _StubAI(RuntimeError("model timeout"))

    factors = await _extract_safety_factors(
        ai=ai, service_case=SimpleNamespace(id=1, risk_level=None), readable_answers="- 健康情况: 无"
    )

    assert factors["safety_review_required"] is False
    assert factors["doctor_specified_lower_weight_kg"] is None


def test_safety_factors_normalize_to_no_adjustment_on_garbage():
    """抽取失败或字段乱填时退回不上调、不改带，而不是靠猜把人推进A带。"""

    for payload in (None, {}, "not-json", {"safety_review_required": "maybe"}):
        factors = _normalize_safety_factors(payload)
        assert factors["safety_review_required"] is False
        assert factors["muscle_insufficiency"] is False
        assert factors["doctor_specified_lower_weight_kg"] is None

    facts = build_intake_fact_summary(
        _band_fields(), {"h": "165", "w": "70"}, _normalize_safety_factors(None)
    )
    assert facts["weight_loss_band"] == "D带｜系统减重"
    assert facts["protected_lower_uplift_bmi"] == "0"


def test_unresolved_band_sentinel_never_reaches_user_documents():
    """算不出带位时后台留哨兵值（dashboard 靠它统计），用户文档只看到人话。"""

    payloads = {
        DocumentType.ENERGY_RESTART_PRESCRIPTION.value: {
            "success_path": {"weight_band": "LLM自写带"},
        },
        DocumentType.DAY28_EXECUTION_STANDARD.value: {"weight_band": "LLM自写带"},
    }
    facts = {"bmi": "暂无数据", "weight_loss_band": UNRESOLVED_BAND}

    _apply_intake_facts_to_document_payloads(payloads, facts)

    energy = payloads[DocumentType.ENERGY_RESTART_PRESCRIPTION.value]
    execution = payloads[DocumentType.DAY28_EXECUTION_STANDARD.value]
    assert UNRESOLVED_BAND not in energy["success_path"]["weight_band"]
    assert UNRESOLVED_BAND not in execution["weight_band"]
    assert energy["success_path"]["weight_band"] == UNRESOLVED_BAND_USER_TEXT
    # 后台事实不动：dashboard 分类和人群画像仍按哨兵值过滤未采集用户。
    assert facts["weight_loss_band"] == UNRESOLVED_BAND


def test_unresolved_band_sentinel_not_synced_into_shared_path():
    payloads = {
        DocumentType.ENERGY_RESTART_PRESCRIPTION.value: {
            "one_sentence": "当前以补齐基础数据为先，先把执行节律稳住，再谈体重目标的具体安排。",
            "current_state": "身高体重尚未补齐，先按稳妥节律执行，并观察睡眠、精力和排便的反馈变化。",
            "priority_direction": "先抓节律稳定，把三餐和睡前时间固定下来。",
        },
        DocumentType.DAY28_EXECUTION_STANDARD.value: {
            "day28_goal": "先补齐身高体重，并稳住三餐结构与睡眠节律。",
            "current_phase_goal": "先解决节律稳定，不是先追求掉秤。",
            "current_mainline": "节律稳定",
        },
    }
    shared = {"weight_band": UNRESOLVED_BAND, "current_mainline": "节律稳定"}

    normalized = _normalize_combined_document_payload(payloads, "测试用户", shared_analysis=shared)

    energy = normalized[DocumentType.ENERGY_RESTART_PRESCRIPTION.value]
    assert energy["success_path"]["weight_band"] == UNRESOLVED_BAND_USER_TEXT
    assert energy["core_conclusions"]["management_band"] == UNRESOLVED_BAND_USER_TEXT
    # shared_analysis 是后台视图，哨兵值必须原样留下。
    assert shared["weight_band"] == UNRESOLVED_BAND


def test_provisional_band_not_forced_by_single_self_reported_weight():
    """单次自报体重是全员常态，不能让「暂定带位」信号全亮而失去筛选意义。"""

    fields = [
        {"key": "h", "title": "您的身高（cm/厘米）"},
        {"key": "w", "title": "您当前体重（kg/公斤）"},
    ]
    facts = build_intake_fact_summary(fields, {"h": "175", "w": "65"})
    shared: dict[str, object] = {}
    _apply_deterministic_band(shared, facts)

    # 测量方式仍如实记录，但它不再冒充「这个带位需要人工确认一眼」。
    assert facts["baseline_weight_provisional"] is True
    assert shared["weight_band"] == "B带｜轻盈启动"
    assert "provisional_band" not in shared
    assert "manual_review_required" not in shared


def test_provisional_band_still_set_when_band_cannot_be_computed():
    fields = [{"key": "h", "title": "您的身高（cm/厘米）"}]
    facts = build_intake_fact_summary(fields, {"h": "暂未测量"})
    shared: dict[str, object] = {}
    _apply_deterministic_band(shared, facts)

    assert shared["provisional_band"] is True
    assert shared["manual_review_required"] is True
    assert shared["safety_status"] == "manual_review"


def test_intake_facts_normalize_height_written_in_meters():
    """身高填「1.75」时不再把 BMI 算成 20 万——否则会被覆写到交付文件每一处 BMI。"""

    fields = [
        {"key": "h", "title": "您的身高（cm/厘米）"},
        {"key": "w", "title": "您当前体重（kg/公斤）"},
    ]
    facts = build_intake_fact_summary(fields, {"h": "1.75", "w": "65"})

    assert facts["height_cm"] == "175"
    assert facts["bmi"] == "21.2"
    assert facts["weight_loss_band"] == "B带｜轻盈启动"


def test_internal_jargon_is_reworded_not_deleted():
    """真实模型会把「保护性下限」写进正文（提示词禁了也照写）。

    这两句承载核心结论，删整句会留残文，所以按词换成用户话；
    带「人工复核」的整句仍然丢弃——那是纯内部状态，用户不该看到。
    """

    assert _scrub_internal_text(
        "当前体重54公斤已经低于您身体的保护性下限，继续减重容易反弹。"
    ) == "当前体重54公斤已经低于您身体的安全下限，继续减重容易反弹。"
    # 模型爱写「体重保护性下限」，替换词不能再带「体重」二字
    assert _scrub_internal_text("由于甲状腺切除和体重保护性下限，早C从半份开始。") == (
        "由于甲状腺切除和体重安全下限，早C从半份开始。"
    )
    assert _scrub_internal_text("当前体重已低于保护性下限，需人工复核。") == ""


def test_adjust_stop_conditions_keeps_medical_escalation():
    """一页预算可以砍长度，但「及时就医」是最重的那句，不能被静默砍掉。"""

    written = (
        "心慌或睡眠明显变差时早C/午B减半并前移；"
        "持续胃部不适或明显心悸时暂停产品并联系班主任；"
        "出现胸痛、呼吸困难、黑便、便血或持续呕吐请立即停止执行并及时就医。"
    )

    rendered = _adjust_stop_conditions_text(written)

    assert "就医" in rendered
    assert "联系班主任" in rendered


def test_adjust_stop_conditions_falls_back_when_model_omits_medical_line():
    rendered = _adjust_stop_conditions_text("心慌时早C减半；睡眠变差时午B前移")

    assert "就医" in rendered


def test_delivery_filename_uses_customer_facing_document_title():
    diagnosis = _document_filename(
        user_name="小燃",
        document_type=DocumentType.ENERGY_RESTART_PRESCRIPTION,
        title="个人能量重启诊断报告",
        gender="女",
    )
    execution = _document_filename(
        user_name="小燃",
        document_type=DocumentType.DAY28_EXECUTION_STANDARD,
        title="28天高燃人生执行建议书",
        gender="女",
    )

    assert diagnosis == "小燃女士_个人能量重启诊断报告.docx"
    assert execution == "小燃女士_28天高燃人生执行建议书.docx"


def test_document_payload_bmi_is_overwritten_by_intake_facts():
    payloads = {
        DocumentType.ENERGY_RESTART_PRESCRIPTION.value: {
            "overview": {
                "bmi": "BMI 23.7（维持优化带）",
                "height_weight": "170cm / 68kg",
            },
            "judgement_points": [
                "BMI 23.7 → 当前已在健康体重范围",
                "白班作息 → 节律可继续优化",
            ],
            "current_state": "30岁男性，BMI 23.7，身高175cm，体重65kg。",
            "success_path": {"weight_band": "LLM自写带"},
        },
        DocumentType.DAY28_EXECUTION_STANDARD.value: {
            "weight_band": "LLM自写带",
            "day28_goal": "BMI 23.7 下不以减重为核心。",
        },
    }
    facts = {
        "bmi": "21.2",
        "weight_loss_band": "维持优化带（A带）",
        "height_cm": "175",
        "weight_kg": "65",
        "waist": "未提供",
        "bodyfat": "未提供",
    }

    _apply_intake_facts_to_document_payloads(payloads, facts)

    energy = payloads[DocumentType.ENERGY_RESTART_PRESCRIPTION.value]
    execution = payloads[DocumentType.DAY28_EXECUTION_STANDARD.value]
    assert energy["overview"]["bmi"] == "21.2"
    # 用户可见体重统一展示为「斤」（65kg × 2 = 130斤）；内部计算与结构化字段仍用 kg。
    assert energy["overview"]["height_weight"] == "175cm / 130斤"
    assert "BMI 21.2" in energy["judgement_points"][0]
    assert "BMI 21.2" in energy["current_state"]
    assert energy["success_path"]["weight_band"] == "维持优化带（A带）"
    assert execution["weight_band"] == "维持优化带（A带）"
    assert "BMI 21.2" in execution["day28_goal"]


@pytest.mark.asyncio
async def test_failed_generation_reverts_status(
    admin_client,
    create_case,
    db_session,
    mock_ai,
    cai_haoran_answers,
    wait_background,
):
    """模拟 AI 返回空 JSON → 后台生成任务失败 → 验证 status 回退并记录错误"""
    mock_ai._responses = {
        INTAKE_SYSTEM_PROMPT_PREFIX: "",
    }

    group_id = f"test-group-fail-{secrets.token_hex(6)}"
    resp = await create_case({
        "group_id": group_id,
        "user": {"name": "Test Fail User"},
    })
    assert resp.status_code == 201, resp.text
    sc = resp.json()
    token = sc["questionnaire_token"]
    case_id = sc["id"]

    submit_resp = await admin_client.post(
        f"/questionnaires/{token}/submit?template=intake",
        json={"answers": cai_haoran_answers},
    )
    assert submit_resp.status_code == 201, submit_resp.text
    await wait_background()

    updated_case = await db_session.get(ServiceCase, case_id)
    assert updated_case is not None
    assert updated_case.status == ServiceCaseStatus.QUESTIONNAIRE_SUBMITTED, (
        f"Expected status 'questionnaire_submitted', got '{updated_case.status.value}'"
    )

    notes = updated_case.notes or ""
    assert "失败" in notes or "Error" in notes or "error" in notes.lower(), (
        f"Expected error in notes, got: {notes[:200]}"
    )
    intake_calls = [call for call in mock_ai.calls if call.get("scenario") == "intake_doc"]
    assert len(intake_calls) == 3
    assert "【重新生成要求】" not in str(intake_calls[0]["user_prompt"])
    assert "【重新生成要求】" in str(intake_calls[1]["user_prompt"])


@pytest.mark.asyncio
async def test_incomplete_intake_package_retries_and_recovers(
    admin_client,
    create_case,
    db_session,
    mock_ai,
    cai_haoran_answers,
    wait_background,
):
    """第一次缺两份文件时自动重新生成；第二次完整就正常落画像和正式文档。"""

    mock_ai._responses = {
        INTAKE_SYSTEM_PROMPT_PREFIX: [
            json.dumps(
                {
                    "intake_analysis_package": {
                        "shared_analysis": {},
                        "profile": {"summary": "只有画像，没有交付文件"},
                        "documents": {},
                    }
                },
                ensure_ascii=False,
            ),
            _make_intake_mock_response(),
        ],
    }

    group_id = f"test-intake-content-retry-{secrets.token_hex(6)}"
    resp = await create_case(
        {
            "group_id": group_id,
            "user": {"name": "内容重试用户"},
        }
    )
    assert resp.status_code == 201, resp.text
    case = resp.json()

    submit_resp = await admin_client.post(
        f"/questionnaires/{case['questionnaire_token']}/submit?template=intake",
        json={"answers": cai_haoran_answers},
    )
    assert submit_resp.status_code == 201, submit_resp.text
    await wait_background()

    profile = await db_session.scalar(
        select(UserProfile).where(UserProfile.service_case_id == case["id"])
    )
    documents = list(
        await db_session.scalars(
            select(UserDocument).where(UserDocument.service_case_id == case["id"])
        )
    )
    assert profile is not None
    assert len(documents) == 2

    intake_calls = [call for call in mock_ai.calls if call.get("scenario") == "intake_doc"]
    assert len(intake_calls) == 2
    assert "【重新生成要求】" not in str(intake_calls[0]["user_prompt"])
    assert "must contain both delivery documents" in str(intake_calls[1]["user_prompt"])


# ---------------------------------------------------------------------------
# 管理带 vs 人工复核、五项打卡结构、执行建议书内容预算
# ---------------------------------------------------------------------------


def test_daily_checkin_rejects_heading_pollution_from_legacy_string_output():
    """真实模型曾把「每日固定打卡/5项打卡」当成第一题，真正的第5项被截掉。

    源头结构校验必须把栏目标题判为无效并保留5个真问题，而不是直接截前5条。
    """

    inline_heading = (
        "1. 每日固定打卡：1. 个案产品排班是否按方案完成 2. 蔬菜总量是否至少2拳 "
        "3. 是否完成计划内快走 4. 睡眠是否按计划收口 5. 饭后困评分（1分很困、5分不困）"
    )
    numbered_heading = (
        "1. 5项打卡：\n"
        "2. 个案产品排班是否按方案完成\n"
        "3. 蔬菜总量是否至少2拳\n"
        "4. 是否完成计划内快走\n"
        "5. 睡眠是否按计划收口\n"
        "6. 饭后困评分（1分很困、5分不困）"
    )
    expected_titles = [
        "个案产品排班是否按方案完成",
        "蔬菜总量是否至少2拳",
        "是否完成计划内快走",
        "睡眠是否按计划收口",
        "饭后困评分（1分很困、5分不困）",
    ]

    for raw in (inline_heading, numbered_heading):
        items = normalize_daily_checkin_items(raw, facts={"weight_kg": "68"})
        assert [item["title"] for item in items] == expected_titles
        assert [item["order"] for item in items] == [1, 2, 3, 4, 5]
        assert not any("每日固定打卡" in item["title"] for item in items)
        assert not any("5项打卡" in item["title"] for item in items)


def test_daily_checkin_structure_is_strictly_five_ordered_items():
    """结构化数组：正好5项、order 1-5 连续唯一、复合问题与相对时间词被丢弃。"""

    items = normalize_daily_checkin_items(
        [
            {"order": 1, "category": "product", "title": "每日固定打卡", "answer_type": "text", "options": []},
            {"order": 2, "category": "product", "title": "早C与午B是否按半份方案完成",
             "answer_type": "yes_no", "options": ["是", "否"]},
            # 复合问题：同时问时间和饮食结构
            {"order": 3, "category": "diet", "title": "晚餐是否在20点前完成并且主食≤1拳",
             "answer_type": "yes_no", "options": ["是", "否"]},
            {"order": 4, "category": "activity", "title": "是否完成30分钟快走或7000步",
             "answer_type": "yes_no", "options": ["是", "否"]},
            # 相对时间词：题面里不允许带「今晚」
            {"order": 5, "category": "sleep", "title": "今晚是否23:30前闭眼",
             "answer_type": "yes_no", "options": ["是", "否"]},
            {"order": 6, "category": "state", "title": "情绪稳定度评分（1分很差、5分很好）",
             "answer_type": "score_1_5", "options": ["1", "2", "3", "4", "5"]},
            {"order": 7, "category": "diet", "title": "蔬菜总量是否至少2拳",
             "answer_type": "yes_no", "options": ["是", "否"]},
            {"order": 8, "category": "state", "title": "是否出现心慌",
             "answer_type": "yes_no", "options": ["是", "否"]},
        ],
        facts={"weight_kg": "78"},
    )

    assert len(items) == 5
    assert [item["order"] for item in items] == [1, 2, 3, 4, 5]
    titles = [item["title"] for item in items]
    assert "每日固定打卡" not in titles
    assert "晚餐是否在20点前完成并且主食≤1拳" not in titles
    assert "今晚是否23:30前闭眼" not in titles
    # 被丢弃的名额由后续合格项补上，不是截前5条
    assert "是否出现心慌" in titles
    for item in items:
        assert item["answer_type"] in {"yes_no", "score_1_5", "number", "text"}
        if item["answer_type"] == "yes_no":
            assert item["options"] == ["是", "否"]
        if item["answer_type"] == "score_1_5":
            assert item["options"] == ["1", "2", "3", "4", "5"]


def test_daily_checkin_falls_back_deterministically_when_all_items_invalid():
    """全部不合格时走确定性兜底，仍然是完整5项，而不是发半套清单。"""

    items = normalize_daily_checkin_items(
        ["每日固定打卡", "5项打卡", "打卡清单"],
        facts={"weight_kg": "70"},
    )

    assert len(items) == 5
    assert [item["order"] for item in items] == [1, 2, 3, 4, 5]
    assert all(item["title"] for item in items)
    assert not any("打卡清单" in item["title"] for item in items)
    # 兜底题也要满足答题类型与选项一致
    for item in items:
        assert item["options"] == checkin_options_for(item["answer_type"])


def test_daily_checkin_keeps_number_question_without_unit_bracket():
    """填数字的题面不带单位括号也必须保留，不能被换成通用兜底题。

    真实模型给过「夜间醒来次数」这道很贴合该用户（几乎每天夜醒）的睡眠题，
    旧规则要求 number 题面必须带括号，把它判废后顶上通用的「晨起体重」，
    个性化被静默吃掉。单位由提示词约束，代码不做事后审核。
    """

    items = normalize_daily_checkin_items(
        [
            {"order": 1, "category": "product", "title": "是否按个案方案完成早C、午B和晚A？", "answer_type": "yes_no"},
            {"order": 2, "category": "diet", "title": "蔬菜总量是否至少2拳？", "answer_type": "yes_no"},
            {"order": 3, "category": "diet", "title": "晚餐后是否未吃任何零食或加餐？", "answer_type": "yes_no"},
            {"order": 4, "category": "activity", "title": "是否完成计划内步数或力量训练？", "answer_type": "yes_no"},
            {"order": 5, "category": "sleep", "title": "夜间醒来次数", "answer_type": "number"},
        ],
        facts={"weight_kg": "62"},
    )

    assert [item["title"] for item in items][4] == "夜间醒来次数"
    assert items[4]["category"] == "sleep"
    assert items[4]["answer_type"] == "number"
    assert not any("晨起体重" in item["title"] for item in items)
    # 类型冲突仍要挡：题面在问是否，就不能声明成填数字
    conflicting = normalize_daily_checkin_items(
        [{"order": 1, "category": "sleep", "title": "是否在23:30前入睡？", "answer_type": "number"}],
        allow_fallback=False,
    )
    assert conflicting == []


def test_structured_text_checkins_accept_answerable_field_labels_only_with_context():
    """结构化模型项可用简短字段名，但历史字符串和栏目标题仍要拒绝。"""

    product_titles = (
        "早C、午B、晚A完成情况",
        "早C、午B、晚A的完成状态分别是什么",
        "早C、午B、晚A的完成情况",
        "早C、午B、晚A完成情况如何",
        "早C、午B、晚A的服用状态",
    )
    for title in product_titles:
        items = normalize_daily_checkin_items(
            [
                {
                    "order": 1,
                    "category": "product",
                    "title": title,
                    "answer_type": "text",
                    "example_answer": "早C已完成；午B未完成；晚A已完成",
                }
            ],
            allow_fallback=False,
        )
        assert len(items) == 1, title
        assert items[0]["title"] == title

    sleep_item = normalize_daily_checkin_items(
        [
            {
                "order": 1,
                "category": "sleep",
                "title": "实际入睡时间",
                "answer_type": "text",
                "example_answer": "23:45，关灯后很快睡着",
            }
        ],
        allow_fallback=False,
    )
    assert len(sleep_item) == 1

    # 没有结构化上下文的历史字符串，仍不能凭栏目名进入问卷。
    assert normalize_daily_checkin_items(["实际入睡时间"], allow_fallback=False) == []
    assert normalize_daily_checkin_items(["早C、午B、晚A完成情况"], allow_fallback=False) == []
    # 即使有结构化上下文，已知栏目标题、相对日期和复合题仍被拦截。
    for title in (
        "每日固定打卡",
        "今天早C、午B、晚A完成情况",
        "早C、午B、晚A完成情况，同时记录蔬菜",
    ):
        assert normalize_daily_checkin_items(
            [
                {
                    "order": 1,
                    "category": "product",
                    "title": title,
                    "answer_type": "text",
                    "example_answer": "早C已完成；午B未完成；晚A已完成",
                }
            ],
            allow_fallback=False,
        ) == [], title

    # text 字段没有实际示例答案时，不能靠结构化字段名绕过完整答案校验。
    assert normalize_daily_checkin_items(
        [
            {
                "order": 1,
                "category": "sleep",
                "title": "实际入睡时间",
                "answer_type": "text",
            }
        ],
        allow_fallback=False,
    ) == []


def test_daily_checkin_substitution_preserves_other_answers_and_leaves_fallback_empty():
    """只替换坏题时保留四道原答案，不为兜底题编造答案，继续触发重试。"""

    items = normalize_daily_checkin_items(
        [
            {
                "order": 1,
                "category": "product",
                "title": "早C、午B、晚A完成情况",
                "answer_type": "text",
                "example_answer": "早C已完成；午B未完成；晚A已完成",
            },
            {
                "order": 2,
                "category": "diet",
                "title": "蔬菜总量是否至少2拳",
                "answer_type": "yes_no",
                "example_answer": "是，完成2拳以上",
            },
            {
                "order": 3,
                "category": "activity",
                "title": "是否完成30分钟快走",
                "answer_type": "yes_no",
                "example_answer": "是，晚饭后快走30分钟",
            },
            {
                "order": 4,
                "category": "sleep",
                "title": "实际入睡时间",
                "answer_type": "text",
                "example_answer": "23:45，关灯后很快睡着",
            },
            {
                "order": 5,
                "category": "state",
                "title": "每日固定打卡",
                "answer_type": "score_1_5",
                "example_answer": "4分，整体较稳",
            },
        ],
        facts={"weight_kg": "70"},
    )

    assert len(items) == 5
    assert [item["example_answer"] for item in items[:4]] == [
        "早C已完成；午B未完成；晚A已完成",
        "是，完成2拳以上",
        "是，晚饭后快走30分钟",
        "23:45，关灯后很快睡着",
    ]
    fallback = items[4]
    assert fallback["title"] == "晨起体重（kg）"
    assert "example_answer" not in fallback
    errors = _generated_daily_checkin_errors(items)
    assert sum("缺少已填写答案" in error for error in errors) == 1


def test_delivery_instruction_consistency_separates_staple_and_vegetable_meal_scopes():
    daily_total = {
        "execution": {"diet_rules": ["主食每餐≤1拳，蔬菜全天总量≥2拳"]}
    }
    daily_total_with_list_separator = {
        "execution": {"diet_rules": ["主食每餐≤1拳、蔬菜全天总量≥2拳"]}
    }
    daily_total_with_conjunction = {
        "execution": {"weekly_standard": "主食每餐≤1拳且蔬菜总量≥2拳≥5天"}
    }
    per_meal_vegetables = {"execution": {"diet_rules": ["每餐蔬菜≥2拳"]}}

    assert "交付文件将蔬菜份量写成每餐口径" not in _delivery_instruction_consistency_errors(
        daily_total
    )
    assert "交付文件将蔬菜份量写成每餐口径" not in _delivery_instruction_consistency_errors(
        daily_total_with_list_separator
    )
    assert "交付文件将蔬菜份量写成每餐口径" not in _delivery_instruction_consistency_errors(
        daily_total_with_conjunction
    )
    assert "交付文件将蔬菜份量写成每餐口径" in _delivery_instruction_consistency_errors(
        per_meal_vegetables
    )


def test_english_daily_checkin_items_keep_their_answer_type():
    """英文题面必须按问法判类型，不能因为"以问号结尾"就一律当是/否题。

    英文的数字题几乎都写成 "How many ... did you ...?"：既含数量问法也含判断词。
    判错的后果不是显示瑕疵——题被判废后顶上来的是兜底题，个性化打卡被静默换掉。
    """

    items = normalize_daily_checkin_items(
        [
            {"order": 1, "category": "product", "title": "Did you complete Morning C, Noon B, and Evening A as planned?", "answer_type": "yes_no"},
            # "carbohydrates" 里含 "rate"：按子串匹配会判成评分题，再因判断+评分双意图整条丢掉
            {"order": 2, "category": "diet", "title": "Did you control dinner carbohydrates to at most 1 fist?", "answer_type": "yes_no"},
            {"order": 3, "category": "activity", "title": "How many minutes of brisk walking did you complete?", "answer_type": "number"},
            {"order": 4, "category": "sleep", "title": "Did you start sleep wind-down by 23:30?", "answer_type": "yes_no"},
            {"order": 5, "category": "state", "title": "How would you rate your afternoon energy level (1-5)?", "answer_type": "score_1_5"},
        ],
        facts={"weight_kg": "90.7"},
        language="en",
    )

    assert len(items) == 5
    assert [item["answer_type"] for item in items] == [
        "yes_no",
        "yes_no",
        "number",
        "yes_no",
        "score_1_5",
    ]
    # 没有一项被兜底题顶掉
    assert all("Did you" in str(item["title"]) or "How " in str(item["title"]) for item in items)
    # 判断题选项必须是英文
    assert items[0]["options"] == ["Yes", "No"]

    # 类型冲突仍要挡：题面问的是数量，就不能声明成是/否
    conflicting = normalize_daily_checkin_items(
        [{"order": 1, "category": "activity", "title": "How many minutes did you walk?", "answer_type": "yes_no"}],
        allow_fallback=False,
        language="en",
    )
    assert conflicting == []


def test_english_daily_checkin_fallback_items_are_english():
    """兜底题顶上来时也必须是英文，否则英文客户每天面对一张中文打卡表。"""

    items = normalize_daily_checkin_items(
        ["daily check-in list"],
        facts={"weight_kg": "90.7"},
        language="en",
    )

    assert len(items) == 5
    assert all(item["category"] in CHECKIN_CATEGORIES for item in items)
    for item in items:
        title = str(item["title"])
        assert not re.search(r"[一-鿿]", title), title
        assert item["options"] in ([], ["Yes", "No"]), item

    # 中文服务单不受影响
    zh_items = normalize_daily_checkin_items(["每日固定打卡"], facts={"weight_kg": "70"})
    assert any("体重" in str(item["title"]) for item in zh_items)


def test_daily_checkin_fallback_items_carry_their_category():
    """兜底题是代码写死的，类别必须直接标注，不能留空。"""

    items = normalize_daily_checkin_items(["每日固定打卡"], facts={"weight_kg": "70"})

    assert len(items) == 5
    assert all(item["category"] in CHECKIN_CATEGORIES for item in items), [
        (item["title"], item["category"]) for item in items
    ]


def test_daily_checkin_logs_every_substitution(caplog):
    """换题必须留痕：后台只看得到最终5项，看不出哪项不是模型挑的。"""

    with caplog.at_level(logging.WARNING, logger="app.services.intake_generation"):
        normalize_daily_checkin_items(["每日固定打卡", "打卡清单"], facts={"weight_kg": "70"})

    messages = [record.getMessage() for record in caplog.records]
    assert any("daily_checkin normalized with substitutions" in message for message in messages)
    assert any("每日固定打卡" in message for message in messages)
    assert any("fallback_used=" in message for message in messages)


def test_generated_daily_checkin_examples_match_answer_contract():
    items = normalize_daily_checkin_items(
        [
            {
                "order": 1,
                "category": "product",
                "title": "早C随早餐、午B随午餐、晚A最晚睡前1小时是否按方案完成？",
                "answer_type": "yes_no",
                "options": ["是", "否"],
                "example_answer": "早C已完成；午B未完成；晚A已完成",
            },
            {
                "order": 2,
                "category": "diet",
                "title": "蔬菜总量是否至少2拳？",
                "answer_type": "yes_no",
                "options": ["是", "否"],
                "example_answer": "是，已完成",
            },
            {
                "order": 3,
                "category": "activity",
                "title": "是否完成30分钟快走？",
                "answer_type": "yes_no",
                "options": ["是", "否"],
                "example_answer": "是，已完成",
            },
            {
                "order": 4,
                "category": "sleep",
                "title": "睡眠时长（小时）",
                "answer_type": "number",
                "options": [],
                "example_answer": "7小时",
            },
            {
                "order": 5,
                "category": "state",
                "title": "饭后困评分（1分很困、5分不困）",
                "answer_type": "score_1_5",
                "options": ["1", "2", "3", "4", "5"],
                "example_answer": "4分，饭后精神尚可",
            },
        ],
        allow_fallback=False,
    )

    assert _generated_daily_checkin_errors(items) == []
    items[0]["example_answer"] = "早C已完成；午B已完成；晚A已完成"
    assert any("至少要有一项未完成" in error for error in _generated_daily_checkin_errors(items))


def test_product_checkin_statuses_are_explicit_and_limited_to_completed_states():
    from app.services.intake_generation import _product_checkin_statuses

    assert _product_checkin_statuses("早C已完成；午B未完成；晚A已完成") == (
        "已完成",
        "未完成",
        "已完成",
    )
    assert _product_checkin_statuses("早C、午B、晚A") is None
    assert _product_checkin_statuses("早C已完成；午B未完成；晚A按方案暂停") is None
    assert _product_checkin_statuses("早C已完成；午B已完成；晚A已完成") == (
        "已完成",
        "已完成",
        "已完成",
    )


def test_generated_daily_checkin_examples_reject_empty_or_mismatched_answers():
    items = normalize_daily_checkin_items(
        [
            {"order": 1, "category": "product", "title": "早C、午B、晚A是否完成？", "answer_type": "yes_no", "example_answer": "是，已完成"},
            {"order": 2, "category": "diet", "title": "蔬菜是否完成？", "answer_type": "yes_no", "example_answer": "请填写"},
            {"order": 3, "category": "activity", "title": "是否完成7000步？", "answer_type": "yes_no", "example_answer": "完成了"},
            {"order": 4, "category": "sleep", "title": "睡眠时长（小时）", "answer_type": "number", "example_answer": "7"},
            {"order": 5, "category": "state", "title": "状态评分（1-5）", "answer_type": "score_1_5", "example_answer": "4"},
        ],
        allow_fallback=False,
    )

    errors = _generated_daily_checkin_errors(items)
    assert any("早C、午B、晚A" in error for error in errors)
    assert any("填写提示" in error for error in errors)
    assert any("明确是或否" in error for error in errors)
    assert any("单位" in error for error in errors)
    assert any("1-5分" in error for error in errors)


def test_execution_document_keeps_single_page_content_budget(caplog):
    """一页执行纸的内容预算：目标摘要5行、产品排班3行、饮食≤3条、运动≤2条。

    条数上限（3条/2条）仍是硬约束；单条字数只是软约束——超长只告警不截断，
    因为截断会回退到最近标点、丢掉最后一整个分句（运动规则丢的可能是禁忌时间窗口）。
    """

    content_json = build_document_content_json(
        document_type=DocumentType.DAY28_EXECUTION_STANDARD,
        title="28天高燃人生执行建议书",
        user_name="预算用户",
        display_name="预算用户",
        fields={
            "weight_band": "C带｜明确减脂",
            "current_mainline": "保肌减脂与节律稳定并重，同时兼顾睡眠修复",
            "day28_goal": "先把晚间收口和午餐结构稳住，观察腰腹围度、下午精力和睡眠连续性的变化趋势，并记录每周体感",
            "body_trend": "缓慢变沉",
            "base_goal": "28天减重4-5斤",
                "challenge_goal": "向6斤推进",
                "commitment_rule": "标准档60%；高承诺档85%；群内确认后生效",
            "path_goal": "12周形成稳定节律，围度稳步收紧",
            "product_schedule": {
                "morning_c": "早C半份随早餐7:30，观察三天无心慌后再上满份",
                "noon_b": "午B半份13:30前完成，保证睡前8小时以外",
                "night_a": "晚A随晚餐后或睡前1-2小时",
            },
            "diet_rules": ["规则一" * 20, "规则二", "规则三", "规则四"],
            "exercise_rules": ["运动一" * 20, "运动二", "运动三"],
            "daily_checkin": normalize_daily_checkin_items(
                [
                    {"order": 1, "category": "product", "title": "个案产品排班是否按方案完成",
                     "answer_type": "yes_no", "options": ["是", "否"],
                     "example_answer": "早C已完成；午B未完成；晚A已完成"},
                    {"order": 2, "category": "diet", "title": "蔬菜总量是否至少2拳",
                     "answer_type": "yes_no", "options": ["是", "否"],
                     "example_answer": "是，已完成"},
                    {"order": 3, "category": "activity", "title": "是否完成30分钟快走或7000步",
                     "answer_type": "yes_no", "options": ["是", "否"],
                     "example_answer": "是，已完成"},
                    {"order": 4, "category": "sleep", "title": "是否在23:30前闭眼",
                     "answer_type": "yes_no", "options": ["是", "否"],
                     "example_answer": "是，已完成"},
                    {"order": 5, "category": "state", "title": "饭后困评分（1分很困、5分不困）",
                     "answer_type": "score_1_5", "options": ["1", "2", "3", "4", "5"],
                     "example_answer": "4分，饭后精神尚可"},
                ],
                facts={"weight_kg": "78"},
            ),
        },
    )
    blocks = {block["key"]: block for block in content_json["blocks"]}

    goal_lines = blocks["goal_summary"]["content"].splitlines()
    assert len(goal_lines) == 6
    assert goal_lines[0].startswith("管理带：")
    assert goal_lines[1].startswith("标准目标：")
    assert goal_lines[2].startswith("高承诺目标：")
    assert goal_lines[3].startswith("执行约定：")
    assert goal_lines[4].startswith("长期方向：")
    assert goal_lines[5].startswith("当前主线：")
    summary_text = blocks["goal_summary"]["content"]
    assert "28天减重4-5斤" in summary_text
    assert "向6斤推进" in summary_text
    assert "12周形成稳定节律" in summary_text
    assert "近3个月身体趋势" not in summary_text
    assert "C带｜明确减脂" in summary_text

    # 产品排班一段一行（三段），且不重复写「早C 早C」这种前缀。
    # 不再压成一行：写全用量+水量+时点后整行约120字，docx 折点不可控。
    product_lines = blocks["product_schedule"]["content"].splitlines()
    assert len(product_lines) == 3
    assert [line[:2] for line in product_lines] == ["早C", "午B", "晚A"]
    assert "早C 早C" not in blocks["product_schedule"]["content"]
    # 时点不得被静默截断：三段各自的原文必须完整出现
    assert product_lines[1].endswith("保证睡前8小时以外")
    assert product_lines[2] == "晚A 随晚餐后或睡前1-2小时"

    # 条数上限是硬约束
    assert len(blocks["diet_rules"]["items"]) == 3
    assert len(blocks["exercise_rules"]["items"]) == 2
    # 单条字数是软约束：超长内容原样保留，不得被截断丢句
    assert blocks["diet_rules"]["items"][0] == "规则一" * 20
    assert blocks["exercise_rules"]["items"][0] == "运动一" * 20
    # 但必须留下可观测的告警，方便运营/开发发现模型写超预算
    over_budget_warnings = [
        record.message
        for record in caplog.records
        if "exceeds soft length budget" in record.message
    ]
    assert any("length=60 limit=34" in message for message in over_budget_warnings)
    assert any("length=60 limit=44" in message for message in over_budget_warnings)

    # 结构化打卡定义随文档一起保存，H5 问卷才能拿到同一份 answer_type/options
    checkin_block = blocks["daily_checkin"]
    assert len(checkin_block["checkin_items"]) == 5
    assert [item["order"] for item in checkin_block["checkin_items"]] == [1, 2, 3, 4, 5]
    assert all(item["example_answer"] for item in checkin_block["checkin_items"])
    assert checkin_block["content"].splitlines()[0].startswith("1. ")
    assert "每日固定打卡" not in checkin_block["content"]


@pytest.mark.asyncio
async def test_manual_review_keeps_business_band_and_blocks_auto_send(
    admin_client,
    create_case,
    db_session,
    mock_ai,
    cai_haoran_answers,
    wait_background,
):
    """用药样本：内部复核标记生效，但管理带仍是业务带位、用户正文不含内部标签、自动发送被阻止。"""

    mock_ai._responses = {
        SAFETY_SYSTEM_PROMPT_PREFIX: _manual_review_safety_response(),
        # 故意让文档模型越界输出复核字段，验证它既看不到，也无权决定运营状态。
        INTAKE_SYSTEM_PROMPT_PREFIX: _make_intake_mock_response(manual_review=True),
    }
    answers = dict(cai_haoran_answers)
    answers["SIMPLE1774108071693mn0i515a"] = "当前长期服用处方降压药"
    # 全局策略开成 auto_send：需人工复核的入营文件仍然不得自动发送
    await admin_client.post("/settings/content-delivery-policy", json={"mode": "auto_send"})
    try:
        group_id = f"test-manual-review-{secrets.token_hex(6)}"
        resp = await create_case({
            "group_id": group_id,
            "user": {"name": "用药复核用户"},
        })
        assert resp.status_code == 201, resp.text
        sc = resp.json()
        token = sc["questionnaire_token"]
        case_id = sc["id"]

        submit_resp = await admin_client.post(
            f"/questionnaires/{token}/submit?template=intake",
            json={"answers": answers},
        )
        assert submit_resp.status_code == 201, submit_resp.text
        await wait_background()

        profile = await db_session.scalar(
            select(UserProfile).where(UserProfile.service_case_id == case_id)
        )
        assert profile is not None
        # 文档模型即使越界把复核标签写成带位，最终仍使用安全减重空间算出的B带。
        assert profile.profile_json["management_band"] == "B带｜轻盈启动"
        assert "复核" not in profile.profile_json["management_band"]
        # 内部字段仍然保留，供后台和人工复核使用
        assert profile.profile_json["manual_review_required"] is True
        assert profile.profile_json["safety_status"] == "manual_review"
        assert profile.profile_json["manual_review_reasons"] == ["问卷填写当前长期用药"]
        assert profile.profile_json["provisional_band"] is False
        assert profile.profile_json["band_confidence"] == ""

        documents = list(await db_session.scalars(
            select(UserDocument).where(UserDocument.service_case_id == case_id)
        ))
        assert len(documents) == 2
        for document in documents:
            # 文档保持待审核，绝不能自动发出去
            assert document.status == DocumentStatus.REVIEWING, document.title
            assert document.sent_at is None
            # 内部标记在 content_json 顶层元数据里，后台能读到
            internal_review = document.content_json["internal_review"]
            assert internal_review["manual_review_required"] is True
            assert internal_review["reasons"] == ["问卷填写当前长期用药"]
            # 用户可见正文（blocks / content_md）一个内部标签都不能有
            rendered = json.dumps(document.content_json["blocks"], ensure_ascii=False)
            for label in ("需人工复核", "人工复核", "待复核", "red风险", "风险等级"):
                assert label not in rendered, f"{document.title} 的 blocks 泄漏了 {label}"
                assert label not in document.content_md, f"{document.title} 的正文泄漏了 {label}"
                assert "28天减重4-6斤" in document.content_md
            assert "D7复评" not in document.content_md
            # 整句剔除，不留「本方案。」这类残句
            assert "本方案。" not in document.content_md

        # 后台审核列表能拿到醒目标记
        resp = await admin_client.get("/dashboard/pending-documents")
        assert resp.status_code == 200, resp.text
        pending = [
            document
            for group in resp.json()
            if group["case"]["service_case_id"] == case_id
            for document in group["documents"]
        ]
        assert len(pending) == 2
        assert all(document["manual_review_required"] is True for document in pending)
        assert all(
            document["manual_review_reasons"] == ["问卷填写当前长期用药"]
            for document in pending
        )
        safety_calls = [
            call for call in mock_ai.calls if call.get("scenario") == "intake_safety_factors"
        ]
        document_calls = [
            call for call in mock_ai.calls if call.get("scenario") == "intake_doc"
        ]
        assert len(safety_calls) == 1
        assert len(document_calls) == 1
        assert "当前长期服用处方降压药" in str(safety_calls[0]["user_prompt"])
        document_prompt = str(document_calls[0]["user_prompt"])
        for internal_text in (
            "当前长期服用处方降压药",
            "safety_review_required",
            "manual_review_required",
            "问卷填写当前长期用药",
        ):
            assert internal_text not in document_prompt
    finally:
        await admin_client.post("/settings/content-delivery-policy", json={"mode": "manual_review"})


@pytest.mark.asyncio
async def test_questionnaire_high_risk_forces_document_review_marker(
    admin_client,
    create_case,
    db_session,
    mock_ai,
    cai_haoran_answers,
    wait_background,
    monkeypatch,
):
    """孕期仍正常计算A-D；即使文档模型返回 manual_review=false，服务单高风险也必须点亮复核标记。

    安全提取的持续风险明细负责点亮服务单 high 风险；它不需要再走群聊风控。
    """

    async def unexpected_chat_insight(**_kwargs):
        raise AssertionError("intake generation must not call the chat risk/intent classifier")

    monkeypatch.setattr(
        "app.services.message_insight_service.analyze_message_insight",
        unexpected_chat_insight,
    )
    mock_ai._responses = {
        SAFETY_SYSTEM_PROMPT_PREFIX: json.dumps(
            {
                "safety_review_required": False,
                "persistent_risk_flags": [
                    {
                        "code": "pregnancy",
                        "reason": "问卷明确填写当前怀孕",
                        "evidence": "当前明确怀孕",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        INTAKE_SYSTEM_PROMPT_PREFIX: _make_intake_mock_response(manual_review=False),
    }
    answers = dict(cai_haoran_answers)
    answers["SIMPLE1774108071693mn0i515a"] = "当前明确怀孕"
    group_id = f"test-pregnancy-review-{secrets.token_hex(6)}"
    resp = await create_case({
        "group_id": group_id,
        "user": {"name": "孕期复核用户"},
    })
    assert resp.status_code == 201, resp.text
    case = resp.json()

    submit_resp = await admin_client.post(
        f"/questionnaires/{case['questionnaire_token']}/submit?template=intake",
        json={"answers": answers},
    )
    assert submit_resp.status_code == 201, submit_resp.text
    await wait_background()

    service_case = await db_session.get(ServiceCase, case["id"])
    assert service_case is not None
    assert service_case.risk_level == "high"

    profile = await db_session.scalar(
        select(UserProfile).where(UserProfile.service_case_id == case["id"])
    )
    assert profile is not None
    # 安全条件不改带位：孕期用户照常按可管理空间拿 B 带。
    assert profile.profile_json["management_band"] == "B带｜轻盈启动"
    # 安全提取本身只输出持续风险，复核标记由服务单风险等级点亮。
    assert profile.profile_json["safety_review_required"] is False
    assert profile.profile_json["manual_review_required"] is True
    assert profile.profile_json["safety_status"] == "manual_review"

    documents = list(await db_session.scalars(
        select(UserDocument).where(UserDocument.service_case_id == case["id"])
    ))
    assert len(documents) == 2
    assert all(
        document.content_json["internal_review"]["manual_review_required"] is True
        for document in documents
    )


@pytest.mark.asyncio
async def test_manual_review_document_can_still_be_sent_by_operator(
    admin_client,
    create_case,
    db_session,
    mock_ai,
    cai_haoran_answers,
    wait_background,
):
    """人工检查/修改后点「确认发送」仍然可用——阻断的是自动发送，不是人工发送。"""

    mock_ai._responses = {
        SAFETY_SYSTEM_PROMPT_PREFIX: _manual_review_safety_response(),
        INTAKE_SYSTEM_PROMPT_PREFIX: _make_intake_mock_response(manual_review=True),
    }
    answers = dict(cai_haoran_answers)
    answers["SIMPLE1774108071693mn0i515a"] = "当前长期服用处方降压药"
    group_id = f"test-manual-review-send-{secrets.token_hex(6)}"
    resp = await create_case({
        "group_id": group_id,
        "user": {"name": "人工发送用户"},
    })
    assert resp.status_code == 201, resp.text
    sc = resp.json()
    token = sc["questionnaire_token"]
    case_id = sc["id"]

    submit_resp = await admin_client.post(
        f"/questionnaires/{token}/submit?template=intake",
        json={"answers": answers},
    )
    assert submit_resp.status_code == 201, submit_resp.text
    await wait_background()

    document = await db_session.scalar(
        select(UserDocument).where(
            UserDocument.service_case_id == case_id,
            UserDocument.document_type == DocumentType.DAY28_EXECUTION_STANDARD,
        )
    )
    assert document is not None
    assert document.status == DocumentStatus.REVIEWING

    resp = await admin_client.post(f"/documents/{document.id}/send")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "sent"


def test_shared_analysis_is_the_single_source_for_cross_document_facts():
    """28天任务、总路径目标、身体趋势和当前主线以 shared_analysis 为唯一来源强制覆写。

    真实模型会在两份文件里各写一遍同一件事，写成同义改写（「腰腹缩围和精力转折」
    对「腰腹围度收紧和精力转向」），既冗余又会带数字漂移。这里用观测到的真实
    漂移文本当输入，确认两份文件落地后逐字一致。
    """

    shared = {
        "weight_band": "B带｜轻盈启动",
        "base_goal": "28天先稳定节律并建立可持续的小幅减脂趋势",
        "challenge_goal": "基础动作稳定后再评估是否加码",
        "day28_goal": "用均衡餐盘替代原高碳结构，收住晚餐后进食，每日温和活动，实现腰腹缩围和精力转折",
        "long_term_goal": "12周建立可长期复制的节律，腰腹围度持续收紧",
        "body_trend": "缓慢变沉",
        "day28_task": "先把晚餐收口和餐盘结构稳定下来",
        "current_mainline": "节律稳定",
    }
    payload = {
        DocumentType.ENERGY_RESTART_PRESCRIPTION.value: {
            "one_sentence": "一句话结论" * 20,
            "overview": {"current_focus": "保肌减脂"},
            "core_conclusions": {"management_band": "C带｜明确减脂", "day28_task": "先抓晚间收口"},
            "goal_response": {"base_goal": "直接冲刺减重", "challenge_goal": "继续加码"},
            "success_path": {
                "weight_band": "D带｜系统减重",
                "day28_goal": "用均衡餐盘取代高碳结构，三餐收口并启动每日温和活动，实现腰腹围度收紧和精力转向",
                "total_goal": "12周把节律固定下来，围度继续下行",
            },
        },
        DocumentType.DAY28_EXECUTION_STANDARD.value: {
            "day28_goal": "均衡餐盘加晚间收口，配合每日温和活动，让腰腹围度和精力一起转向",
            "path_goal": "12周形成稳定节律，围度稳步收紧",
            "body_trend": "基本稳定",
            "current_mainline": "睡眠修复",
            "principle": "先守底盘，再抓结果" * 5,
        },
    }

    normalized = _normalize_combined_document_payload(payload, "单一来源用户", shared_analysis=shared)

    energy = normalized[DocumentType.ENERGY_RESTART_PRESCRIPTION.value]
    execution = normalized[DocumentType.DAY28_EXECUTION_STANDARD.value]
    assert energy["success_path"]["day28_goal"] == shared["day28_goal"]
    assert execution["day28_goal"] == shared["day28_goal"]
    assert energy["core_conclusions"]["management_band"] == shared["weight_band"]
    assert energy["success_path"]["weight_band"] == shared["weight_band"]
    assert energy["goal_response"]["base_goal"] == shared["base_goal"]
    assert energy["goal_response"]["challenge_goal"] == shared["challenge_goal"]
    assert energy["success_path"]["total_goal"] == shared["long_term_goal"]
    assert execution["path_goal"] == shared["long_term_goal"]
    # 趋势不能一份写「缓慢变沉」另一份写「基本稳定」
    assert execution["body_trend"] == shared["body_trend"]
    assert energy["core_conclusions"]["day28_task"] == shared["day28_task"]
    assert energy["overview"]["current_focus"] == shared["current_mainline"]
    assert execution["current_mainline"] == shared["current_mainline"]


def test_shared_activity_frequency_syncs_full_profile_and_documents():
    """三处原始自由文本即使各写不同次数，也只能展示共同计划的次数。"""

    # 即使模型自己填了 2 次，产品规则的共同计划仍固定为每周 3 次。
    shared = {"activity_plan": {"status": "planned", "sessions_per_week": "2次"}}
    documents = {
        DocumentType.ENERGY_RESTART_PRESCRIPTION.value: {
            "week_actions": [
                "每周2次30分钟快走，避开睡前3小时",
                "每周1次晨重和腰围复测",
            ],
            "special_reminder": "运动从每周4次改为更容易坚持的频次。",
        },
        DocumentType.DAY28_EXECUTION_STANDARD.value: {
            "exercise_rules": ["每周4次30分钟快走，中低强度，以能说话为准"],
            "weekly_plan": [
                {"week": "第3周", "acceptance_standard": "每周5次轻力量，每次20分钟"},
            ],
            "weekly_standard": "每周1次复测腰围，完成计划内活动。",
        },
    }
    profile = {
        "summary": "目前每周2次30分钟快走，执行不稳定。",
        "state": "计划每周4次步行，每次20分钟。",
        "need_analysis": {"next_actions": ["每周5次轻力量，每次20分钟"]},
    }

    sessions = _normalize_shared_activity_frequency(shared)
    _apply_shared_activity_frequency_to_documents(documents, sessions)
    _apply_shared_activity_frequency_to_profile(profile, sessions)

    energy = documents[DocumentType.ENERGY_RESTART_PRESCRIPTION.value]
    execution = documents[DocumentType.DAY28_EXECUTION_STANDARD.value]
    assert shared["activity_plan"] == {"status": "planned", "sessions_per_week": 3}
    assert profile["activity_plan"] == {"status": "planned", "sessions_per_week": 3}
    assert profile["summary"] == "目前每周3次30分钟快走，执行不稳定。"
    assert profile["state"] == "计划每周3次步行，每次20分钟。"
    assert profile["need_analysis"]["next_actions"] == ["每周3次轻力量，每次20分钟"]
    assert energy["week_actions"][0] == "每周3次30分钟快走，避开睡前3小时"
    # 非运动的复测频次必须保留，不能被统一规则误改。
    assert energy["week_actions"][1] == "每周1次晨重和腰围复测"
    assert energy["special_reminder"] == "运动从每周3次改为更容易坚持的频次。"
    assert execution["exercise_rules"] == ["每周3次30分钟快走，中低强度，以能说话为准"]
    assert execution["weekly_plan"][0]["acceptance_standard"] == "每周3次轻力量，每次20分钟"
    assert execution["weekly_standard"] == "每周1次复测腰围，完成计划内活动。"


def test_shared_activity_frequency_allows_two_or_four_sessions():
    for requested in (2, 4):
        shared = {"activity_plan": {"status": "planned", "sessions_per_week": requested}}
        assert _normalize_shared_activity_frequency(shared) == requested
        assert shared["activity_plan"]["sessions_per_week"] == requested


def test_a_band_user_visible_goal_contract_replaces_model_numbers():
    shared = {
        "weight_band": "A带｜修复塑形",
        "base_goal": "保持体重基本稳定，修复睡眠和精力",
        "challenge_goal": "不设置减重挑战目标",
        "day28_goal": "修复睡眠和精力，观察体态变化",
        "long_term_goal": "建立可持续的恢复节律",
        "body_trend": "基本稳定",
        "day28_task": "先修复睡眠和精力",
        "current_mainline": "睡眠修复",
    }
    payload = {
        DocumentType.ENERGY_RESTART_PRESCRIPTION.value: {
            "one_sentence": "一句话结论" * 20,
            "overview": {"current_focus": "睡眠修复"},
            "core_conclusions": {
                "management_band": "A带｜修复塑形",
                "band_explanation": "安全减重空间不到2公斤",
            },
            "goal_response": {
                "content": "您希望28天减重10斤，但当前不建议。",
                "user_goal": "28天减重10斤",
                "judgement": "减重10斤不适合",
                "base_goal": "保持体重基本稳定",
                "challenge_goal": "不设置减重挑战目标",
            },
            "success_path": {
                "weight_band": "A带｜修复塑形",
                "day28_goal": "修复睡眠和精力，观察体态变化",
                "total_goal": "建立可持续的恢复节律",
            },
        },
        DocumentType.DAY28_EXECUTION_STANDARD.value: {
            "day28_goal": "修复睡眠和精力，观察体态变化",
            "path_goal": "建立可持续的恢复节律",
            "body_trend": "基本稳定",
            "current_mainline": "睡眠修复",
            "principle": "先守底盘，再抓结果" * 5,
        },
    }

    normalized = _normalize_combined_document_payload(
        payload,
        "A带定性回应用户",
        shared_analysis=shared,
    )
    energy = normalized[DocumentType.ENERGY_RESTART_PRESCRIPTION.value]
    visible_text = energy["core_conclusions"]["band_explanation"] + json.dumps(
        energy["goal_response"], ensure_ascii=False
    )

    assert "10斤" not in visible_text
    assert "2公斤" not in visible_text
    assert "安全减重空间" not in visible_text
    assert energy["goal_response"]["user_goal"] == "希望尽快看到体重和体态变化"
    assert energy["goal_response"]["challenge_goal"] == shared["challenge_goal"]


def test_force_sync_keeps_document_text_when_shared_analysis_is_empty():
    """shared_analysis 缺字段时保留文档里模型写的那份，不能把字段清空。"""

    payload = {
        DocumentType.ENERGY_RESTART_PRESCRIPTION.value: {
            "one_sentence": "一句话结论" * 20,
            "success_path": {"day28_goal": "先稳定三餐节律", "total_goal": "12周维持"},
        },
        DocumentType.DAY28_EXECUTION_STANDARD.value: {
            "day28_goal": "先稳定三餐节律",
            "path_goal": "12周维持",
            "principle": "先守底盘，再抓结果" * 5,
        },
    }

    normalized = _normalize_combined_document_payload(
        payload, "缺字段用户", shared_analysis={"day28_goal": "", "long_term_goal": None}
    )

    assert normalized[DocumentType.DAY28_EXECUTION_STANDARD.value]["day28_goal"] == "先稳定三餐节律"
    assert normalized[DocumentType.DAY28_EXECUTION_STANDARD.value]["path_goal"] == "12周维持"


@pytest.mark.asyncio
async def test_submit_leaves_pending_signal_without_local_ai_key(
    admin_client,
    create_case,
    db_session,
    mock_ai,
    cai_haoran_answers,
):
    """提交进程没有 AI key(生产的常驻 ingress 就是这样)也必须留下"待生成"信号。

    否则消费端(backend)永远等不到活，服务单会停在"已交问卷、没有交付文件"。
    """
    mock_ai._responses = {
        INTAKE_SYSTEM_PROMPT_PREFIX: _make_intake_mock_response(),
    }
    group_id = f"test-group-nokey-{secrets.token_hex(6)}"
    resp = await create_case({"group_id": group_id, "user": {"name": "无密钥进程"}})
    assert resp.status_code == 201, resp.text
    token = resp.json()["questionnaire_token"]
    case_id = resp.json()["id"]

    from app.core.config import get_settings as real_get_settings

    keyless = real_get_settings().model_copy(update={"ai_api_key": ""})
    with patch(
        "app.api.routes.questionnaires.get_settings",
        return_value=keyless,
    ):
        submit_resp = await admin_client.post(
            f"/questionnaires/{token}/submit?template=intake",
            json={"answers": cai_haoran_answers},
        )
    assert submit_resp.status_code == 201, submit_resp.text

    service_case = await db_session.get(ServiceCase, case_id)
    await db_session.refresh(service_case)
    assert service_case.intake_pending_generation_at is not None


@pytest.mark.asyncio
async def test_missing_intake_documents_recovered_after_service_started(
    admin_client,
    create_case,
    db_session,
    mock_ai,
    cai_haoran_answers,
):
    """服务已经开始、待生成信号已丢的单子，也要被补跑出交付文件。

    计划开始日到期后调度器会把"生成中"激活成"服务中"，只按状态兜底就再也捞不到，
    生产上 8/2-8/4 的服务单正是这样一直没有交付文件。
    """
    from app.services.reminder_service import ReminderService

    mock_ai._responses = {
        INTAKE_SYSTEM_PROMPT_PREFIX: _make_intake_mock_response(),
    }
    group_id = f"test-group-recover-{secrets.token_hex(6)}"
    resp = await create_case({"group_id": group_id, "user": {"name": "缺文件用户"}})
    assert resp.status_code == 201, resp.text
    token = resp.json()["questionnaire_token"]
    case_id = resp.json()["id"]

    submit_resp = await admin_client.post(
        f"/questionnaires/{token}/submit?template=intake",
        json={"answers": cai_haoran_answers},
    )
    assert submit_resp.status_code == 201, submit_resp.text

    # 复刻生产形态：信号没落下/已被清，状态被到期激活推成"服务中"，问卷交了很久。
    service_case = await db_session.get(ServiceCase, case_id)
    service_case.intake_pending_generation_at = None
    service_case.status = ServiceCaseStatus.IN_SERVICE
    submission = await db_session.scalar(
        select(QuestionnaireSubmission).where(
            QuestionnaireSubmission.service_case_id == case_id,
            QuestionnaireSubmission.template_key == "intake",
        )
    )
    submission.created_at = datetime.now(timezone.utc) - timedelta(hours=2)
    await db_session.commit()

    await ReminderService().check_stuck_document_generation_once()

    documents = list(
        await db_session.scalars(
            select(UserDocument).where(UserDocument.service_case_id == case_id)
        )
    )
    assert {doc.document_type for doc in documents} == {
        DocumentType.ENERGY_RESTART_PRESCRIPTION,
        DocumentType.DAY28_EXECUTION_STANDARD,
    }
    profile = await db_session.scalar(
        select(UserProfile).where(UserProfile.service_case_id == case_id)
    )
    assert profile is not None
    # 补跑不能让已经在服务中的单子状态倒退回待审核。
    await db_session.refresh(service_case)
    assert service_case.status == ServiceCaseStatus.IN_SERVICE

    # 再跑一轮不应重复生成(已有文件即退出候选)。
    await ReminderService().check_stuck_document_generation_once()
    again = list(
        await db_session.scalars(
            select(UserDocument).where(UserDocument.service_case_id == case_id)
        )
    )
    assert len(again) == len(documents)


@pytest.mark.asyncio
async def test_submit_receipt_is_sent_before_generation(
    admin_client,
    create_case,
    db_session,
    mock_ai,
    cai_haoran_answers,
    wait_background,
):
    """提交回执必须先发、不等文档生成：连生成失败的单子也要收到回执。

    回执正文就是"已收到、正在生成、请稍等"，等文档生成完(单次可达数分钟)才发，用户
    提交后会长时间零反馈；挂在生成成功之后更会让失败的单子永远收不到回执。
    """
    from app.models.message import Message, MessageDirection

    mock_ai._responses = {
        INTAKE_SYSTEM_PROMPT_PREFIX: "",  # 空 JSON → 生成失败
    }
    group_id = f"test-group-receipt-{secrets.token_hex(6)}"
    resp = await create_case({"group_id": group_id, "user": {"name": "回执用户"}})
    assert resp.status_code == 201, resp.text
    token = resp.json()["questionnaire_token"]
    case_id = resp.json()["id"]

    submit_resp = await admin_client.post(
        f"/questionnaires/{token}/submit?template=intake",
        json={"answers": cai_haoran_answers},
    )
    assert submit_resp.status_code == 201, submit_resp.text
    await wait_background()

    receipts = list(
        await db_session.scalars(
            select(Message).where(
                Message.service_case_id == case_id,
                Message.direction == MessageDirection.OUTBOUND,
                Message.content.like("问卷已收到%"),
            )
        )
    )
    assert len(receipts) == 1, "生成失败的单子也必须已经收到提交回执"

    # 生成确实失败了(状态回退)，回执与它无关。
    failed_case = await db_session.get(ServiceCase, case_id)
    await db_session.refresh(failed_case)
    assert failed_case.status == ServiceCaseStatus.QUESTIONNAIRE_SUBMITTED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("enabled", "expects_reward"),
    [(False, False), (True, True)],
)
async def test_intake_receipt_only_promises_rewards_when_enabled(
    db_session, monkeypatch, enabled, expects_reward
):
    from app.api.routes import questionnaires
    from app.models.checkin_reward import CheckinRewardProgram
    from app.models.message import Message, MessageDirection
    from app.models.service_case import ServiceCase
    from app.models.user import User
    from app.services.checkin_questionnaires import (
        CHECKIN_REWARD_RECEIPT_PARAGRAPH,
        default_checkin_schedule,
    )
    from app.services.checkin_reward_settings import (
        build_reward_receipt_paragraph,
        get_reward_tier_config,
        set_reward_tier_config,
    )

    await set_reward_tier_config(db_session, {"enabled": enabled})
    expected_paragraph = build_reward_receipt_paragraph(
        await get_reward_tier_config(db_session)
    )

    user = User(name="奖励回执用户")
    db_session.add(user)
    await db_session.flush()
    service_case = ServiceCase(
        case_no=f"RECEIPT-{secrets.token_hex(4)}",
        user_id=user.id,
        group_id=f"group-receipt-{secrets.token_hex(4)}",
        questionnaire_token=secrets.token_urlsafe(12),
        service_plan_start_date=datetime(2026, 8, 20).date(),
    )
    db_session.add(service_case)
    await db_session.flush()

    async def fake_schedule(session):
        schedule = default_checkin_schedule()
        if not enabled:
            schedule["intake_submit_reply"]["message"] = schedule[
                "intake_submit_reply"
            ]["message"].replace("{reward_intro}", CHECKIN_REWARD_RECEIPT_PARAGRAPH)
        return schedule

    monkeypatch.setattr(questionnaires, "get_checkin_questionnaire_schedule", fake_schedule)
    await questionnaires._send_intake_submit_reply(service_case.id)

    receipt = await db_session.scalar(
        select(Message).where(
            Message.service_case_id == service_case.id,
            Message.direction == MessageDirection.OUTBOUND,
        )
    )
    assert receipt is not None
    # 奖励介绍以档位配置动态生成；旧固定段只做老模板兼容，不应再出现在回执里。
    assert (expected_paragraph != "" and expected_paragraph in receipt.content) is expects_reward
    assert CHECKIN_REWARD_RECEIPT_PARAGRAPH not in receipt.content
    assert "{reward_intro}" not in receipt.content
    assert "{plan_start_line}" not in receipt.content
    assert "2026年08月20日" in receipt.content
    program = await db_session.scalar(
        select(CheckinRewardProgram).where(
            CheckinRewardProgram.service_case_id == service_case.id
        )
    )
    assert (program is not None and program.reward_config_snapshot is not None) is enabled
    if enabled:
        await set_reward_tier_config(
            db_session,
            {
                "enabled": True,
                "tiers": [{"days": 14, "reward_text": "修改后的奖品"}],
            },
        )
        changed_paragraph = build_reward_receipt_paragraph(
            await get_reward_tier_config(db_session)
        )
        await questionnaires._send_intake_submit_reply(service_case.id)
        receipts = list(
            await db_session.scalars(
                select(Message)
                .where(
                    Message.service_case_id == service_case.id,
                    Message.direction == MessageDirection.OUTBOUND,
                )
                .order_by(Message.id)
            )
        )
        assert len(receipts) == 2
        assert expected_paragraph in receipts[-1].content
        assert changed_paragraph not in receipts[-1].content


def test_overview_measurements_only_get_units_when_numeric():
    """量测栏只给真数值拼单位：用户把整句填进体脂率时不能印出"体脂率不知道%"。"""
    from app.services.intake_generation import _format_waist_bodyfat

    assert _format_waist_bodyfat("87", "后续告知") == "腰围87cm / 体脂率未提供"
    assert _format_waist_bodyfat("未提供", "暂时没测量在出差") == "腰围未提供 / 体脂率未提供"
    assert _format_waist_bodyfat("未提供", "不知道") == "腰围未提供 / 体脂率未提供"
    # 正常数值、区间与已带单位的写法保持原样
    assert _format_waist_bodyfat("83", "28-29") == "腰围83cm / 体脂率28-29%"
    assert _format_waist_bodyfat("87cm", "28%") == "腰围87cm / 体脂率28%"
    # 夹着说明的数值保留信息，只是不再拼单位
    assert _format_waist_bodyfat("未提供", "30 左右，去年测的") == "腰围未提供 / 体脂率30 左右，去年测的"


def test_english_document_titles_and_filenames():
    from app.services.document_common import get_document_title

    assert get_document_title(DocumentType.ENERGY_RESTART_PRESCRIPTION, language="en") == "Personal Energy Reboot Diagnostic Report"
    assert get_document_title(DocumentType.DAY28_EXECUTION_STANDARD, language="en") == "28-Day HumanBurning Action Plan"

    assert _document_filename(
        user_name="Alex",
        document_type=DocumentType.ENERGY_RESTART_PRESCRIPTION,
        title="Personal Energy Reboot Diagnostic Report",
        gender="",
    ) == "Alex_Personal Energy Reboot Diagnostic Report.docx"
    assert _document_filename(
        user_name="Alex",
        document_type=DocumentType.DAY28_EXECUTION_STANDARD,
        title="28-Day HumanBurning Action Plan",
        gender="",
    ) == "Alex_28-Day HumanBurning Action Plan.docx"


def test_english_document_content_rendering():
    from app.services.document_templates import build_document_content_json

    energy_json = build_document_content_json(
        document_type=DocumentType.ENERGY_RESTART_PRESCRIPTION,
        title="Personal Energy Reboot Diagnostic Report",
        user_name="Alex",
        fields={"overview": {"name": "Alex", "height_weight": "180cm / 75kg"}},
        language="en",
    )
    assert energy_json["blocks"][0]["title"] == "1. User Profile Overview"

    execution_json = build_document_content_json(
        document_type=DocumentType.DAY28_EXECUTION_STANDARD,
        title="28-Day HumanBurning Action Plan",
        user_name="Alex",
        fields={
            "weight_band": "Band B",
            "overview": {"name": "Alex"},
            "weekly_standards": {
                "week1": "Follow morning C protocol",
                "week2": "Consistent diet",
            },
        },
        language="en",
    )
    assert execution_json["blocks"][0]["title"] == "1. Target & Goal Summary"
    # Verify table headers in week blocks are localized
    table_block = next((b for b in execution_json["blocks"] if b.get("kind") == "table"), None)
    assert table_block is not None
    assert table_block["rows"][0][0]["value"] == "Week"
    assert table_block["rows"][1][0]["value"] == "Week 1"


def test_english_documents_render_without_chinese_or_fullwidth_punctuation():
    """英文交付文档的渲染层不得漏出中文或全角标点。

    这些残留全是代码拼上去的，不是模型写的：兜底句、补回的安全边界、分句连接符、
    「To：」和「Key Factor 1：」的冒号、标题里的「先生」。它们不受"输出必须全英文"
    的提示词约束，所以只能在渲染层守住——英文客户拿到的文件里出现中文，
    看起来就是没做完的半成品。
    """
    import re

    from app.services.document_templates import (
        build_document_content_json,
        render_document_content,
    )

    cjk = re.compile(r"[一-鿿]")
    fullwidth = "：；，。、（）"

    # 模型这几栏留空 / 只给一句，正好逼出所有兜底与补句分支。
    energy_json = build_document_content_json(
        document_type=DocumentType.ENERGY_RESTART_PRESCRIPTION,
        title="Personal Energy Reboot Diagnostic Report",
        user_name="Alex",
        display_name="Alex",
        fields={
            "recipient": "Alex",
            "overview": {"name": "Alex", "height_weight": "180cm / 165 lbs"},
            "personal_execution_basis": {},
            "key_influence_chains": [
                "Screen-heavy work -> low activity -> energy crash",
                "Late dinner -> poor sleep -> low morning energy",
            ],
        },
        language="en",
    )
    execution_json = build_document_content_json(
        document_type=DocumentType.DAY28_EXECUTION_STANDARD,
        title="28-Day HumanBurning Action Plan",
        user_name="Alex",
        display_name="Alex",
        fields={
            "weight_band": "Band B | Gentle Energy Kickstart",
            "commitment_rule": "",
            "weekly_standard": "Complete the protocol on 6 days; follow diet rules 5 days",
            "adjust_stop_conditions": "Stop intense exercise if dizzy. Reassess weight weekly",
        },
        language="en",
    )

    for payload in (energy_json, execution_json):
        assert payload["language"] == "en"
        rendered = render_document_content(payload)
        assert not cjk.search(rendered), f"chinese leaked: {cjk.findall(rendered)}"
        assert not any(char in rendered for char in fullwidth), rendered

    execution_md = render_document_content(execution_json)
    # 被长度预算砍掉后必须用英文补回来，而不是中文
    assert "log weight or waist/how you feel at least once." in execution_md
    assert "seek medical care promptly" in execution_md


def test_english_commitment_rule_is_forced_in_english():
    """执行约定是强制覆写的固定口径，英文服务单不能被覆写成中文那句。"""

    payload = {
        DocumentType.ENERGY_RESTART_PRESCRIPTION.value: {
            "one_sentence": (
                "Stabilize your daily rhythm and daytime energy first, rather than "
                "chasing fast weight loss."
            ),
            "success_path": {
                "day28_goal": "Lose 4.4-6.6 lbs in 28 days",
                "total_goal": "Build a sustainable rhythm with steady energy.",
            },
        },
        DocumentType.DAY28_EXECUTION_STANDARD.value: {
            "day28_goal": "Lose 4.4-6.6 lbs in 28 days",
            "path_goal": "Build a sustainable rhythm with steady energy.",
            "commitment_rule": "Whatever the model wrote",
        },
    }

    normalized = _normalize_combined_document_payload(
        payload, "Alex", shared_analysis={}, language="en"
    )
    rule = normalized[DocumentType.DAY28_EXECUTION_STANDARD.value]["commitment_rule"]
    assert rule.startswith("Standard tier: 60% full check-in rate")
    assert not re.search(r"[一-鿿]", rule)

    zh_payload = {
        DocumentType.ENERGY_RESTART_PRESCRIPTION.value: {
            "one_sentence": "你需要先稳住全天节律和能量曲线，而不是追求快速掉秤。",
            "success_path": {
                "day28_goal": "28天减重4-6斤",
                "total_goal": "建立稳定的三餐节律和精力曲线，减少午后困倦和晚餐后失控。",
            },
            "core_conclusions": {"day28_task": "先解决晚餐失控和白天精力波动。"},
        },
        DocumentType.DAY28_EXECUTION_STANDARD.value: {
            "day28_goal": "28天减重4-6斤",
            "path_goal": "建立稳定的三餐节律和精力曲线。",
            "principle": "先守三餐节律和晚间收口，再放大运动和结果。",
            "current_mainline": "节律稳定",
        },
    }
    zh_normalized = _normalize_combined_document_payload(
        zh_payload, "张三", shared_analysis={}
    )
    assert zh_normalized[DocumentType.DAY28_EXECUTION_STANDARD.value]["commitment_rule"] == (
        "标准档累计完整打卡率60%；高承诺档85%，均在群内确认后生效；当天五项都有回答才计为1个完整打卡日。"
    )


def test_english_recipient_name_drops_chinese_honorific():
    from app.services.intake_facts import _recipient_name

    assert _recipient_name("NAR", "蔡浩然", "男", language="en") == "NAR (蔡浩然)"
    assert _recipient_name("Alex", "Alex", "female", language="en") == "Alex"
    # 缺名字仍然回中文哨兵，全系统按它判空
    assert _recipient_name("", "", "", language="en") == "未提供"
    # 中文侧字节级不变
    assert _recipient_name("NAR", "蔡浩然", "男") == "NAR 先生（蔡浩然）"


def test_english_document_filename_has_no_chinese_honorific():
    assert _document_filename(
        user_name="Alex",
        document_type=DocumentType.DAY28_EXECUTION_STANDARD,
        title="28-Day HumanBurning Action Plan",
        gender="男",
        language="en",
    ) == "Alex_28-Day HumanBurning Action Plan.docx"
    assert _document_filename(
        user_name="张三",
        document_type=DocumentType.DAY28_EXECUTION_STANDARD,
        title="28天执行标准与打卡要求",
        gender="男",
    ) == "张三先生_28天执行标准与打卡要求.docx"


@pytest.mark.asyncio
async def test_english_intake_submit_reply(db_session, monkeypatch):
    from datetime import date
    from app.api.routes import questionnaires
    from app.models.message import Message, MessageDirection

    service_case = ServiceCase(
        case_no="CASE-EN-001",
        group_id="group-en-intake",
        questionnaire_token="token-en-001",
        status=ServiceCaseStatus.QUESTIONNAIRE_SUBMITTED,
        language="en",
        service_plan_start_date=date(2026, 9, 10),
    )
    db_session.add(service_case)
    await db_session.flush()

    monkeypatch.setattr(
        questionnaires,
        "send_channel_text",
        AsyncMock(return_value=SimpleNamespace(message_id="msg-en-123", error=None, success=True)),
    )
    await questionnaires._send_intake_submit_reply(service_case.id)

    receipt = await db_session.scalar(
        select(Message).where(
            Message.service_case_id == service_case.id,
            Message.direction == MessageDirection.OUTBOUND,
        )
    )
    assert receipt is not None
    assert "received your enrollment assessment" in receipt.content
    assert "September 10, 2026" in receipt.content


def test_english_daily_checkin_validation_rules():
    from app.services.intake_generation import _generated_daily_checkin_errors

    valid_english_items = [
        {
            "category": "product",
            "title": "Morning C, Noon B, Evening A product protocol",
            "answer_type": "text",
            "example_answer": "Morning C completed, Noon B not completed, Evening A completed",
        },
        {
            "category": "diet",
            "title": "Did you eat at least 2 fist-sized portions of vegetables?",
            "answer_type": "yes_no",
            "example_answer": "Yes, 2 bowls of spinach",
        },
        {
            "category": "activity",
            "title": "Did you complete 20 minutes of brisk walking?",
            "answer_type": "yes_no",
            "example_answer": "Yes, 25 mins brisk walk after dinner",
        },
        {
            "category": "sleep",
            "title": "Did you start sleep wind-down by 23:00?",
            "answer_type": "yes_no",
            "example_answer": "Yes, in bed by 22:45",
        },
        {
            "category": "state",
            "title": "Rate your daytime alertness on a 1-5 scale",
            "answer_type": "score_1_5",
            "example_answer": "4 / 5, felt energetic all day",
        },
    ]
    errors = _generated_daily_checkin_errors(valid_english_items)
    assert errors == []


@pytest.mark.asyncio
async def test_english_document_delivery_intro_and_checkin_template(db_session):
    from app.services.document_delivery import _render_post_document_checkin_example
    from app.services.execution_commitment import render_commitment_invitation

    service_case = ServiceCase(
        case_no="CASE-EN-DELIVERY",
        group_id="group-en-delivery",
        questionnaire_token="token-en-delivery",
        status=ServiceCaseStatus.DOCUMENTS_GENERATED,
        language="en",
    )
    db_session.add(service_case)
    await db_session.flush()

    checkin_items = [
        {"order": 1, "title": "Product Protocol", "example_answer": "Morning C / Noon B / Evening A completed"},
        {"order": 2, "title": "Vegetables", "example_answer": "Yes, 2 fist-sized portions"},
        {"order": 3, "title": "Activity", "example_answer": "Yes, 20 mins walk"},
        {"order": 4, "title": "Sleep", "example_answer": "Yes, slept by 23:00"},
        {"order": 5, "title": "Energy", "example_answer": "4 / 5, high alertness"},
    ]
    doc = UserDocument(
        service_case_id=service_case.id,
        document_type=DocumentType.DAY28_EXECUTION_STANDARD,
        title="28-Day HumanBurning Action Plan",
        status=DocumentStatus.SENT,
        sent_at=datetime.now(timezone.utc),
        content_json={"blocks": [{"key": "daily_checkin", "checkin_items": checkin_items}]},
        content_md="...",
    )
    db_session.add(doc)
    await db_session.flush()

    checkin_msg = await _render_post_document_checkin_example(db_session, service_case_id=service_case.id)
    assert checkin_msg is not None
    assert "Here are your 2 tailored documents" in checkin_msg
    assert "【Daily Check-in】" in checkin_msg
    assert "1. Product Protocol｜Morning C / Noon B / Evening A completed" in checkin_msg

    invitation = render_commitment_invitation(
        {
            "standard_target": "4-6 lbs",
            "high_commitment_candidate": True,
            "high_commitment_target": "8 lbs",
        },
        language="en",
    )
    assert "High Commitment Track" in invitation
    assert "Standard Track" in invitation
    assert "8 lbs" in invitation


def test_english_stage_report_and_followup_templates():
    from app.services.document_templates import build_stage_report_content_json
    from app.services.stage_report import _stage_title, _fallback_stage_payload
    from app.services.archivist.followup import (
        _compose_document_checklist_followup_text,
        compose_milestone_followup,
    )

    assert _stage_title("day7", language="en") == "Day 7 Stage Progress Report"

    fallback = _fallback_stage_payload("day7", {}, [], language="en")
    assert "Recorded 0 active check-in days" in fallback["stage_summary"]
    assert "Morning C/Noon B/Evening A" in fallback["adjustment_advice"]

    report_json = build_stage_report_content_json(
        stage="day7",
        title="Day 7 Stage Progress Report",
        user_name="Alex",
        fields={
            "stage_summary": "Great compliance in Week 1.",
            "risk_reminder": "No adverse risks identified.",
            "adjustment_advice": "Maintain hydration.",
            "followup_advice": "Focus on evening routine.",
        },
        language="en",
    )
    assert report_json["blocks"][0]["title"] == "1. Stage Summary"
    assert report_json["blocks"][1]["title"] == "2. Risk Reminders"
    assert report_json["blocks"][2]["title"] == "3. Fine-tuning Recommendations"
    assert report_json["blocks"][3]["title"] == "4. Follow-up Action Plan"

    followup_text = _compose_document_checklist_followup_text(
        missing=[{"id": "c1", "label": "Product Protocol"}, {"id": "c2", "label": "Vegetables"}],
        language="en",
    )
    assert "Good morning! Just a quick follow-up" in followup_text
    assert "Product Protocol; Vegetables" in followup_text

    milestone_text = compose_milestone_followup(
        milestone_day=7,
        need_waist=True,
        need_weight=True,
        language="en",
    )
    assert "Tomorrow is Day 7 of your program!" in milestone_text
    assert "record your weight and measure your waist" in milestone_text


def test_imperial_units_weight_and_waist_conversion():
    from app.services.intake_facts import _parse_weight_kg, _normalize_waist_cm, _weight_kg_to_display
    from app.services.body_metrics import parse_weight_kg, waist_explicit_unit_scale

    # 165 lbs should be ~74.84 kg, NOT 165.0 kg
    assert _parse_weight_kg("165 lbs") == pytest.approx(74.84, 0.05)
    assert _parse_weight_kg("165 kg") == 165.0
    assert _parse_weight_kg("165斤") == 82.5
    assert _parse_weight_kg("165") == 165.0

    assert _normalize_waist_cm("28 inch") == pytest.approx(71.12, 0.05)
    assert _normalize_waist_cm("28 inches") == pytest.approx(71.12, 0.05)
    assert _normalize_waist_cm("70 cm") == 70.0
    # "I think 80" should NOT be matched as inches (not 80 * 2.54 = 203.2)
    assert _normalize_waist_cm("I think 80") == 80.0

    body_val = parse_weight_kg("165 lbs")
    assert body_val.ok is True
    assert body_val.value == pytest.approx(74.84, 0.05)

    assert waist_explicit_unit_scale("28 inch") == 2.54
    assert waist_explicit_unit_scale("I think 80") is None

    # 展示单位：中文按斤，英文直接用公斤（英语客户不等于美制客户，见 _jin_display 注释）
    assert _weight_kg_to_display(74.84, language="en") == "74.8 kg"
    assert _weight_kg_to_display(75.0, language="en") == "75 kg"
    assert _weight_kg_to_display(74.84, language="zh") == "149.7斤"


def test_english_deterministic_targets_are_native_english():
    """确定性目标必须按语言原生生成，不能靠正则把中文句子事后改写成英文。

    事后改写只换掉带名和「X斤」，"从入营起算累计""不预设累计斤数…" 原样留在
    英文客户的交付文档里，成了中英混排；而提示词又要求模型"照抄 intake_facts"，
    模型只能照抄中文或自己翻译换算。
    """

    from app.services.intake_facts import build_intake_fact_summary
    from app.services.questionnaire_template import get_questionnaire_fields

    fields_en = get_questionnaire_fields("en")
    fields_zh = get_questionnaire_fields("zh")
    answers = {
        "SIMPLE1774104038286mn0fqky6": "70 inches",
        "SIMPLE1774104087629mn0frn0t": "200 lbs",
        "SIMPLE1774104236120mn0futlk": "38 inches",
        "RADIO1774103553031mn0fg6ix": "Male",
        "SIMPLE1774103589114mn0fgyd6": "35",
    }

    facts_en = build_intake_fact_summary(fields_en, answers, language="en")
    facts_zh = build_intake_fact_summary(fields_zh, answers, language="zh")

    # 英文目标里不能残留任何中文
    for key in ("basic_target_jin_or_range", "challenge_target_jin_or_review_text", "target_generation_rule_applied"):
        assert not re.search(r"[一-鿿]", str(facts_en[key])), (key, facts_en[key])
    for value in facts_en["stage_targets"].values():
        assert not re.search(r"[一-鿿]", str(value)), value
    for value in facts_en["day28_targets"].values():
        assert not re.search(r"[一-鿿]", str(value)), value

    # 英文用 kg，中文用斤，但说的是同一个安全上限（1斤=0.5kg）
    assert "kg" in facts_en["day28_targets"]["base"]
    assert "lbs" not in facts_en["day28_targets"]["base"]
    assert "斤" in facts_zh["day28_targets"]["base"]
    assert facts_en["28_day_display_cap_jin"] == pytest.approx(
        facts_zh["28_day_display_cap_jin"] / 2, 0.01
    )

    # 带位标识仍是中文内部键：看板分类统计和阻塞判据都按它过滤
    assert facts_en["weight_loss_band"] == facts_zh["weight_loss_band"]
    # 缺失哨兵同样保持中文，由渲染层翻译
    assert facts_en["city"] == "未提供"


def test_english_overview_and_sentinels_render_in_english():
    """报告首屏的概览格和缺失占位符不能出中文。

    facts 里的"未提供""暂无数据"是跨模块哨兵值（看板分类统计、文档生成阻塞判据
    都按它们过滤），所以源头保持中文，必须由渲染层翻译。
    """

    from app.services.document_templates import _localize_band_and_units_en
    from app.services.intake_generation import _format_waist_bodyfat

    assert _format_waist_bodyfat("96.52", "未提供", language="en") == (
        "Waist: 96.52cm / Body fat: not provided"
    )
    # 中文行为不变
    assert _format_waist_bodyfat("96.52", "未提供", language="zh") == "腰围96.52cm / 体脂率未提供"

    for sentinel, expected in (
        ("未提供", "Not provided"),
        ("暂无数据", "No data yet"),
        ("需补充身高体重后判断", "To be assessed once height and weight are provided"),
        ("信息不足", "Insufficient information"),
    ):
        assert _localize_band_and_units_en(sentinel) == expected


def test_a_band_weight_number_guard_covers_imperial_units():
    """A带禁止出现减重数字的闸门必须认 lbs，否则对英文客户完全不设防。"""

    from app.services.intake_generation import _WEIGHT_NUMBER_PATTERN

    for text in ("28天减重6斤", "reduce 3 kg", "Lose 5 lbs in 28 days", "down 10 pounds", "目标 4公斤"):
        assert _WEIGHT_NUMBER_PATTERN.search(text), text
    for text in ("focus on posture and strength", "不设减重指标，以体态与力量提升为主"):
        assert not _WEIGHT_NUMBER_PATTERN.search(text), text


def test_imperial_height_conversion():
    """英文客户按本地习惯填英寸/英尺，必须换算成厘米。

    不换算的话 70 会被当成 70 厘米，BMI 算成 185，管理带、安全减重空间和
    28 天目标会整档算错，且生成后无法纠正。
    """

    from app.services.body_metrics import parse_height_cm
    from app.services.intake_facts import _normalize_height_cm

    # 英寸与英尺英寸的各种写法都要认
    for raw in ('70 inches', '70 in', '70"', "5'10\"", "5'10", "5 ft 10 in", "5ft10"):
        assert _normalize_height_cm(raw) == pytest.approx(177.8, 0.01), raw
    assert _normalize_height_cm("5 feet") == pytest.approx(152.4, 0.01)
    assert _normalize_height_cm("5.9 ft") == pytest.approx(179.8, 0.01)

    # 原有中文/公制行为不能变
    assert _normalize_height_cm("175") == 175.0
    assert _normalize_height_cm("175cm") == 175.0
    assert _normalize_height_cm("1.75") == 175.0
    assert _normalize_height_cm("1.75m") == 175.0

    # 无单位的裸数字不按语言猜英制：70 既可能是英寸也可能是笔误，
    # 保留原值让前端拦在提交前、让下游腰高比矛盾检查兜住，不静默换算。
    assert _normalize_height_cm("70") == 70.0
    assert _normalize_height_cm("") is None
    assert _normalize_height_cm("没量") is None

    # 换算不出可信值时保留原始数字，不能清空——身高缺失会直接拦掉文档生成
    assert _normalize_height_cm("500") == 500.0
    assert parse_height_cm("500").value is None

    # 英寸识别不能把英语介词当单位
    assert _normalize_height_cm("80 in the morning") == 80.0


def test_english_contradiction_followup_localization():
    from app.services.archivist.followup import compose_followup_message

    msg_en_chat = compose_followup_message(
        confirmations=[{"field": "weight", "chat_value": "165 lbs"}],
        language="en",
    )
    assert msg_en_chat is not None
    assert "By the way, is your weight 165 lbs?" in msg_en_chat

    msg_en_ask = compose_followup_message(
        confirmations=[{"field": "waist"}],
        language="en",
    )
    assert msg_en_ask is not None
    assert "By the way, what is your latest waist measurement?" in msg_en_ask


def test_english_document_template_localizes_bands_and_units():
    from app.services.document_templates import (
        _localize_band_and_units_en,
        _execution_goal_summary_block,
        _core_conclusions_block,
    )

    assert _localize_band_and_units_en("B带｜轻盈启动") == "Band B | Gentle Energy Kickstart"
    # 斤→kg 只是历史文档/模型没照抄时的兜底；1 斤 = 0.5 kg，换算无舍入误差
    assert _localize_band_and_units_en("4–6斤") == "2–3 kg"
    assert _localize_band_and_units_en("8斤") == "4 kg"
    assert _localize_band_and_units_en("9斤") == "4.5 kg"

    summary_block = _execution_goal_summary_block(
        {
            "weight_band": "B带｜轻盈启动",
            "base_goal": "4–6斤",
            "challenge_goal": "8斤",
            "commitment_rule": "85% check-in rate",
            "path_goal": "Long term vitality",
            "current_mainline": "Establish daily rhythm",
        },
        is_en=True,
    )
    assert "Band B | Gentle Energy Kickstart" in summary_block["content"]
    assert "2–3 kg" in summary_block["content"]
    assert "4 kg" in summary_block["content"]
    assert "B带" not in summary_block["content"]
    assert "斤" not in summary_block["content"]
