from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models.outcome import OutcomeEvent, OutcomeStageState
from app.models.questionnaire import QuestionnaireSubmission
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user_document import DocumentStatus, DocumentType, UserDocument
from app.models.user_profile import UserProfile
from app.services.document_service import (
    _apply_intake_facts_to_document_payloads,
    _intake_generation_system_prompt,
)
from app.services.document_delivery import _document_filename
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
    _apply_deterministic_band,
    _extract_safety_factors,
    _force_high_risk_manual_review,
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
                        "day28_goal": "保持健康体重，睡眠时间提前至23:00前",
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
                        "22:30手机熄屏，23:00前关灯",
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
                    "day28_goal": "保持健康体重，睡眠时间提前至23:00前，减少咖啡因依赖",
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
                         "acceptance_standard": "晚餐后不再吃任何东西；22:30熄屏23:00前关灯"},
                        {"week": "第2周", "core_task": "提高一致性",
                         "acceptance_standard": "继续维持晚间收口；午B从半份起"},
                        {"week": "第3周", "core_task": "抓结果指标",
                         "acceptance_standard": "每周3次30分钟快走；午餐主食控制在1拳内"},
                        {"week": "第4周", "core_task": "稳定复现",
                         "acceptance_standard": "综合执行前三周所有核心动作"},
                    ],
                    "daily_checkin": [
                        {"order": 1, "category": "product", "title": "个案产品排班是否按方案完成",
                         "answer_type": "yes_no", "options": ["是", "否"]},
                        {"order": 2, "category": "sleep", "title": "是否在23:30前上床",
                         "answer_type": "yes_no", "options": ["是", "否"]},
                        {"order": 3, "category": "diet", "title": "蔬菜总量是否达到2拳",
                         "answer_type": "yes_no", "options": ["是", "否"]},
                        {"order": 4, "category": "activity", "title": "是否完成7000步或有氧30分钟",
                         "answer_type": "yes_no", "options": ["是", "否"]},
                        {"order": 5, "category": "state", "title": "饭后困评分（1分很困、5分不困）",
                         "answer_type": "score_1_5", "options": ["1", "2", "3", "4", "5"]},
                    ],
                    "weekly_standard": "个案产品排班和晚间收口至少完成5天；完成计划运动；至少记录1次体重或围度/体感",
                    "adjust_stop_conditions": "持续心慌或睡眠明显恶化时暂停并联系班主任；胸痛、呼吸困难或明显不适时及时就医。",
                },
            },
        }
    })


