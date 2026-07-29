from __future__ import annotations

import json
import logging
import secrets
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models.outcome import OutcomeEvent, OutcomeStageState
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
    _normalize_combined_document_payload,
    checkin_options_for,
    normalize_daily_checkin_items,
)
from app.services.intake_facts import _weight_loss_band, build_intake_fact_summary
from app.services.questionnaire_template import default_questionnaire_template


INTAKE_SYSTEM_PROMPT_PREFIX = "你是加速燃「高燃人生28天训练营」的首席能量教练"


def _make_intake_mock_response(
    *,
    weight_band: str = "A带｜修复塑形",
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
                    "BMI 21.2 → 当前处于健康体重范围",
                    "最近3个月体重基本稳定 → 暂无继续上升信号",
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
                    "30岁男性，BMI 21.2（维持优化带），白班作息，主要挑战是晚睡压力导致的"
                    "情绪性进食和执行波动。当前体重健康，核心方向不是减重而是重建稳定节律"
                    "和减少晚间失控。"
                ),
                "tags": ["A带", "晚睡习惯", "下午犯困"],
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
    assert "manual_review_required=true 时 weight_band 仍必须输出正常业务带位" in prompt
    assert "以上五个带位是 weight_band 的全部合法取值，没有第六种" in prompt
    assert "严禁把「需人工复核」「人工复核」「待复核」「red风险」「高风险」「风险等级」等内部标签写进 weight_band" in prompt
    assert "无法安全自动判带时 weight_band 写「需人工复核」" not in prompt
    assert "需人工复核：安全闸门未通过或资料冲突导致无法自动给出安全管理带" not in prompt

    # 数字来源与趋势证据
    assert "都必须能追溯到问卷里用户自己写下的明确数字答案" in prompt
    assert "严禁生成「腰围缩减1–3cm」「腰围少5cm」这类数字区间" in prompt
    # 问卷选项文字自带填写示例，模型曾把占位符替换成自己挑的数字（腰围缩小5cm以上）
    assert "括号里的 X 公斤、XX 厘米是问卷给用户看的填写格式示例，不是用户的答案" in prompt
    assert "严禁把示例里的占位符替换成一个自己挑的数字" in prompt
    assert "不只是 day28_goal" in prompt
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
    assert "目标摘要只输出执行需要的三项" in prompt
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
    assert "diet_rules" in execution_spec
    assert "exercise_rules" in execution_spec
    # 分析性目标不再在执行建议书重复输出，避免顶到第二页
    assert "base_goal" not in execution_spec
    assert "challenge_goal" not in execution_spec
    assert "long_term_goal" not in execution_spec

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
    assert profile.profile_json.get("management_band") == "A带｜修复塑形"
    assert profile.profile_json.get("body_trend") == "基本稳定"
    assert profile.profile_json.get("manual_review_required") is False
    tags = (profile.profile_json or {}).get("tags") or []
    assert tags == ["A带", "晚睡习惯", "下午犯困"]
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
    assert facts["weight_loss_band"] == "A带｜修复塑形", (
        f"Expected A带, got '{facts['weight_loss_band']}'"
    )


@pytest.mark.parametrize(
    ("bmi", "expected"),
    [
        (21.2, "A带｜修复塑形"),
        (26.0, "B带｜轻盈启动"),
        (30.0, "C带｜明确减脂"),
        (33.0, "D带｜系统减重"),
    ],
)
def test_bmi_initial_reference_uses_new_management_band_names(
    bmi: float,
    expected: str,
):
    assert _weight_loss_band(bmi) == expected


@pytest.mark.asyncio
async def test_intake_facts_expose_safe_reduction_room(cai_haoran_answers):
    """安全余量三项：目标的天花板，175cm/65kg → 下限 61.25kg、空间 3.75kg、28天上限 3.25kg。"""

    template = default_questionnaire_template("intake")
    facts = build_intake_fact_summary(template.get("fields", []), cai_haoran_answers)

    assert facts["protected_lower_weight_kg"] == "61.2"
    assert facts["safe_reduction_room_kg"] == "3.8"
    # min(65*5%=3.25, 4, 3.75) → 基线5% 起作用
    assert facts["day28_max_loss_kg"] == "3.2"


