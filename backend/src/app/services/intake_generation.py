from __future__ import annotations

"""入营建档：LLM 生成 intake_analysis_package，并归一化画像/文档 payload、派生打卡字段。"""

import json
import re

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.questionnaire import QuestionnaireSubmission
from app.models.service_case import ServiceCase
from app.models.user_document import DocumentType
from app.services.ai_service import AIService, parse_json_object
from app.services.document_templates import combined_prompt_schema
from app.services.intake_facts import build_intake_fact_summary, sanitize_profile_json
from app.services.checkin_questionnaires import personalized_checkin_fields_with_weight
from app.services.questionnaire_template import get_active_questionnaire_fields
from app.services.document_prompts import _intake_generation_system_prompt


async def _generate_intake_analysis_package(
    *,
    session: AsyncSession,
    ai: AIService,
    service_case: ServiceCase,
    user_name: str,
    submission: QuestionnaireSubmission,
) -> dict[str, object]:
    fields = await get_active_questionnaire_fields(session, "intake")
    readable_answers = _readable_answers(fields, submission.answers_json)
    intake_facts = build_intake_fact_summary(fields, submission.answers_json)
    text = await ai.generate_text(
        scenario="intake_doc",
        temperature=0.3,
        service_case_id=service_case.id,
        system_prompt=_intake_generation_system_prompt(),
        user_prompt=(
            f"当前用户：{user_name}\n"
            f"服务单：{service_case.case_no}\n\n"
            f"请严格按这个 package 顶层字段结构填值：\n{json.dumps(_intake_package_schema(), ensure_ascii=False, indent=2)}\n\n"
            f"intake_facts（共同事实摘要，优先级最高）：\n{json.dumps(intake_facts, ensure_ascii=False, indent=2)}\n\n"
            f"问卷结果：\n{readable_answers}"
        ),
    )
    payload = parse_json_object(text)
    if isinstance(payload.get("intake_analysis_package"), dict):
        payload = payload["intake_analysis_package"]
    shared = payload.get("shared_analysis") if isinstance(payload.get("shared_analysis"), dict) else {}
    document_root = payload.get("documents") if isinstance(payload.get("documents"), dict) else payload
    document_payloads = _normalize_combined_document_payload(
        document_root,
        user_name,
        shared_analysis=shared,
    )
    if not document_payloads:
        raise RuntimeError("intake analysis package generated empty document content")
    # 减重带改由 LLM 综合判断：用 package 里 LLM 判的带回填 intake_facts（BMI 值降为兜底初判）。
    # 后续强制同步逻辑保持不变 → profile 与两份文档三处仍以单一来源对齐、天然一致。
    _llm_band = str(shared.get("weight_band") or "").strip()
    if _llm_band:
        intake_facts["weight_loss_band"] = _llm_band
    _apply_intake_facts_to_document_payloads(document_payloads, intake_facts)
    profile_json = _normalize_profile_from_package(payload, document_payloads, intake_facts)
    summary = str(profile_json.get("summary") or _profile_summary_from_documents(document_payloads) or "")[:2000]
    if not summary:
        raise RuntimeError("intake analysis package generated empty profile content")
    return {
        "profile_json": profile_json,
        "summary": summary,
        "document_payloads": document_payloads,
        "intake_facts": intake_facts,
    }


def _intake_package_schema() -> dict[str, object]:
    return {
        "shared_analysis": {
            "safety_status": "只能取 standard / cautious / manual_review",
            "manual_review_required": "布尔值；孕哺、未成年人、明显疾病/用药/持续不适、非主动消瘦或资料冲突时为 true",
            "weight_band": "综合判断后的最终管理带：A带｜修复塑形 / B带｜轻盈启动 / C带｜明确减脂 / D带｜系统减重 / 增肌增能带 / 需人工复核",
            "band_basis": ["支持管理带的2-3条问卷事实或确定性指标"],
            "body_trend": "仅依据入营问卷反映的近3个月趋势：正在改善 / 基本稳定 / 缓慢变沉 / 明显恶化 / 信息不足",
            "body_trend_summary": "一句话解释近3个月趋势；不得写入营后的变化",
            "body_trend_evidence": ["支持趋势结论的2-3条问卷判断依据；信息不足时可为空"],
            "body_trend_confidence": "high / medium / low",
            "core_problem": "核心问题，只基于问卷事实",
            "base_goal": "基础目标；用户没有明确数字时不得编造斤数",
            "challenge_goal": "条件性挑战目标；不适合时写不设置",
            "long_term_goal": "长期方向，必须包含非体重结果",
            "day28_goal": "28天目标",
            "day28_task": "这28天先解决什么",
            "long_term_defense": "长期如何避免回到原来的上升或失控轨迹",
            "execution_principle": "执行总原则",
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
                "weight_loss_band": "与 shared_analysis.weight_band 一致（综合判断后的最终管理带）",
            },
            "state": "当前状态评估",
            "main_problem": "核心主要问题",
            "day28_goal": "28天目标",
            "risks": "只写问卷明确出现的健康/心理/医疗风险；无明确风险写暂无明确风险",
            "need_analysis": {
                "surface_needs": ["用户表面表达的需求，例如减重、睡好、精神好"],
                "deep_needs": ["表层需求背后的真实服务需求，只基于问卷事实推断"],
                "priority_need": "当前最优先满足的一个需求",
                "system_outputs": ["系统应交付的方案、动作、产品调优、陪跑或复盘方向"],
                "next_actions": ["接下来最该推进的1-3个动作"],
                "evidence_notes": ["对应的问卷证据，2-4条"],
            },
            "notes": "AI陪跑建议，供运营参考",
        },
        "documents": combined_prompt_schema(),
    }