def test_intake_prompt_requires_five_independent_daily_checkin_questions():
    prompt = _intake_generation_system_prompt()
    # 模型收到的是 system prompt + user prompt 里的字段结构，两段合起来才是完整指令。
    # daily_checkin 的结构规则单一来源在字段结构里，系统提示词只补出题判断，不再逐条复述。
    instructions = prompt + json.dumps(combined_prompt_schema(), ensure_ascii=False)

    # 五项打卡改为结构化数组：数量、序号、单一意图、题面污染、答题类型都要说清
    assert "唯一、连续、严格递增；数组长度必须正好5项，禁止4项或6项" in instructions
    assert "禁止写「每日固定打卡」「5项打卡」「打卡清单」等栏目标题或说明文字" in instructions
    assert "序号由程序按 order 渲染" in instructions
    assert (
        "今天/今日/今早/今晚/昨天/昨日/昨晚/当天/本日」等相对时间词"
    ) in instructions
    assert "也禁止一道题同时问时间和饮食结构" in instructions
    assert "只能取 yes_no / score_1_5 / number / text" in instructions
    assert "只能取 product / diet / activity / sleep / state" in instructions
    assert "蔬菜总量是否至少2拳" in instructions
    assert "今天是否至少吃到2拳蔬菜" not in instructions
    assert "今日是否心慌" not in instructions
    # 出题判断留在系统提示词
    assert "禁止所有用户都输出同一套固定五题" in prompt
    assert "恰好 1 项 category=activity" in prompt
    assert "至少2项设计成正向动作" in prompt
    assert "禁止把两个问题合并成一项来腾名额" in prompt
    assert "「情绪稳定度评分」与「饭后困评分」是两个独立候选问题" in prompt
    assert "若两项同时入选 daily_checkin，必须各占 1 项" in prompt
    assert "情绪稳定度与饭后困综合评分必须固定" not in prompt
    assert "情绪稳定度和饭后困综合评分（1-5）" not in prompt
    assert "只描述用户提交问卷之前的近3个月" in prompt

    # 管理带与人工复核彻底分开：内部标签不再是带位，也不得进入用户可见内容
    assert "weight_band 永远只能逐字复制系统算好的业务带位" in prompt
    assert "以上四个带位是 weight_band 的全部合法取值，没有第五种" in prompt
    assert "严禁把「需人工复核」「人工复核」「待复核」「red风险」「高风险」「风险等级」等内部标签写进 weight_band" in prompt
    assert "无法安全自动判带时 weight_band 写「需人工复核」" not in prompt
    assert "需人工复核：安全闸门未通过或资料冲突导致无法自动给出安全管理带" not in prompt
    # 安全减重空间是唯一分带主轴；执行困难和用户期待均不能改带
    assert "【证据职责分开】" in prompt
    assert "只由 intake_facts.safe_reduction_room_kg 按精确边界确定" in prompt
    assert "用户希望减多少斤、近3个月趋势和腰腹体感都不能在安全空间计算后改带" in prompt
    assert "不能仅因为晚睡、外卖多、精力差或运动少就把用户升到更高管理带" in prompt
    assert "模型不得凭BMI标签、腰腹体感、趋势或执行状态推翻系统已算好的 weight_loss_band" in prompt
    assert "2.5kg ≤ safe_reduction_room_kg < 5kg" in prompt
    assert "5kg ≤ safe_reduction_room_kg < 10kg" in prompt
    assert "safe_reduction_room_kg ≥ 10kg" in prompt
    assert "正常BMI也不能单独判A带" in prompt
    # 安全条件只强制人工复核，不改带位；风险已通过上调保护性下限折算进可管理空间
    assert "【安全条件不改带位，只强制复核】" in prompt
    assert "仍按安全减重空间正常得到 A-D 带" in prompt
    assert "不得为了凑带位把孕哺、疾病或用药用户包装成 A 带" in prompt
    assert "也不得因为标记了复核就不给带位" in prompt
    assert "带位随之下移是这一步的正常结果，不要再在带位上二次调整" in prompt
    # 曾短暂改成"红风险直接判A带"，已回退，不能留在提示词里自相矛盾
    assert "系统在计算带位时已直接判为 A带" not in prompt
    assert "A带同时承载" not in prompt

    # 12周阶段路径：客户 V2.1 §7.2-7.4 的各带累计区间与转段条件
    assert "【12周阶段路径】" in prompt
    assert "从入营当天起算的累计管理区间" in prompt
    assert "A带：第5-12周不设累计减重指标" in prompt
    assert "B带：第5-8周累计5-8斤" in prompt
    assert "第9-12周累计7-10斤" in prompt
    assert "C带：第5-8周累计8-12斤" in prompt
    assert "第9-12周累计10-16斤" in prompt
    assert "D带：第5-8周按第一个28天的实际结果、耐受度和肌肉保护情况继续推进" in prompt
    assert "第9-12周累计16-20斤左右" in prompt
    # 阶段累计受安全空间硬顶，但不受28天上限约束——用 day28 上限砍阶段区间会让它永远超限
    assert "任何阶段的累计目标都不得超过 safe_reduction_room_kg" in prompt
    assert "day28_goal_cap_kg 只约束第一个28天，不约束第5-8周和第9-12周的累计值" in prompt
    assert "A带在任何情况下都不得出现阶段减重数字" in prompt

    # 数字来源与趋势证据
    assert "第5-8周和第9-12周用【12周阶段路径】给出的累计区间且不突破 safe_reduction_room_kg" in prompt
    assert "除这两类来源外，任何位置都不得自拟数字" in prompt
    assert "严禁生成「腰围缩减1–3cm」「腰围少5cm」这类数字区间" in prompt
    # 问卷选项文字自带填写示例，模型曾把占位符替换成自己挑的数字（腰围缩小5cm以上）
    assert "括号里的 X 公斤、XX 厘米是问卷给用户看的填写格式示例，不是用户的答案" in prompt
    assert "严禁把示例里的占位符替换成一个自己挑的数字" in prompt
    assert "不只是 day28_goal" in prompt
    # A带不是“小幅减重带”，不允许用 0-2斤 等象征性区间安慰式落点
    assert "【A带的无数字目标】" in prompt
    assert "即使用户在问卷里明确填写了想减多少斤或公斤" in prompt
    assert "「0-2斤」「0–2斤」「1-2斤」「维持0-2斤波动」" in prompt
    assert "覆盖所有用户可见字段" in prompt
    assert "challenge_goal 固定写「不设置减重挑战目标」" in prompt
    assert "只能定性概括为「希望尽快看到体重和体态变化」等意图" in prompt
    assert "严禁原样复述具体数字" in prompt
    assert "A带的 core_conclusions.band_explanation 只能定性说明" in prompt
    assert "goal_response.content、user_goal 和 judgement 都不得原样复述" in prompt
    assert "不得把原数字改写成另一个更小的建议区间" in prompt
    assert "body_trend_direct_evidence（1条，字符串）" in prompt
    assert "body_trend_support_signals（1-2条，字符串数组）" in prompt
    assert "严禁单独用它们证明「正在上升」「正在下降」「持续变沉」" in prompt
    assert "「每餐」不得与蔬菜份量同句出现" in prompt

    # 诊断报告第6节只讲原因、第10节只讲动作，两节不得互相复述
    assert "priority_items 回答「为什么是这三件」，week_actions 回答「这周具体怎么做」" in prompt
    assert "priority_items 每项写成「问题 + 为什么它排在前面」" in prompt
    assert "严禁出现份量（拳/掌心/克）、次数、时长、具体时间点这类可量化执行参数" in prompt
    assert "week_actions 必须 4-5 条，每条带数字和时间或场景，且不解释原因" in prompt
    assert "禁止与 priority_items 逐条一一对应地复述" in prompt
    assert "至少 1 条来自产品节律 / 睡眠 / 情绪和环境设计" in prompt
    assert "「优先解决的三件事」只讲原因、「本周直接执行」只讲动作，两节不得互相复述" in prompt
    # 旧规则会主动要求两节对应，是重复的来源
    assert "至少 2 条 week_actions 要能对应 priority_items 中的前两项" not in prompt
    assert "本周执行严格3条" not in prompt

    # 一页硬预算
    assert "【一页硬预算】" in prompt
    assert "目标摘要只输出执行需要的两项" in prompt
    assert "current_mainline 必须逐字复制 shared_analysis.current_mainline" in prompt
    assert "diet_rules 最多3条，每条 ≤34字" in prompt
    assert "exercise_rules 最多2条，每条 ≤44字" in prompt

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
    assert "A带固定写不设置减重挑战目标" in energy_spec["goal_response"]["challenge_goal"]
    assert "diet_rules" in execution_spec
    assert "exercise_rules" in execution_spec
    # 分析性目标不再在执行建议书重复输出，避免顶到第二页
    assert "base_goal" not in execution_spec
    assert "challenge_goal" not in execution_spec
    assert "long_term_goal" not in execution_spec
    assert "weight_band" not in execution_spec

    daily_checkin_spec = execution_spec["daily_checkin"]
    assert isinstance(daily_checkin_spec, list) and len(daily_checkin_spec) == 1
    item_spec = daily_checkin_spec[0]
    assert set(item_spec) == {"order", "category", "title", "answer_type", "options"}
    assert "数组长度必须正好5项，禁止4项或6项" in item_spec["order"]
    assert "第1项固定 product，5项中必须有1项 activity" in item_spec["category"]
    assert "严禁合并成一项综合评分" in item_spec["title"]
    assert "禁止写「每餐蔬菜至少2拳」" in item_spec["title"]
    assert "只能取 yes_no / score_1_5 / number / text" in item_spec["answer_type"]

    fallback = _daily_checkin_text(None)
    assert "个案产品排班是否按方案完成" in fallback
    assert "饭后困评分（1分很困、5分不困）" in fallback
    assert "情绪稳定度和饭后困综合评分" not in fallback


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
        "14. 说明",
    ]
    # 本周直接执行放宽到最多5条，第4条不再被截断——参考设计要求3-5条覆盖三件事之外的维度
    assert "22:30手机熄屏，23:00前关灯" in diagnosis.content_md
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
    assert energy["overview"]["height_weight"] == "175cm / 65kg"
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


