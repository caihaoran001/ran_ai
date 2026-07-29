from __future__ import annotations

"""入营建档：LLM 生成 intake_analysis_package，并归一化画像/文档 payload、派生打卡字段。"""

import json
import logging
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


logger = logging.getLogger(__name__)

MANAGEMENT_BANDS = (
    "A带｜修复塑形",
    "B带｜轻盈启动",
    "C带｜明确减脂",
    "D带｜系统减重",
    "增肌增能带",
)

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


def _resolve_management_band(shared: dict[str, object], facts: dict[str, object]) -> str:
    """把管理带收敛为业务带位；内部复核标签不得占用带位。

    模型仍可能把「需人工复核」写进 weight_band（历史提示词遗留行为）。这种情况下
    回退到 intake_facts 里按 BMI 得出的确定性初判带，而不是把内部状态透给用户。
    「需补充身高体重后判断」是真实的数据缺失状态、不是内部标签，保持原样。
    """

    raw = str(shared.get("weight_band") or "").strip()
    if raw and not _contains_internal_review_label(raw):
        return raw
    return str(facts.get("weight_loss_band") or "").strip()


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
    # 管理带改由 LLM 综合判断（BMI 值降为兜底初判），但带位只能是业务带位：
    # 「需人工复核」是独立的内部安全状态，不占管理带；模型写成带位时回退到确定性初判。
    # 解析必须发生在文档归一化之前 → profile 与两份文档三处仍以单一来源对齐、天然一致。
    resolved_band = _resolve_management_band(shared, intake_facts)
    if resolved_band:
        shared["weight_band"] = resolved_band
        intake_facts["weight_loss_band"] = resolved_band
    document_payloads = _normalize_combined_document_payload(
        document_root,
        user_name,
        shared_analysis=shared,
    )
    if not document_payloads:
        raise RuntimeError("intake analysis package generated empty document content")
    _apply_intake_facts_to_document_payloads(document_payloads, intake_facts)
    # 人工复核是内部运营状态，不是管理带：即使模型仍把内部标签写进正文，也要在落库前清出用户可见内容。
    _strip_internal_review_labels(document_payloads)
    # 五项打卡在这里收敛成严格 5 项结构化数组，之后画像、执行文档和 H5 问卷都读同一份结构。
    _normalize_daily_checkin_in_payloads(document_payloads, intake_facts)
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
            "safety_status": "内部字段，只能取 standard / cautious / manual_review",
            "manual_review_required": "内部字段（布尔值）；孕哺、未成年人、明显疾病/用药/持续不适、非主动消瘦或资料冲突时为 true。"
            "它不是管理带，为 true 时 weight_band 仍要给正常业务带位",
            "manual_review_reasons": ["内部字段；需要人工复核的1-3条原因，只给运营看，禁止写进两份交付文件"],
            "weight_band": "综合判断后的最终业务管理带，只能取：A带｜修复塑形 / B带｜轻盈启动 / C带｜明确减脂 / D带｜系统减重 / 增肌增能带。"
            "禁止写「需人工复核」等内部标签",
            "band_confidence": "内部字段，high / medium / low；资料缺失或安全闸门触发时写 low",
            "provisional_band": "内部字段（布尔值）；带位为待人工确认的暂定结论时为 true",
            "band_basis": ["支持管理带的2-3条事实，只来自BMI/体重状态、腰围体脂或腰腹体感、近3个月直接趋势"],
            "body_trend": "仅依据入营问卷反映的近3个月趋势：正在改善 / 基本稳定 / 缓慢变沉 / 明显恶化 / 信息不足",
            "body_trend_summary": "一句话解释近3个月趋势；不得写入营后的变化",
            "body_trend_direct_evidence": "1条能证明变化方向的直接趋势事实，优先来自问卷「最近3个月体重变化」。"
            "当前BMI/体重/腰围是单点状态，不能放在这里；问卷没有前后对比信息时写明未提供",
            "body_trend_support_signals": ["1-2条当前状态支持信号：腰腹体感、睡眠、精力、食欲边界、晚间失控、活动量等"],
            "body_trend_confidence": "high / medium / low",
            "core_problem": "核心问题，只基于问卷事实",
            "current_mainline": "当前唯一主线，只能取：保肌减脂 / 节律稳定 / 睡眠修复 / 饮食结构修复 / 系统化管理；主要依据睡眠、精力、饮食、食欲边界、晚间失控、运动和恢复状态选择",
            "base_goal": "基础目标；用户没有明确数字时不得编造斤数；A带/增肌增能带禁止任何减重数字",
            "challenge_goal": "条件性挑战目标；A带/增肌增能带固定写不设置减重挑战目标",
            "long_term_goal": "长期方向，必须包含非体重结果；A带/增肌增能带禁止任何减重数字",
            "day28_goal": "28天目标；A带/增肌增能带只写体重稳定或恢复、体态、力量、睡眠、精力和执行目标，不写减重数字",
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
        profile["band_confidence"] = str(shared.get("band_confidence") or "").strip()
        profile["provisional_band"] = _boolean_value(shared.get("provisional_band"))
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
                "options": checkin_options_for(answer_type),
                "answer_type": answer_type,
                "category": str(item.get("category") or ""),
            }
        )
    return fields