def _normalize_profile_from_package(
    payload: dict,
    document_payloads: dict[str, dict],
    intake_facts: dict[str, object],
) -> dict[str, object]:
    profile = payload.get("profile") if isinstance(payload.get("profile"), dict) else {}
    if not profile:
        profile = _profile_from_document_payloads(document_payloads)
    shared = payload.get("shared_analysis") if isinstance(payload.get("shared_analysis"), dict) else {}
    if shared:
        profile.setdefault("main_problem", shared.get("core_problem"))
        profile.setdefault("day28_goal", shared.get("day28_goal"))
    profile = sanitize_profile_json(dict(profile), intake_facts)
    if shared:
        profile["safety_status"] = str(shared.get("safety_status") or "standard")
        profile["management_band"] = str(
            shared.get("weight_band")
            or intake_facts.get("weight_loss_band")
            or "需补充身高体重后判断"
        )
        profile["body_trend"] = str(shared.get("body_trend") or "信息不足")
        profile["body_trend_evidence"] = _clean_list(shared.get("body_trend_evidence"))
        profile["manual_review_required"] = _boolean_value(
            shared.get("manual_review_required")
        )
    profile.setdefault("risks", "暂无明确风险")
    profile["need_analysis"] = _normalize_need_analysis(profile.get("need_analysis"), intake_facts, shared)
    profile.setdefault("notes", "建议围绕问卷中明确的执行卡点进行轻量陪跑，避免一次性增加太多动作。")
    tags = payload.get("profile", {}).get("tags") if isinstance(payload.get("profile"), dict) else None
    profile["tags"] = _clean_list(tags) if isinstance(tags, list) else []
    profile["personalized_checkin_fields"] = (
        _personalized_checkin_fields_from_documents(document_payloads)
        or _fallback_personalized_checkin_fields(intake_facts)
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
        "risks": "暂无明确风险",
        "notes": _text_from_payload(energy.get("special_reminder")) or "建议围绕问卷中的核心执行卡点进行陪跑。",
    }


def _normalize_need_analysis(value: object, facts: dict[str, object], shared: dict[str, object]) -> dict[str, object]:
    """Keep the need engine structured even when the LLM omits optional fields.

    This is intentionally not a keyword classifier. It preserves the user's
    own stated needs and lets the generation prompt infer deeper needs from
    questionnaire evidence.
    """

    analysis = value if isinstance(value, dict) else {}
    main_needs = _split_need_text(facts.get("main_needs"))
    expectation = _split_need_text(facts.get("day28_expectation"))
    surface_needs = _clean_list(analysis.get("surface_needs")) or main_needs or expectation
    deep_needs = _clean_list(analysis.get("deep_needs"))
    outputs = _clean_list(analysis.get("system_outputs"))
    actions = _clean_list(analysis.get("next_actions"))
    evidence = _clean_list(analysis.get("evidence_notes"))

    return {
        "surface_needs": surface_needs or ["待补充"],
        "deep_needs": deep_needs or ["待结合聊天和打卡继续识别"],
        "priority_need": str(
            analysis.get("priority_need")
            or shared.get("core_problem")
            or facts.get("main_needs")
            or "待补充"
        ),
        "system_outputs": outputs or _clean_list(
            [
                shared.get("day28_goal"),
                shared.get("execution_principle"),
            ]
        ) or ["待根据方案生成结果补充"],
        "next_actions": actions or ["按已生成方案先完成入营前3天执行与反馈"],
        "evidence_notes": evidence or _clean_list(
            [
                f"服务需求：{facts.get('main_needs')}",
                f"28天期待：{facts.get('day28_expectation')}",
                f"执行担心：{facts.get('adherence_worry')}",
            ]
        ),
    }


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


def _fallback_personalized_checkin_fields(facts: dict[str, object]) -> list[dict[str, object]]:
    weight = _blank_if_missing(facts.get("weight_kg"))
    weight_kg = None
    if weight:
        try:
            weight_kg = float(weight)
        except (ValueError, TypeError):
            weight_kg = None
    return personalized_checkin_fields_with_weight(weight_kg)


def personalized_checkin_fields_from_document_payloads(document_payloads: dict[str, dict]) -> list[dict[str, object]]:
    return _personalized_checkin_fields_from_documents(document_payloads)