def test_execution_document_keeps_single_page_content_budget():
    """一页执行纸的内容预算：目标摘要2行、产品排班1行、饮食≤3条、运动≤2条。"""

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
            "base_goal": "基础目标不该出现在执行建议书",
            "challenge_goal": "挑战目标不该出现在执行建议书",
            "long_term_goal": "长期目标不该出现在执行建议书",
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
                     "answer_type": "yes_no", "options": ["是", "否"]},
                    {"order": 2, "category": "diet", "title": "蔬菜总量是否至少2拳",
                     "answer_type": "yes_no", "options": ["是", "否"]},
                    {"order": 3, "category": "activity", "title": "是否完成30分钟快走或7000步",
                     "answer_type": "yes_no", "options": ["是", "否"]},
                    {"order": 4, "category": "sleep", "title": "是否在23:30前闭眼",
                     "answer_type": "yes_no", "options": ["是", "否"]},
                    {"order": 5, "category": "state", "title": "饭后困评分（1分很困、5分不困）",
                     "answer_type": "score_1_5", "options": ["1", "2", "3", "4", "5"]},
                ],
                facts={"weight_kg": "78"},
            ),
        },
    )
    blocks = {block["key"]: block for block in content_json["blocks"]}

    goal_lines = blocks["goal_summary"]["content"].splitlines()
    assert len(goal_lines) == 2
    assert goal_lines[0].startswith("28天任务：")
    assert goal_lines[1].startswith("当前主线：")
    # 分析性目标与身体趋势只写在诊断报告，不在这份文件重复
    summary_text = blocks["goal_summary"]["content"]
    assert "基础目标" not in summary_text
    assert "挑战目标" not in summary_text
    assert "长期目标" not in summary_text
    assert "近3个月身体趋势" not in summary_text
    assert "管理带" not in summary_text

    # 产品排班压成一行，且不重复写「早C 早C」这种前缀
    product_content = blocks["product_schedule"]["content"]
    assert len(product_content.splitlines()) == 1
    assert "早C 早C" not in product_content
    assert product_content.count("｜") == 2

    assert len(blocks["diet_rules"]["items"]) == 3
    assert len(blocks["exercise_rules"]["items"]) == 2
    assert all(len(item) <= 34 for item in blocks["diet_rules"]["items"])
    assert all(len(item) <= 44 for item in blocks["exercise_rules"]["items"])

    # 结构化打卡定义随文档一起保存，H5 问卷才能拿到同一份 answer_type/options
    checkin_block = blocks["daily_checkin"]
    assert len(checkin_block["checkin_items"]) == 5
    assert [item["order"] for item in checkin_block["checkin_items"]] == [1, 2, 3, 4, 5]
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
        INTAKE_SYSTEM_PROMPT_PREFIX: _make_intake_mock_response(manual_review=True),
    }
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
            json={"answers": cai_haoran_answers},
        )
        assert submit_resp.status_code == 201, submit_resp.text
        await wait_background()

        profile = await db_session.scalar(
            select(UserProfile).where(UserProfile.service_case_id == case_id)
        )
        assert profile is not None
        # 模型即使把复核标签写成带位，最终仍使用安全减重空间算出的B带
        assert profile.profile_json["management_band"] == "B带｜轻盈启动"
        assert "复核" not in profile.profile_json["management_band"]
        # 内部字段仍然保留，供后台和人工复核使用
        assert profile.profile_json["manual_review_required"] is True
        assert profile.profile_json["safety_status"] == "manual_review"
        assert profile.profile_json["manual_review_reasons"] == [
            "问卷填写当前长期用药",
            "关键指标需医生确认后才能加码",
        ]
        assert profile.profile_json["provisional_band"] is True
        assert profile.profile_json["band_confidence"] == "low"

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
            assert internal_review["reasons"] == [
                "问卷填写当前长期用药",
                "关键指标需医生确认后才能加码",
            ]
            # 用户可见正文（blocks / content_md）一个内部标签都不能有
            rendered = json.dumps(document.content_json["blocks"], ensure_ascii=False)
            for label in ("需人工复核", "人工复核", "待复核", "red风险", "风险等级"):
                assert label not in rendered, f"{document.title} 的 blocks 泄漏了 {label}"
                assert label not in document.content_md, f"{document.title} 的正文泄漏了 {label}"
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
            document["manual_review_reasons"] == [
                "问卷填写当前长期用药",
                "关键指标需医生确认后才能加码",
            ]
            for document in pending
        )
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
):
    """孕期仍正常计算A-D；即使文档模型返回 manual_review=false，服务单高风险也必须点亮复核标记。

    这条同时覆盖了两条复核来源的分工：mock 的安全审阅员没识别出孕期
    （safety_review_required 为 false），标记仍由风控判定的 high 风险点亮。
    """

    mock_ai._responses = {
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
    # 问卷抽取这一路没识别出来，复核标记由服务单风险等级点亮。
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
        INTAKE_SYSTEM_PROMPT_PREFIX: _make_intake_mock_response(manual_review=True),
    }
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
        json={"answers": cai_haoran_answers},
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
    visible_text = (
        energy["core_conclusions"]["band_explanation"]
        + energy["goal_response"]["content"]
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