DAILY_CHECKIN_SIZE = 5
CHECKIN_ANSWER_TYPES = ("yes_no", "score_1_5", "number", "text")
CHECKIN_CATEGORIES = ("product", "diet", "activity", "sleep", "state")
CHECKIN_YES_NO_OPTIONS = ["是", "否"]
CHECKIN_SCORE_OPTIONS = ["1", "2", "3", "4", "5"]
MAX_CHECKIN_TITLE_CHARS = 80

# 「问题的形状」标记：用来判断一条题面是不是真的能被回答，而不是栏目标题或说明文字。
# 这不是业务分类，只是把模型输出约束成可转成问卷字段的结构；类别一律由 category 字段承载。
_JUDGEMENT_MARKERS = ("是否", "有无", "有没有")
_SCORE_MARKERS = ("评分", "打分")
_REQUEST_MARKERS = ("请", "多少", "几点", "几次", "几分")
# 一条题面里出现两个及以上回答意图就是复合问题，必须拆开或只留优先级更高的那个。
_SECOND_INTENT_MARKERS = ("请记录", "请填写", "并填写", "并记录", "同时记录", "同时填写", "并写下")
# 并列连词把两个独立动作串成一题（如「20点前完成并且主食≤1拳」同时问时间和饮食结构），
# 必须拆开或只留优先级更高的一个。「或」是同一动作的多种达标方式，不算复合。
_COMPOSITE_CONJUNCTIONS = ("并且", "同时", "以及", "，并", ",并", "并且要", "而且")
# 栏目标题/说明文字，不是题目。这是唯一需要显式识别的形状，其余判断都靠上面的回答意图标记。
_CHECKIN_HEADING_MARKERS = ("每日固定打卡", "每日打卡", "打卡清单", "打卡内容", "打卡项", "项打卡", "打卡如下")
# 兜底题池是代码写死的默认五项，类别按 key 直接标注，不靠倒猜。
_FALLBACK_CHECKIN_CATEGORIES = {
    "personalized_weight": "state",
    "personalized_water": "diet",
    "personalized_sleep": "sleep",
    "personalized_diet": "diet",
    "personalized_exercise": "activity",
}
_RELATIVE_TIME_WORDS = ("今天", "今日", "今早", "今晚", "昨天", "昨日", "昨晚", "当天", "本日")