def test_intake_facts_safe_room_missing_without_height_or_weight():
    facts = build_intake_fact_summary(
        [{"key": "h", "title": "您的身高（cm/厘米）"}],
        {"h": "暂未测量"},
    )

    assert facts["protected_lower_weight_kg"] == "暂无数据"
    assert facts["safe_reduction_room_kg"] == "暂无数据"
    assert facts["day28_max_loss_kg"] == "暂无数据"


def test_intake_facts_normalize_height_written_in_meters():
    """身高填「1.75」时不再把 BMI 算成 20 万——否则会被覆写到交付文件每一处 BMI。"""

    fields = [
        {"key": "h", "title": "您的身高（cm/厘米）"},
        {"key": "w", "title": "您当前体重（kg/公斤）"},
    ]
    facts = build_intake_fact_summary(fields, {"h": "1.75", "w": "65"})

    assert facts["height_cm"] == "175"
    assert facts["bmi"] == "21.2"
    assert facts["weight_loss_band"] == "A带｜修复塑形"


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
    """一页执行纸的内容预算：目标摘要3行、产品排班1行、饮食≤3条、运动≤2条。"""

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
    assert len(goal_lines) == 3
    assert goal_lines[0].startswith("当前管理带：")
    assert goal_lines[1].startswith("28天任务：")
    assert goal_lines[2].startswith("当前主线：")
    # 分析性目标与身体趋势只写在诊断报告，不在这份文件重复
    summary_text = blocks["goal_summary"]["content"]
    assert "基础目标" not in summary_text
    assert "挑战目标" not in summary_text
    assert "长期目标" not in summary_text
    assert "近3个月身体趋势" not in summary_text

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
        # 管理带回退成按 BMI 得出的业务带位，「需人工复核」不占带位
        assert profile.profile_json["management_band"] == "A带｜修复塑形"
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
    """28天任务、总路径目标、身体趋势以 shared_analysis 为唯一来源强制覆写。

    真实模型会在两份文件里各写一遍同一件事，写成同义改写（「腰腹缩围和精力转折」
    对「腰腹围度收紧和精力转向」），既冗余又会带数字漂移。这里用观测到的真实
    漂移文本当输入，确认两份文件落地后逐字一致。
    """

    shared = {
        "day28_goal": "用均衡餐盘替代原高碳结构，收住晚餐后进食，每日温和活动，实现腰腹缩围和精力转折",
        "long_term_goal": "12周建立可长期复制的节律，腰腹围度持续收紧",
        "body_trend": "缓慢变沉",
        "day28_task": "先把晚餐收口和餐盘结构稳定下来",
    }
    payload = {
        DocumentType.ENERGY_RESTART_PRESCRIPTION.value: {
            "one_sentence": "一句话结论" * 20,
            "core_conclusions": {"management_band": "C带｜明确减脂", "day28_task": "先抓晚间收口"},
            "success_path": {
                "day28_goal": "用均衡餐盘取代高碳结构，三餐收口并启动每日温和活动，实现腰腹围度收紧和精力转向",
                "total_goal": "12周把节律固定下来，围度继续下行",
            },
        },
        DocumentType.DAY28_EXECUTION_STANDARD.value: {
            "day28_goal": "均衡餐盘加晚间收口，配合每日温和活动，让腰腹围度和精力一起转向",
            "path_goal": "12周形成稳定节律，围度稳步收紧",
            "body_trend": "基本稳定",
            "principle": "先守底盘，再抓结果" * 5,
        },
    }

    normalized = _normalize_combined_document_payload(payload, "单一来源用户", shared_analysis=shared)

    energy = normalized[DocumentType.ENERGY_RESTART_PRESCRIPTION.value]
    execution = normalized[DocumentType.DAY28_EXECUTION_STANDARD.value]
    assert energy["success_path"]["day28_goal"] == shared["day28_goal"]
    assert execution["day28_goal"] == shared["day28_goal"]
    assert energy["success_path"]["total_goal"] == shared["long_term_goal"]
    assert execution["path_goal"] == shared["long_term_goal"]
    # 趋势不能一份写「缓慢变沉」另一份写「基本稳定」
    assert execution["body_trend"] == shared["body_trend"]
    assert energy["core_conclusions"]["day28_task"] == shared["day28_task"]


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