def _personalized_checkin_fields_from_documents(document_payloads: dict[str, dict]) -> list[dict[str, object]]:
    execution = document_payloads.get(DocumentType.DAY28_EXECUTION_STANDARD.value, {})
    if not isinstance(execution, dict):
        return []
    daily_items = _parse_daily_checkin_items(_daily_checkin_value_from_execution(execution))
    if not daily_items:
        return []
    fields = []
    for index, item in enumerate(daily_items[:5], start=1):
        field_type = "RADIO" if "是否" in item else "SIMPLE"
        fields.append(
            {
                "id": f"personalized_checkin_{index}",
                "key": f"personalized_checkin_{index}",
                "type": field_type,
                "title": item,
                "label": item,
                "subtitle": "",
                "required": True,
                "options": ["是", "否"] if field_type == "RADIO" else [],
            }
        )
    return fields


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
        value = block.get("content") or block.get("items") or block.get("value") or block.get("text")
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

    _sync_if_missing(core_conclusions, "management_band", shared.get("weight_band"))
    _sync_if_missing(core_conclusions, "band_explanation", _first_clean_item(shared.get("band_basis")))
    _sync_if_missing(core_conclusions, "body_trend", shared.get("body_trend"))
    _sync_if_missing(core_conclusions, "trend_explanation", shared.get("body_trend_summary"))
    _sync_if_missing(core_conclusions, "trend_evidence", shared.get("body_trend_evidence"))
    _sync_if_missing(core_conclusions, "day28_task", shared.get("day28_task"))
    _sync_if_missing(core_conclusions, "long_term_defense", shared.get("long_term_defense"))
    if core_conclusions:
        energy_payload["core_conclusions"] = core_conclusions

    _sync_if_missing(goal_response, "base_goal", shared.get("base_goal"))
    _sync_if_missing(goal_response, "challenge_goal", shared.get("challenge_goal"))
    if goal_response:
        energy_payload["goal_response"] = goal_response

    _sync_if_missing(shared_path, "weight_band", shared.get("weight_band"))
    _sync_if_missing(shared_path, "total_goal", shared.get("long_term_goal"))
    _sync_if_missing(shared_path, "day28_goal", shared.get("day28_goal"))
    _sync_if_missing(execution_payload, "weight_band", shared_path.get("weight_band"))
    _sync_if_missing(execution_payload, "path_goal", shared_path.get("total_goal"))
    _sync_if_missing(execution_payload, "day28_goal", shared_path.get("day28_goal"))
    _sync_if_missing(execution_payload, "body_trend", shared.get("body_trend"))
    _sync_if_missing(execution_payload, "base_goal", shared.get("base_goal"))
    _sync_if_missing(execution_payload, "challenge_goal", shared.get("challenge_goal"))
    _sync_if_missing(execution_payload, "long_term_goal", shared.get("long_term_goal"))
    _sync_if_missing(execution_payload, "current_mainline", energy_payload.get("priority_direction"))
    _sync_if_missing(execution_payload, "principle", shared.get("execution_principle"))
    _sync_if_missing(
        execution_payload,
        "product_schedule",
        energy_payload.get("product_schedule"),
    )
    _sync_if_missing(shared_path, "weight_band", execution_payload.get("weight_band"))
    _sync_if_missing(shared_path, "total_goal", execution_payload.get("path_goal"))
    _sync_if_missing(shared_path, "day28_goal", execution_payload.get("day28_goal"))
    if shared_path:
        energy_payload["success_path"] = shared_path
    if _flatten_payload_text_length(energy_payload) < 80 or _flatten_payload_text_length(execution_payload) < 50:
        return {}
    return {energy_key: energy_payload, execution_key: execution_payload}


def _first_clean_item(value: object) -> str:
    if isinstance(value, list):
        for item in value:
            text = str(item or "").strip()
            if text:
                return text
    return str(value or "").strip() if isinstance(value, str) else ""


def _apply_intake_facts_to_document_payloads(payloads: dict[str, dict], facts: dict[str, object]) -> None:
    for document_type, payload in list(payloads.items()):
        if isinstance(payload, dict):
            try:
                enum_value = DocumentType(document_type)
            except ValueError:
                continue
            _apply_intake_facts_to_single_payload(payload, enum_value, facts)


def _apply_intake_facts_to_single_payload(
    payload: dict[str, object],
    document_type: DocumentType,
    facts: dict[str, object],
) -> None:
    weight_band = str(facts.get("weight_loss_band") or "").strip()
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
            overview["bmi"] = bmi or str(overview.get("bmi") or "暂无数据")
            overview["height_weight"] = _join_fact_values(
                _with_unit(facts.get("height_cm"), "cm"),
                _with_unit(facts.get("weight_kg"), "kg"),
            ) or overview.get("height_weight")
            overview["waist_bodyfat"] = _join_fact_values(
                _blank_if_missing(facts.get("waist")),
                _blank_if_missing(facts.get("bodyfat")),
            ) or overview.get("waist_bodyfat")
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


def _with_unit(value: object, unit: str) -> str:
    text = _blank_if_missing(value)
    if not text:
        return ""
    return text if text.endswith(unit) else f"{text}{unit}"


def _sync_if_missing(target: dict, key: str, value: object) -> None:
    if not target.get(key) and value:
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
