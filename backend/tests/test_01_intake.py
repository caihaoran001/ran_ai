from __future__ import annotations

import json
import secrets
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models.outcome import OutcomeEvent, OutcomeStageState
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user_document import DocumentType, UserDocument
from app.models.user_profile import UserProfile
from app.services.document_service import (
    _apply_intake_facts_to_document_payloads,
    _intake_generation_system_prompt,
)
from app.services.document_delivery import _document_filename
from app.services.document_templates import _daily_checkin_text, combined_prompt_schema
from app.services.intake_facts import _weight_loss_band, build_intake_fact_summary
from app.services.questionnaire_template import default_questionnaire_template


INTAKE_SYSTEM_PROMPT_PREFIX = "你是加速燃「高燃人生28天训练营」的首席能量教练"


def _make_intake_mock_response(*, weight_band: str = "A带｜修复塑形", bmi: str = "21.2") -> str:
    return json.dumps({
        "intake_analysis_package": {
            "shared_analysis": {
                "safety_status": "standard",
                "manual_review_required": False,
                "weight_band": weight_band,
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
                    "special_reminder": "您的体重已经在健康范围，不要追求更瘦。这28天的目标是让身体在高压白班下也能稳定运转。",
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
                    "daily_checkin": (
                        "1. 个案产品排班是否按方案完成？（是/否）\n"
                        "2. 是否在23:30前上床？（是/否）\n"
                        "3. 蔬菜总量是否达到2拳？（是/否）\n"
                        "4. 是否完成7000步或有氧30分钟？（是/否）\n"
                        "5. 饭后困评分（1-5分，1很困5分不困）"
                    ),
                    "weekly_standard": "个案产品排班和晚间收口至少完成5天；完成计划运动；至少记录1次体重或围度/体感",
                    "adjust_stop_conditions": "持续心慌或睡眠明显恶化时暂停并联系班主任；胸痛、呼吸困难或明显不适时及时就医。",
                },
            },
        }
    })


def test_intake_prompt_requires_five_independent_daily_checkin_questions():
    prompt = _intake_generation_system_prompt()

    assert (
        "daily_checkin 必须严格输出5项且每项只能对应一个独立回答意图："
        "第1项固定为个案产品排班完成情况；"
        "如果方案要求减量、前移或暂停，第1项必须按该方案写，禁止继续要求早C/午B/晚A全部完成。"
        "后4项围绕用户具体卡点定制；"
        "候选问题超过5个时只保留优先级最高的5个并舍弃其余，禁止合并多个问题，"
        "尤其禁止「是否……？请记录……」「是否……并填写……」「是否……同时记录……」等把判断题和填空/评分题塞进同一项的句式。"
    ) in prompt
    assert (
        "daily_checkin 的5项题面一律禁止包含「"
        "今天/今日/今早/今晚/昨天/昨日/昨晚/当天/本日」等相对时间词"
    ) in prompt
    assert "蔬菜总量是否至少2拳" in prompt
    assert "今天是否至少吃到2拳蔬菜" not in prompt
    assert "今日是否心慌" not in prompt
    assert "「情绪稳定度评分」与「饭后困评分」是两个独立候选问题" in prompt
    assert "若两项同时入选 daily_checkin，必须各占 1 项" in prompt
    assert "情绪稳定度与饭后困综合评分必须固定" not in prompt
    assert "情绪稳定度和饭后困综合评分（1-5）" not in prompt
    assert "只描述用户提交问卷之前的近3个月" in prompt
    assert "不得为了匹配管理带而编造固定斤数" in prompt
    assert "不得为了凑 A–D 把医疗复核用户包装成 A 带" in prompt

    schema = combined_prompt_schema()
    energy_spec = schema[DocumentType.ENERGY_RESTART_PRESCRIPTION.value]
    execution_spec = schema[DocumentType.DAY28_EXECUTION_STANDARD.value]
    assert "core_conclusions" in energy_spec
    assert "body_trend" in energy_spec["core_conclusions"]
    assert "diet_rules" in execution_spec
    assert "exercise_rules" in execution_spec

    daily_checkin_spec = execution_spec["daily_checkin"]
    assert "情绪稳定度评分和饭后困评分是两个独立候选问题" in daily_checkin_spec
    assert "两项同时入选时各占1项" in daily_checkin_spec
    assert "必须合并为1项" not in daily_checkin_spec
    assert "禁止继续要求三杯全部完成" in daily_checkin_spec

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

    # 内容准确性验证：Profile 字段与预期完全一致
    expected_labels = [
        "个案产品排班是否按方案完成？（是/否）",
        "是否在23:30前上床？（是/否）",
        "蔬菜总量是否达到2拳？（是/否）",
        "是否完成7000步或有氧30分钟？（是/否）",
        "饭后困评分（1-5分，1很困5分不困）",
    ]
    for i, (pf, expected) in enumerate(zip(personalized_fields, expected_labels)):
        assert pf["label"] == expected, f"第 {i+1} 项标签不一致"
        if "是否" in expected:
            assert pf["type"] == "RADIO"
            assert pf["options"] == ["是", "否"]
        else:
            assert pf["type"] == "SIMPLE"

    # 验证打卡链接 API 返回与 Profile 一致
    resp = await admin_client.get(f"/questionnaires/{token}?template=personalized_checkin")
    assert resp.status_code == 200
    api_fields = resp.json()["fields"]
    assert len(api_fields) == 5
    for i, (af, pf) in enumerate(zip(api_fields, personalized_fields)):
        assert af["label"] == pf["label"], f"API 第 {i+1} 项与 Profile 不一致"
        assert af["type"] == pf["type"]

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
    assert fields[0]["title"] == "今日是否完成早C/午B/晚A？（是/否）"
    assert fields[0]["type"] == "RADIO"
    assert fields[4]["type"] == "SIMPLE"
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
