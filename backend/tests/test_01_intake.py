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
from app.services.document_service import _apply_intake_facts_to_document_payloads
from app.services.intake_facts import build_intake_fact_summary
from app.services.questionnaire_template import default_questionnaire_template


INTAKE_SYSTEM_PROMPT_PREFIX = "你是加速燃「高燃人生28天训练营」的首席能量教练"


def _make_intake_mock_response(*, weight_band: str = "维持优化带（A带）", bmi: str = "21.2") -> str:
    return json.dumps({
        "intake_analysis_package": {
            "shared_analysis": {
                "weight_band": weight_band,
                "core_problem": "晚睡压力导致的情绪性进食和执行波动",
                "day28_goal": "维持体重0-2斤，重点优化睡眠节律和晚餐收口",
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
                "day28_goal": "维持体重0-2斤，重点优化睡眠节律和晚餐收口",
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
                    "success_path": {
                        "intro": "12周成功路径，先稳定再微调",
                        "weight_band": weight_band,
                        "total_goal": "12周维持体重、优化腰围体态、重建稳定睡眠节律",
                        "day28_goal": "维持体重0-2斤，睡眠时间提前至23:00前",
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
                    "special_reminder": "您的体重已经在健康范围，不要追求更瘦。这28天的目标是让身体在高压白班下也能稳定运转。",
                    "first_week_signals": "前7天重点看4个信号：入睡时间、晚餐收口成功率、下午犯困程度、咖啡因需求变化",
                },
                "day28_execution_standard": {
                    "recipient": "蔡浩然",
                    "weight_band": weight_band,
                    "path_goal": "12周维持体重、优化腰围体态、重建稳定睡眠节律",
                    "day28_goal": "维持体重0-2斤，睡眠时间提前至23:00前，减少咖啡因依赖",
                    "current_phase_goal": "第1-4周以稳定节律和建立晚间收口为核心",
                    "principle": "先稳节律再抓减脂，第1-2周只抓睡眠和晚餐收口两个动作",
                    "weekly_plan": [
                        {"week": "第1周", "focus": "建立晚间收口和入睡时间的底线意识",
                         "required_actions": "晚餐后不再吃任何东西；22:30熄屏23:00前关灯",
                         "pass_standard": "7天中至少5天晚餐后成功收口"},
                        {"week": "第2周", "focus": "稳定睡眠节奏减少咖啡因依赖",
                         "required_actions": "继续维持晚间收口；午B从半份起",
                         "pass_standard": "7天中至少5天晚餐后收口"},
                        {"week": "第3周", "focus": "加入运动和饮食结构调整",
                         "required_actions": "每周3次30分钟快走；午餐主食控制在1拳内",
                         "pass_standard": "完成2次以上快走"},
                        {"week": "第4周", "focus": "整合节奏验证可持续性",
                         "required_actions": "综合执行前三周所有核心动作",
                         "pass_standard": "综合执行率>=70%"},
                    ],
                    "daily_checkin": (
                        "1. 今日是否完成早C/午B/晚A？（是/否）\n"
                        "2. 今日是否在23:30前上床？（是/否）\n"
                        "3. 今日蔬菜摄入是否达到2拳？（是/否）\n"
                        "4. 今日是否完成7000步或有氧30分钟？（是/否）\n"
                        "5. 今日情绪稳定度和饭后困综合评分（1-5分，1很差5很好）"
                    ),
                    "week5_12": "第5-8周在睡眠稳定后逐步拉高运动频率和饮食精细度。第9-12周进入维持模式。",
                    "weekly_standard": "每周通过标准：每周至少完成1次体重+腰围记录；晚间收口>=5天/周；23:00前入睡>=4天/周",
                    "reminder": "结果看连续性不看单日完美。如果今天回家晚了或者忍不住吃了夜宵不要破罐破摔，明天就是新的一天。",
                },
            },
        }
    })


@pytest.mark.asyncio
async def test_questionnaire_submit_creates_profile_and_documents(
    admin_client,
    db_session,
    mock_ai,
    cai_haoran_answers,
    wait_background,
):
    mock_ai._responses = {
        INTAKE_SYSTEM_PROMPT_PREFIX: _make_intake_mock_response(),
    }

    group_id = f"test-group-intake-{secrets.token_hex(6)}"
    resp = await admin_client.post("/service-cases", json={
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
    tags = (profile.profile_json or {}).get("tags") or []
    assert tags == ["A带", "晚睡习惯", "下午犯困"]
    personalized_fields = (profile.profile_json or {}).get("personalized_checkin_fields") or []
    assert len(personalized_fields) == 5

    # 内容准确性验证：Profile 字段与预期完全一致
    expected_labels = [
        "今日是否完成早C/午B/晚A？（是/否）",
        "今日是否在23:30前上床？（是/否）",
        "今日蔬菜摄入是否达到2拳？（是/否）",
        "今日是否完成7000步或有氧30分钟？（是/否）",
        "今日情绪稳定度和饭后困综合评分（1-5分，1很差5很好）",
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

    updated_case = await db_session.get(ServiceCase, case_id)
    assert updated_case is not None
    assert updated_case.status in (ServiceCaseStatus.REVIEWING, ServiceCaseStatus.IN_SERVICE)


@pytest.mark.asyncio
async def test_generating_status_on_submit(
    admin_client,
    mock_ai,
    cai_haoran_answers,
):
    """验证问卷提交后 status 被设置为 generating——用 AsyncMock 阻止后台任务执行，保持中间状态"""
    mock_ai._responses = {
        INTAKE_SYSTEM_PROMPT_PREFIX: _make_intake_mock_response(),
    }

    group_id = f"test-group-genstatus-{secrets.token_hex(6)}"
    resp = await admin_client.post("/service-cases", json={
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
    db_session,
    cai_haoran_answers,
):
    group_id = f"test-group-intake-outcome-{secrets.token_hex(6)}"
    resp = await admin_client.post("/service-cases", json={
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
async def test_personalized_checkin_uses_latest_document_daily_checkin_block(admin_client, db_session):
    group_id = f"test-personalized-checkin-{secrets.token_hex(6)}"
    resp = await admin_client.post("/service-cases", json={
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
                            "5. 今日情绪稳定度和饭后困综合评分（1-5分）"
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
    assert facts["weight_loss_band"] == "维持优化带（A带）", (
        f"Expected A带, got '{facts['weight_loss_band']}'"
    )


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
    resp = await admin_client.post("/service-cases", json={
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