def _normalize_daily_checkin_in_payloads(
    payloads: dict[str, dict],
    facts: dict[str, object],
) -> None:
    execution = payloads.get(DocumentType.DAY28_EXECUTION_STANDARD.value)
    if not isinstance(execution, dict):
        return
    execution["daily_checkin"] = normalize_daily_checkin_items(
        execution.get("daily_checkin"),
        facts=facts,
    )


def normalize_daily_checkin_items(
    value: object,
    *,
    facts: dict[str, object] | None = None,
    strict_style: bool = True,
    allow_fallback: bool = True,
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
        item = _validated_checkin_item(candidate, strict_style=strict_style)
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
        for fallback in _fallback_checkin_items(facts or {}):
            key = _checkin_dedupe_key(str(fallback["title"]))
            if key in seen:
                continue
            seen.add(key)
            accepted.append(fallback)
            substituted.append(str(fallback["title"]))
            if len(accepted) >= DAILY_CHECKIN_SIZE:
                break

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


def _validated_checkin_item(candidate: object, *, strict_style: bool = True) -> dict[str, object] | None:
    if isinstance(candidate, dict):
        title = _clean_checkin_item_text(
            candidate.get("title") or candidate.get("label") or candidate.get("text")
        )
        answer_type = str(candidate.get("answer_type") or "").strip()
        category = str(candidate.get("category") or "").strip()
    else:
        title = _clean_checkin_item_text(candidate)
        answer_type = ""
        category = ""

    if not _is_valid_checkin_title(title, strict_style=strict_style):
        return None
    if answer_type not in CHECKIN_ANSWER_TYPES:
        answer_type = _infer_checkin_answer_type(title)
    if not _answer_type_matches_title(answer_type, title):
        return None
    return {
        "order": 0,
        "category": category if category in CHECKIN_CATEGORIES else _infer_checkin_category(answer_type),
        "title": title,
        "answer_type": answer_type,
        "options": checkin_options_for(answer_type),
    }


def _is_valid_checkin_title(title: str, *, strict_style: bool = True) -> bool:
    if not title or len(title) > MAX_CHECKIN_TITLE_CHARS:
        return False
    if any(marker in title for marker in _CHECKIN_HEADING_MARKERS):
        return False
    if strict_style and any(word in title for word in _RELATIVE_TIME_WORDS):
        return False
    # 分号说明这一项塞了两个独立句子，拆不开就丢弃。
    if "；" in title or ";" in title:
        return False
    if any(conjunction in title for conjunction in _COMPOSITE_CONJUNCTIONS):
        return False
    intents = sum(
        [
            any(marker in title for marker in _JUDGEMENT_MARKERS),
            any(marker in title for marker in _SCORE_MARKERS),
            any(marker in title for marker in _SECOND_INTENT_MARKERS),
        ]
    )
    return intents <= 1


def _answer_type_matches_title(answer_type: str, title: str) -> bool:
    """题面形状必须和答题类型对得上，否则转成问卷字段就会答非所问。"""

    if answer_type == "yes_no":
        return any(marker in title for marker in _JUDGEMENT_MARKERS)
    if answer_type == "score_1_5":
        return any(marker in title for marker in _SCORE_MARKERS)
    if answer_type == "number":
        # 填数字的题只要不和别的答题类型冲突就放行。曾经要求题面必须带单位括号，
        # 结果把「夜间醒来次数」这类完全合法的题判废、换成通用兜底题，白丢个性化。
        # 单位该不该写、写什么由提示词管（体重题必须写清 kg），代码不做事后审核也不猜。
        return not any(
            marker in title for marker in (*_JUDGEMENT_MARKERS, *_SCORE_MARKERS)
        )
    return _looks_like_question(title)


def _looks_like_question(title: str) -> bool:
    if title.endswith(("？", "?")):
        return True
    if "（" in title or "(" in title:
        return True
    return any(
        marker in title
        for marker in (*_JUDGEMENT_MARKERS, *_SCORE_MARKERS, *_REQUEST_MARKERS)
    )


def infer_checkin_answer_type(title: str) -> str:
    """按题面形状推断答题类型；只用于没有结构化定义的人工改写清单。"""

    return _infer_checkin_answer_type(title)


def _infer_checkin_answer_type(title: str) -> str:
    if any(marker in title for marker in _JUDGEMENT_MARKERS):
        return "yes_no"
    if any(marker in title for marker in _SCORE_MARKERS):
        return "score_1_5"
    if "（" in title or "(" in title:
        return "number"
    return "text"


def _infer_checkin_category(answer_type: str) -> str:
    """类别只由模型的 category 字段承载，不从题面或答题类型倒猜（猜出来一定是错的）。

    历史字符串格式没有这个字段，留空表示未知；消费方目前只用 category 做展示分组。
    """

    return ""


def checkin_options_for(answer_type: str) -> list[str]:
    if answer_type == "yes_no":
        return list(CHECKIN_YES_NO_OPTIONS)
    if answer_type == "score_1_5":
        return list(CHECKIN_SCORE_OPTIONS)
    return []


def checkin_field_type_for(answer_type: str) -> str:
    return "RADIO" if answer_type in {"yes_no", "score_1_5"} else "SIMPLE"


def _checkin_dedupe_key(title: str) -> str:
    return re.sub(r"[\s，,。！？!?；;：:（）()]+", "", title).casefold()


def _fallback_checkin_items(facts: dict[str, object]) -> list[dict[str, object]]:
    """确定性兜底：沿用默认五项个性化打卡（含按体重换算的饮水量），并去掉相对时间词。"""

    items: list[dict[str, object]] = []
    for field in _fallback_personalized_checkin_fields(facts):
        title = _clean_checkin_item_text(field.get("title") or field.get("label"))
        for word in _RELATIVE_TIME_WORDS:
            title = title.replace(word, "")
        title = title.strip(" ，,。")
        if not title:
            continue
        answer_type = "yes_no" if any(marker in title for marker in _JUDGEMENT_MARKERS) else "number"
        items.append(
            {
                "order": 0,
                # 兜底题是代码写死的，类别直接按 key 标注：这不是从题面倒猜模型的分类，
                # 而是我们自己定义的题本来就属于哪一类。
                "category": _FALLBACK_CHECKIN_CATEGORIES.get(str(field.get("key") or ""), ""),
                "title": title,
                "answer_type": answer_type,
                "options": checkin_options_for(answer_type),
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

    _sync_if_missing(core_conclusions, "management_band", shared.get("weight_band"))
    _sync_if_missing(core_conclusions, "band_explanation", _first_clean_item(shared.get("band_basis")))
    _sync_if_missing(core_conclusions, "body_trend", shared.get("body_trend"))
    _sync_if_missing(core_conclusions, "trend_explanation", shared.get("body_trend_summary"))
    shared_direct, shared_supports = _trend_evidence_parts(shared)
    _sync_if_missing(core_conclusions, "trend_direct_evidence", shared_direct)
    _sync_if_missing(core_conclusions, "trend_support_signals", shared_supports)
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
        (core_conclusions, "management_band", shared.get("weight_band")),
        (shared_path, "weight_band", shared.get("weight_band")),
        (goal_response, "base_goal", shared.get("base_goal")),
        (goal_response, "challenge_goal", shared.get("challenge_goal")),
        (shared_path, "day28_goal", shared.get("day28_goal")),
        (execution_payload, "day28_goal", shared.get("day28_goal")),
        (shared_path, "total_goal", shared.get("long_term_goal")),
        (execution_payload, "path_goal", shared.get("long_term_goal")),
        (execution_payload, "body_trend", shared.get("body_trend")),
        (core_conclusions, "day28_task", shared.get("day28_task")),
        (overview, "current_focus", shared.get("current_mainline")),
        (execution_payload, "current_mainline", shared.get("current_mainline")),
    ):
        _force_sync(target, key, source)
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
