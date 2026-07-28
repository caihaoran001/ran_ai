from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.models.user_document import DocumentType


ENERGY_FIELD_SPEC = {
    "recipient": "称呼，例如 易车汇 宝宝 或 蔡浩然",
    "overview": {
        "name": "用户姓名",
        "gender_age": "性别 / 年龄",
        "height_weight": "身高 / 体重",
        "bmi": "BMI 与阶段",
        "waist_bodyfat": "腰围 / 体脂率",
        "recent_change": "最近3个月变化",
        "schedule": "作息状态",
        "exercise": "运动基础",
        "main_need": "主要诉求",
        "current_focus": "当前主线",
    },
    "one_sentence": "一句话结论",
    "core_conclusions": {
        "management_band": "管理带，格式为「A带｜修复塑形 / B带｜轻盈启动 / C带｜明确减脂 / D带｜系统减重 / 需人工复核」",
        "band_explanation": "用一句用户能听懂的话解释为什么属于这个管理带",
        "body_trend": "入营问卷反映的近3个月身体趋势，只能取：正在改善 / 基本稳定 / 缓慢变沉 / 明显恶化 / 信息不足",
        "trend_explanation": "用一句话解释近3个月趋势；信息不足时明确无法判断，不得补编长期变化",
        "trend_evidence": ["支持趋势结论的2-3条问卷判断依据，只能来自本次入营问卷或确定性计算指标"],
        "day28_task": "这28天先解决什么，用一句话说清",
        "long_term_defense": "长期如何避免回到原来的上升或失控轨迹，用一句话说清",
    },
    "goal_response": {
        "goal_type": "specific 或 general。specific=用户写了明确数字/明确结果；general=用户只勾选了期待变化或泛化诉求",
        "judgement_type": "reasonable 或 needs_adjustment。needs_adjustment=目标过激、当前不建议或需先安全复核",
        "title": "固定写「3. 对您目标的直接回应」。这是固定章节标题，不要改成「本次优先改善方向」等其他标题。",
        "content": "直接面向用户的一段完整回应。需要包含用户目标/期待、判断、基础目标、挑战目标或当前更适合方向、冲刺/下调条件；不要空泛，不承诺结果。",
        "user_goal": "兼容字段：复述用户原始目标或期待变化",
        "judgement": "兼容字段：直接回应目标是否适合当前阶段",
        "base_goal": "兼容字段：基础目标",
        "challenge_goal": "兼容字段：挑战目标",
        "conditions": "兼容字段：冲刺条件或下调条件",
    },
    "judgement_points": ["为什么这样判断，5条以内"],
    "current_state": "当前身体状态真正该关注什么",
    "insulin_resistance": "食欲边界、饭后困、腰腹堆积等生活方式信号。保留兼容字段名，但用户可见内容不得写成胰岛素抵抗诊断。",
    "leptin_sensitivity": "饱腹感、晚间失控、食欲边界等生活方式信号。保留兼容字段名，但用户可见内容不得写成瘦素抵抗诊断。",
    "hormone_stage": "年龄、睡眠、压力、恢复能力相关影响。保留兼容字段名，但用户可见内容不得写成内分泌或激素诊断。",
    "priority_direction": "当前最该先抓的方向",
    "priority_items": ["真正需要优先解决的三件事，严格3项，按优先级排列"],
    "success_path": {
        "intro": "成功路径总述",
        "weight_band": "当前管理带",
        "total_goal": "总路径目标",
        "day28_goal": "第一个28天目标",
        "week5_8": "第5-8周目标",
        "week9_12": "第9-12周目标",
    },
    "module_diagnosis": {
        "rhythm": "节律",
        "diet": "饮食",
        "sleep": "睡眠",
        "pressure": "压力",
        "activity": "身体活动",
    },
    "expected_changes": {
        "day7": "前7天变化",
        "day14_28": "第14-28天变化",
    },
    "week_actions": ["本周直接执行，固定3条。至少包含1条运动建议，且运动建议必须写出类型+频率+时长+禁忌时间窗口"],
    "product_schedule": {
        "morning_c": "早C",
        "noon_b": "午B",
        "night_a": "晚A",
        "summary": "产品排班总结；必须服从个案安全、耐受和暂停结论",
    },
    "product_meaning": "说明饮食和运动本身有效，产品只用于帮助执行和稳定节律，不是减重捷径，也不替代医疗建议",
    "special_reminder": "特别提醒，包含必要就医/检测提示",
    "missing_information": ["仍缺失或需要补测的信息；没有则输出空列表"],
}


EXECUTION_FIELD_SPEC = {
    "weight_band": "当前管理带，格式为「A带｜修复塑形 / B带｜轻盈启动 / C带｜明确减脂 / D带｜系统减重 / 需人工复核」",
    "body_trend": "入营问卷反映的近3个月身体趋势：正在改善 / 基本稳定 / 缓慢变沉 / 明显恶化 / 信息不足",
    "base_goal": "基础目标；用户没有明确数字目标时不得编造斤数",
    "challenge_goal": "条件性挑战目标；不适合冲刺、风险较高或资料不足时写不设置",
    "long_term_goal": "长期方向，包含体重之外的腰围、体态、力量、睡眠或精力目标",
    "current_mainline": "当前唯一主线，例如保肌减脂、节律稳定、睡眠修复、饮食结构修复、系统化管理",
    "path_goal": "更适合的12周总路径目标。只在用户明确提出数字目标且资料支持时使用数字；否则用体重趋势和非体重结果表达，不得机械套固定斤数。",
    "day28_goal": "第一个28天目标，1-2句完整描述。必须同时包含阶段任务和观察结果；没有明确数字目标时不得编造斤数。",
    "current_phase_goal": "当前阶段目标，用「先解决{核心问题}，不是{反面}」句式，聚焦排序而非平铺罗列，例如「先解决节律稳定，不是追求失控式掉秤」。",
    "principle": "执行总原则，保持两段递进结构「先守{底盘动作}，再{放大动作}」，是通用指导语，不嵌入具体参数（时间点、拳数）。例如「先守底盘，再抓结果；先连续完成，再逐步加码」。",
    "product_schedule": {
        "morning_c": "早C个案剂量与时间；需要暂停时明确写暂停",
        "noon_b": "午B个案剂量与时间；需要减量、前移或暂停时明确写",
        "night_a": "晚A个案剂量与时间；需要暂停时明确写",
    },
    "diet_rules": ["对应用户真实生活场景的饮食规则，最多4条"],
    "exercise_rules": ["运动频率、单次时长、强度、动作和替代方案，最多3条"],
    "weekly_plan": [
        {"week": "第1周", "core_task": "稳住底盘", "acceptance_standard": "1个短句，例如：按个案产品排班完成；晚餐主食≤1拳；完成计划内活动。"},
        {"week": "第2周", "core_task": "提高一致性", "acceptance_standard": "1个短句，例如：连续5天不漏早餐；晚餐后走15–20分钟；应酬日不加宵夜。"},
        {"week": "第3周", "core_task": "抓结果指标", "acceptance_standard": "1个短句，例如：每周2次轻力量；记录腰围和体重。"},
        {"week": "第4周", "core_task": "稳定复现", "acceptance_standard": "1个短句，例如：把睡前收口、计划内活动和2次轻力量做满。"},
    ],
    "daily_checkin": "每日固定打卡要求。包含5项打卡清单，且针对用户的具体痛点进行个性化定制。"
                     "第1项固定为个案产品排班完成情况；如方案要求减量或暂停，题目必须按减量/暂停方案写，禁止继续要求三杯全部完成。"
                     "5项中必须包含1项运动/步数完成情况（如「是否完成30分钟快走或≥7000步」）。"
                     "至少2项打卡必须是正向动作（做到什么），不能全是限制项。"
                     "每项必须天然适合转成问卷字段：要么是含「是否」的判断题，要么是需要用户填写数值/描述的填空题。"
                     "情绪稳定度评分和饭后困评分是两个独立候选问题，严禁合并为1项综合观察；"
                     "两项同时入选时各占1项，名额不足时按用户具体卡点只保留优先级更高的一项。"
                     "不要输出硬编码的通用套话。"
                     "禁止在每项后加括号注释（如「正向动作」「限制动作」）。",
    "weekly_standard": "每周通过标准，1句，必须包含4要素：个案产品排班完成情况 + 关键饮食动作 + 运动/步数达标 + 至少1次体重/围度/体感记录。减量或暂停用户不得写三杯完成要求。",
    "adjust_stop_conditions": "调整与停止条件，1-3句。必须说明什么情况减半、前移、暂停、联系班主任、及时就医。不要写成医疗方案。",
}


STAGE_REPORT_SPEC = {
    "stage_summary": "阶段总结",
    "risk_reminder": "风险提醒",
    "adjustment_advice": "微调建议",
    "followup_advice": "后续执行建议",
}


def prompt_schema_for(document_type: DocumentType) -> dict[str, Any]:
    if document_type == DocumentType.ENERGY_RESTART_PRESCRIPTION:
        return ENERGY_FIELD_SPEC
    return EXECUTION_FIELD_SPEC


def combined_prompt_schema() -> dict[str, Any]:
    return {
        DocumentType.ENERGY_RESTART_PRESCRIPTION.value: ENERGY_FIELD_SPEC,
        DocumentType.DAY28_EXECUTION_STANDARD.value: EXECUTION_FIELD_SPEC,
    }


def stage_report_prompt_schema(stage: str) -> dict[str, Any]:
    return STAGE_REPORT_SPEC


def build_document_content_json(
    *,
    document_type: DocumentType,
    title: str,
    user_name: str,
    display_name: str | None = None,
    fields: dict[str, Any],
) -> dict[str, Any]:
    if document_type == DocumentType.ENERGY_RESTART_PRESCRIPTION:
        return _build_energy(title, user_name, display_name, fields)
    return _build_execution(title, user_name, display_name, fields)


def build_stage_report_content_json(
    *,
    stage: str,
    title: str,
    user_name: str,
    fields: dict[str, Any],
) -> dict[str, Any]:
    blocks = [
        _text_block("stage_summary", "1. 阶段总结", fields.get("stage_summary")),
        _text_block("risk_reminder", "2. 风险提醒", fields.get("risk_reminder")),
        _text_block("adjustment_advice", "3. 微调建议", fields.get("adjustment_advice")),
        _text_block(
            "followup_advice",
            "4. 后续执行建议",
            fields.get("followup_advice") or fields.get("repurchase_advice"),
        ),
    ]
    payload = _payload(title=title, recipient=user_name, blocks=blocks)
    payload["stage"] = stage
    return payload


def render_document_content(content_json: dict[str, Any]) -> str:
    lines = []
    if content_json.get("show_header_brand", True):
        lines.append("Powered by HumanBurning.AI")
    lines.append(f"# {content_json.get('title') or '交付文件'}")
    recipient = _text(content_json.get("recipient"))
    if recipient and content_json.get("show_recipient", True):
        lines.append(f"To：{recipient}")
    for block in content_json.get("blocks") or []:
        title = _text(block.get("title"))
        kind = block.get("kind")
        if block.get("display") == "inline":
            content = _text(block.get("content"))
            if title and content:
                lines.append(f"{title}：{content}")
            elif content:
                lines.append(content)
            continue
        if title:
            lines.extend(["", f"## {title}"])
        if kind == "table":
            rows = block.get("rows") or []
            for row in rows:
                cells = [_text(cell.get("value")) if isinstance(cell, dict) else _text(cell) for cell in row]
                lines.append(" | ".join(cells))
        elif kind == "list":
            for item in block.get("items") or []:
                if _text(item):
                    lines.append(f"- {_text(item)}")
        else:
            content = _text(block.get("content"))
            if content:
                lines.append(content)
    if content_json.get("show_footer_brand", True):
        lines.extend(["", "Powered by HumanBurning.AI"])
    return "\n".join(lines).strip()


def _build_energy(title: str, user_name: str, display_name: str | None, fields: dict[str, Any]) -> dict[str, Any]:
    overview = _dict(fields.get("overview"))
    blocks = [
        {
            "key": "overview",
            "title": "1. 用户信息概览表",
            "kind": "table",
            "rows": [
                [_label("用户姓名"), _editable(overview.get("name") or user_name), _label("性别 / 年龄"), _editable(overview.get("gender_age"))],
                [_label("身高 / 体重"), _editable(overview.get("height_weight")), _label("BMI"), _editable(overview.get("bmi"))],
                [_label("腰围 / 体脂率"), _editable(overview.get("waist_bodyfat")), _label("最近3个月变化"), _editable(overview.get("recent_change"))],
                [_label("作息状态"), _editable(overview.get("schedule")), _label("运动基础"), _editable(overview.get("exercise"))],
                [_label("主要诉求"), _editable(overview.get("main_need")), _label("当前主线"), _editable(overview.get("current_focus"))],
            ],
        },
        _core_conclusions_block(fields),
        _goal_response_block(fields),
        _list_block("judgement_points", "4. 为什么我会这样判断", fields.get("judgement_points")),
        _success_path_block(fields),
        _priority_items_block(fields),
        _rhythm_recovery_block(fields),
        _module_block(fields),
        _product_block(fields),
        _list_block("week_actions", "10. 本周直接执行", fields.get("week_actions"), limit=3),
        _expected_block(fields),
        _text_block("special_reminder", "12. 特别提醒", fields.get("special_reminder")),
        _missing_information_block(fields),
        _text_block(
            "notice",
            "14. 说明",
            "本报告用于生活方式和体重管理参考，不替代医生诊断、治疗或药物调整。如出现胸痛、明显心悸、呼吸困难、持续头晕、黑便、便血、腹痛、呕吐或其他明显不适，请暂停执行并及时就医。",
        ),
    ]
    return _payload(title=title, recipient=fields.get("recipient") or display_name or user_name, blocks=blocks)


def _build_execution(title: str, user_name: str, display_name: str | None, fields: dict[str, Any]) -> dict[str, Any]:
    rows = fields.get("weekly_plan")
    if not isinstance(rows, list):
        rows = []
    rows_by_week = {str(row.get("week")): row for row in rows if isinstance(row, dict)}
    table_rows = [[_label("周次"), _label("核心任务"), _label("验收标准")]]

    fixed_focus = {
        "第1周": "稳住节律 / 先守底盘",
        "第2周": "拉高关键动作一致性",
        "第3周": "开始抓结果指标",
        "第4周": "稳定复现 / 做实结果",
    }

    fixed_actions = {
        "第1周": "按个案产品排班完成；每天至少吃2拳蔬菜；完成计划内活动。",
        "第2周": "连续5天完成关键饮食动作；每周2次15分钟徒手力量；晚餐后步行15–20分钟。",
        "第3周": "记录体重和腰围；继续快走和轻力量；观察睡眠、饭后困和排便变化。",
        "第4周": "把蔬菜、快走和轻力量做满；复现有效动作，不过度加码。",
    }
    
    for week in ("第1周", "第2周", "第3周", "第4周"):
        row = rows_by_week.get(week, {})
        focus_val = _execution_sentence(
            row.get("core_task") or row.get("focus") or fixed_focus[week],
            max_parts=1,
            max_len=18,
        )

        # Backward compatibility for the old focus / required_actions shape.
        actions_val = (
            row.get("acceptance_standard")
            or row.get("required_actions")
            or row.get("standard")
        )
        if not actions_val:
            actions_val = fixed_actions[week]
        else:
            actions_val = _execution_sentence(actions_val, max_parts=3, max_len=58)

        table_rows.append(
            [
                _label(week),
                _editable(focus_val),
                _editable(actions_val),
            ]
        )

    blocks = [
        _execution_goal_summary_block(fields),
        _text_block("current_phase_goal", "2. 当前阶段目标", fields.get("current_phase_goal")),
        _text_block(
            "principle",
            "3. 执行总原则",
            fields.get("principle") or "先守底盘，再抓结果；先连续完成，再逐步加码。",
        ),
        _execution_product_block(fields),
        _list_block("diet_rules", "5. 饮食规则", fields.get("diet_rules"), limit=4),
        _list_block("exercise_rules", "6. 运动规则", fields.get("exercise_rules"), limit=3),
        {"key": "weekly_plan", "title": "7. 四周执行表", "kind": "table", "rows": table_rows},
        _text_block(
            "daily_checkin",
            "8. 每日固定打卡（每天只打这5项）",
            _daily_checkin_text(fields.get("daily_checkin")),
        ),
        _text_block("weekly_standard", "9. 每周通过标准", _weekly_standard_text(fields.get("weekly_standard"))),
        _text_block("adjust_stop_conditions", "10. 调整和停止条件", _adjust_stop_conditions_text(fields.get("adjust_stop_conditions"))),
    ]
    payload = _payload(title=f"{display_name or user_name}｜{title}", recipient="", blocks=blocks)
    payload["show_recipient"] = False
    payload["show_footer_brand"] = False
    return payload


def normalize_content_json(value: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(value)
    blocks = []
    for block in normalized.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        item = dict(block)
        if item.get("kind") == "table":
            item["rows"] = _normalize_rows(item.get("rows"))
        elif item.get("kind") == "list":
            item["items"] = [_text(entry) for entry in item.get("items") or []]
        else:
            item["content"] = _text(item.get("content"))
        blocks.append(item)
    normalized["blocks"] = blocks
    return normalized


def _payload(*, title: str, recipient: Any, blocks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "template_version": "ranai_docx_v1",
        "title": title,
        "recipient": _text(recipient),
        "blocks": [block for block in blocks if _is_non_empty_block(block)],
    }


def _is_non_empty_block(block: dict[str, Any]) -> bool:
    if not block:
        return False
    kind = block.get("kind")
    if kind == "table":
        return bool(block.get("rows"))
    if kind == "list":
        return bool(block.get("items"))
    if block.get("key") == "non_negotiable":
        return bool(_text(block.get("title")))
    return bool(_text(block.get("content")) or block.get("display") == "inline" and _text(block.get("content")))


def _text_block(key: str, title: str, content: Any) -> dict[str, Any]:
    return {"key": key, "title": title, "kind": "text", "content": _clean_text(content)}


def _inline_block(key: str, title: str, content: Any) -> dict[str, Any]:
    return {
        "key": key,
        "title": title,
        "kind": "text",
        "display": "inline",
        "content": _clean_inline_value(content, title),
    }


def _list_block(key: str, title: str, items: Any, *, limit: int | None = None) -> dict[str, Any]:
    if not isinstance(items, list):
        items = [_text(items)] if _text(items) else []
    cleaned = [_clean_text(item) for item in items if _text(item)]
    if limit is not None:
        cleaned = cleaned[:limit]
    return {"key": key, "title": title, "kind": "list", "items": cleaned}


def _non_negotiable_block(fields: dict[str, Any]) -> dict[str, Any]:
    """生成第 10 节「您这 28 天最不能打折的一件事」。

    旧版（已废弃）会把 LLM 给的具体动作拼到 title 末尾、再把 content 清空，
    导致 block.title = "10. 您这28天最不能打折的一件事是：<答案>"，content 为空，
    前端 StructuredEditor 把 title 当作 h3 渲染，content 渲染为空 Textarea，
    用户详情页 blockText() 只读 content 也会显示为空。

    新版语义统一：title 是章节名，content 是答案全文。
    - 如果 LLM 同时给了 title（短标签）和 content（详细说明），合并为 content。
    - 如果 LLM 只给了 title（短标签），用 title 作为 content。
    - 如果 LLM 只给了 content（说明性文字），直接用 content。
    """
    value = _dict(fields.get("non_negotiable"))
    title = _text(value.get("title"))
    content = _text(value.get("content"))

    if title and content:
        body = f"{title}。{content}".strip("。")
    elif title:
        body = title
    elif content:
        body = content
    else:
        body = "先守住最关键动作"

    return _text_block(
        "non_negotiable",
        "10. 您这28天最不能打折的一件事",
        body,
    )


def _goal_response_block(fields: dict[str, Any]) -> dict[str, Any]:
    value = _dict(fields.get("goal_response"))
    title = "3. 对您目标的直接回应"

    content = _text(value.get("content"))
    if content:
        return _text_block("goal_response", title, content)

    goal_type = _text(value.get("goal_type"))
    judgement_type = _text(value.get("judgement_type"))
    meta = []
    if goal_type in {"specific", "general"}:
        meta.append(f"目标类型：{'具体目标' if goal_type == 'specific' else '泛化期待'}")
    if judgement_type in {"reasonable", "needs_adjustment"}:
        meta.append(f"判断：{'需要调整' if judgement_type == 'needs_adjustment' else '当前合理'}")
    lines = [
        *meta,
        f"用户目标：{_text(value.get('user_goal'))}",
        f"直接回应：{_text(value.get('judgement')) or ('当前需要调整' if judgement_type == 'needs_adjustment' else '')}",
        f"基础目标：{_text(value.get('base_goal'))}",
        f"挑战目标：{_text(value.get('challenge_goal'))}",
        f"冲刺条件：{_text(value.get('conditions'))}",
    ]
    return _text_block("goal_response", title, "\n".join(line for line in lines if not line.endswith("：")))


def _core_conclusions_block(fields: dict[str, Any]) -> dict[str, Any]:
    value = _dict(fields.get("core_conclusions"))
    success_path = _dict(fields.get("success_path"))
    overview = _dict(fields.get("overview"))
    management_band = _text(value.get("management_band") or success_path.get("weight_band"))
    body_trend = _text(value.get("body_trend") or overview.get("recent_change")) or "信息不足"
    evidence = value.get("trend_evidence")
    if not isinstance(evidence, list):
        evidence = []
    evidence_text = "；".join(_clean_text(item) for item in evidence[:3] if _text(item))
    lines = [
        _text(fields.get("one_sentence")),
        f"管理带：{management_band}" if management_band else "",
        f"管理带解释：{_text(value.get('band_explanation'))}" if _text(value.get("band_explanation")) else "",
        f"近3个月身体趋势：{body_trend}",
        f"趋势解释：{_text(value.get('trend_explanation'))}" if _text(value.get("trend_explanation")) else "",
        f"问卷判断依据：{evidence_text}" if evidence_text else "",
        f"这28天先做什么：{_text(value.get('day28_task'))}" if _text(value.get("day28_task")) else "",
        f"长期防线：{_text(value.get('long_term_defense'))}" if _text(value.get("long_term_defense")) else "",
    ]
    return _text_block(
        "core_conclusions",
        "2. 一句话结论与两项核心判断",
        "\n".join(line for line in lines if line),
    )


def _priority_items_block(fields: dict[str, Any]) -> dict[str, Any]:
    items = fields.get("priority_items")
    if not isinstance(items, list):
        items = []
    if not items:
        week_actions = fields.get("week_actions")
        first_week_action = week_actions[0] if isinstance(week_actions, list) and week_actions else None
        fallback = [
            fields.get("priority_direction"),
            _dict(fields.get("non_negotiable")).get("content"),
            first_week_action,
        ]
        items = [_text(item) for item in fallback if _text(item)]
    return _list_block(
        "priority_items",
        "6. 您真正需要优先解决的三件事",
        items,
        limit=3,
    )


def _rhythm_recovery_block(fields: dict[str, Any]) -> dict[str, Any]:
    lines = [
        _text(fields.get("current_state")),
        (
            f"食欲与饭后状态：{_text(fields.get('insulin_resistance'))}"
            if _text(fields.get("insulin_resistance"))
            else ""
        ),
        (
            f"饱腹感与晚间收口：{_text(fields.get('leptin_sensitivity'))}"
            if _text(fields.get("leptin_sensitivity"))
            else ""
        ),
        (
            f"睡眠、压力与恢复：{_text(fields.get('hormone_stage'))}"
            if _text(fields.get("hormone_stage"))
            else ""
        ),
        (
            f"当前最该先抓的方向：{_text(fields.get('priority_direction'))}"
            if _text(fields.get("priority_direction"))
            else ""
        ),
    ]
    return _text_block(
        "rhythm_recovery",
        "7. 代谢节律与恢复状态评估",
        "\n".join(line for line in lines if line),
    )


def _missing_information_block(fields: dict[str, Any]) -> dict[str, Any]:
    value = fields.get("missing_information")
    if isinstance(value, list):
        items = value
    elif _text(value):
        items = [_text(value)]
    else:
        overview = _dict(fields.get("overview"))
        items = []
        if "未提供" in _text(overview.get("waist_bodyfat")) or "暂未测量" in _text(overview.get("waist_bodyfat")):
            items.append("腰围或体脂率待补测")
    return _list_block("missing_information", "13. 需要补充的信息", items)


def _execution_goal_summary_block(fields: dict[str, Any]) -> dict[str, Any]:
    lines = [
        f"当前管理带：{_text(fields.get('weight_band'))}",
        f"近3个月身体趋势：{_text(fields.get('body_trend'))}",
        f"基础目标：{_text(fields.get('base_goal') or fields.get('day28_goal'))}",
        f"挑战目标：{_text(fields.get('challenge_goal'))}",
        f"长期目标：{_text(fields.get('long_term_goal') or fields.get('path_goal'))}",
        f"当前阶段主线：{_text(fields.get('current_mainline'))}",
    ]
    return _text_block(
        "goal_summary",
        "1. 目标摘要",
        "\n".join(line for line in lines if not line.endswith("：")),
    )


def _execution_product_block(fields: dict[str, Any]) -> dict[str, Any]:
    value = _dict(fields.get("product_schedule"))
    lines = [
        f"早C：{_text(value.get('morning_c'))}" if _text(value.get("morning_c")) else "",
        f"午B：{_text(value.get('noon_b'))}" if _text(value.get("noon_b")) else "",
        f"晚A：{_text(value.get('night_a'))}" if _text(value.get("night_a")) else "",
    ]
    return _text_block(
        "product_schedule",
        "4. 产品排班",
        "\n".join(line for line in lines if line),
    )


def _first_week_signals_block(fields: dict[str, Any]) -> dict[str, Any]:
    value = fields.get("first_week_signals")
    if isinstance(value, list):
        lines = [_clean_text(item) for item in value if _clean_text(item)]
        return _list_block("first_week_signals", "14. 前7天重点看4个信号", lines, limit=4)
    text_val = _clean_text(value)
    if not text_val:
        return _list_block("first_week_signals", "14. 前7天重点看4个信号", [], limit=4)
    lines = [part.strip() for part in re_split_standard(text_val) if part.strip()]
    return _list_block("first_week_signals", "14. 前7天重点看4个信号", lines or [text_val], limit=4)


def _success_path_block(fields: dict[str, Any]) -> dict[str, Any]:
    value = _dict(fields.get("success_path"))
    intro = _text(value.get("intro")) or "您的成功路径，不是只看这28天。"
    
    bullets = []
    
    wb = _text(value.get("weight_band"))
    if wb:
        if not wb.startswith("•"):
            wb = f"• 您当前管理带：{_clean_prefix(_clean_prefix(wb, '您当前管理带'), '您当前所属减重带')}"
        bullets.append(wb)
        
    tg = _text(value.get("total_goal"))
    if tg:
        if not tg.startswith("•"):
            tg = f"• 您当前更适合的总路径目标：{_clean_prefix(tg, '您当前更适合的总路径目标')}"
        bullets.append(tg)
        
    d28 = _text(value.get("day28_goal"))
    if d28:
        if not d28.startswith("•"):
            d28 = f"• 第一个28天：{_clean_prefix(d28, '第一个28天')}"
        bullets.append(d28)
        
    w5_8 = _text(value.get("week5_8"))
    if w5_8:
        if not w5_8.startswith("•"):
            w5_8 = f"• 第5–8周：{_clean_prefix(w5_8, '第5–8周')}"
        bullets.append(w5_8)
        
    w9_12 = _text(value.get("week9_12"))
    if w9_12:
        if not w9_12.startswith("•"):
            w9_12 = f"• 第9–12周：{_clean_prefix(w9_12, '第9–12周')}"
        bullets.append(w9_12)
        
    lines = [intro] + bullets
    return _text_block("success_path", "5. 您的成功路径，不是只看这28天", "\n".join(line for line in lines if line))


def _module_block(fields: dict[str, Any]) -> dict[str, Any]:
    value = _dict(fields.get("module_diagnosis"))
    lines = [
        f"节律：{_text(value.get('rhythm'))}",
        f"饮食：{_text(value.get('diet'))}",
        f"睡眠：{_text(value.get('sleep'))}",
        f"压力：{_text(value.get('pressure'))}",
        f"身体活动：{_text(value.get('activity'))}",
    ]
    return _text_block("module_diagnosis", "8. 模块诊断：节律 / 饮食 / 睡眠 / 压力 / 身体活动", "\n".join(line for line in lines if not line.endswith("：")))


def _expected_block(fields: dict[str, Any]) -> dict[str, Any]:
    value = _dict(fields.get("expected_changes"))
    lines = [
        _text(value.get("day7")),
        _text(value.get("day14_28")),
    ]
    labelled = []
    if lines[0]:
        labelled.append(f"前7天先看：{lines[0]}")
    if lines[1]:
        labelled.append(f"第14–28天再看：{lines[1]}")
    return _text_block("expected_changes", "11. 您可能先看到的变化", "\n".join(labelled))


def _product_block(fields: dict[str, Any]) -> dict[str, Any]:
    value = _dict(fields.get("product_schedule"))
    summary = _text(value.get("summary"))
    morning = _text(value.get("morning_c"))
    noon = _text(value.get("noon_b"))
    night = _text(value.get("night_a"))
    
    bullets = []
    if morning:
        if not morning.startswith("•"):
            morning = f"• 早C：{_clean_prefix(morning, '早C')}"
        bullets.append(morning)
    if noon:
        if not noon.startswith("•"):
            noon = f"• 午B：{_clean_prefix(noon, '午B')}"
        bullets.append(noon)
    if night:
        if not night.startswith("•"):
            night = f"• 晚A：{_clean_prefix(night, '晚A')}"
        bullets.append(night)
        
    lines = []
    if summary:
        lines.append(summary)
    lines.extend(bullets)
    
    meaning = _text(fields.get("product_meaning")) or (
        "饮食和运动本身有效；早C、午B、晚A的作用是配合个案排班帮助执行和稳定节律，"
        "不是减重捷径，也不替代饮食、运动或医疗建议。"
    )
    return _text_block(
        "product_schedule",
        "9. 早C、午B、晚A对您的意义",
        "\n".join([meaning, *[line for line in lines if line]]),
    )


def _normalize_rows(rows: Any) -> list[list[dict[str, Any]]]:
    if not isinstance(rows, list):
        return []
    normalized_rows = []
    for row in rows:
        if not isinstance(row, list):
            continue
        normalized_rows.append([cell if isinstance(cell, dict) else _editable(cell) for cell in row])
    return normalized_rows


def _label(value: Any) -> dict[str, Any]:
    return {"value": _text(value), "editable": False}


def _editable(value: Any) -> dict[str, Any]:
    return {"value": _clean_text(value), "editable": True}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return ""
    return str(value).strip()


def _clean_text(value: Any) -> str:
    text_val = _text(value)
    if not text_val:
        return ""
    replacements = {
        "✅": "",
        "📊": "",
        "👉": "",
        "✔": "",
        "☑": "",
        "**": "",
        "###": "",
        "##": "",
        "#": "",
    }
    for old, new in replacements.items():
        text_val = text_val.replace(old, new)
    lines = []
    for line in text_val.splitlines():
        cleaned = line.strip()
        cleaned = cleaned.lstrip("-•* ").strip()
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines).strip()


def _clean_inline_value(value: Any, title: str) -> str:
    text_val = _clean_text(value)
    prefixes = (
        title,
        title.replace("您当前", "当前"),
        title.replace("第一个", "第1个"),
        title.replace("更适合的", "您当前更适合的"),
    )
    for prefix in prefixes:
        text_val = _clean_prefix(text_val, prefix)
    return text_val


def _compact_standard(value: Any) -> str:
    text_val = _clean_text(value)
    if not text_val:
        return ""
    parts = [part.strip() for part in re_split_standard(text_val) if part.strip()]
    if not parts:
        return text_val[:80]
    compact = "；".join(parts[:3])
    return compact[:90].rstrip("；，,。") + "。"


def _execution_sentence(value: Any, *, max_parts: int, max_len: int) -> str:
    text_val = _clean_text(value)
    if not text_val:
        return ""
    parts = [part.strip() for part in re_split_standard(text_val) if part.strip()]
    if not parts:
        parts = [text_val]
    selected: list[str] = []
    for part in parts:
        candidate = "；".join([*selected, part]).strip("；，,。 ")
        if selected and len(candidate) > max_len:
            break
        selected.append(part)
        if len(selected) >= max_parts:
            break
    compact = "；".join(selected or parts[:1]).strip("；，,。 ")
    return compact + ("。" if compact and compact[-1] not in "。！？" else "")


def _weekly_standard_text(value: Any) -> str:
    text_val = _execution_sentence(value, max_parts=3, max_len=98)
    if not text_val:
        return "每周至少5天完成个案产品排班和关键饮食动作；运动或步数达到计划；至少记录1次体重、围度或体感。"
    if "体重" not in text_val and "围度" not in text_val and "体感" not in text_val:
        text_val = text_val.rstrip("。") + "；至少记录1次体重或围度/体感。"
    return text_val


def _adjust_stop_conditions_text(value: Any) -> str:
    text_val = _execution_sentence(value, max_parts=3, max_len=120)
    if text_val:
        return text_val
    return "心慌、睡眠明显变差或胃部不适时，早C/午B先减半并前移；持续不适、胸痛、呼吸困难、明显心悸或异常疼痛时，暂停执行并及时就医。"


def _reminder_text(value: Any) -> str:
    text_val = _personal_reminder(value)
    parts = [part.strip() for part in re_split_standard(text_val) if part.strip()]
    if not parts:
        return "结果看连续性，不看单日完美。持续不适请及时就医。"
    compact = "。".join(parts[:2]).strip("。")
    if "结果看连续性" not in compact and "不看单日完美" not in compact:
        compact = f"结果看连续性，不看单日完美。{compact}"
    if len(compact) > 82:
        compact = compact[:82].rstrip("；，,。 ")
    return compact + ("。" if compact[-1] not in "。！？" else "")


def re_split_standard(value: str) -> list[str]:
    import re

    return re.split(r"[。；;\n]+", value)


def _default_week_standard(week: str) -> str:
    defaults = {
        "第1周": "按个案产品排班完成；晚餐主食控制在1拳内；完成计划内活动。",
        "第2周": "连续5天完成关键饮食动作；晚餐后走15–20分钟；不额外加宵夜。",
        "第3周": "记录体重和腰围；每周安排2次轻力量；继续观察睡眠和排便。",
        "第4周": "把收口、步数和轻力量做满；复盘腰腹、睡眠和排便变化。",
    }
    return defaults.get(week, "")


def _daily_checkin_text(value: Any) -> str:
    if not value:
        return (
            "1. 个案产品排班是否按方案完成\n"
            "2. 蔬菜总量是否至少2拳\n"
            "3. 是否完成计划内快走或力量训练\n"
            "4. 睡眠是否按计划收口\n"
            "5. 饭后困评分（1分很困、5分不困）。"
        )
    
    if isinstance(value, list):
        cleaned_items = []
        for idx, item in enumerate(value, 1):
            cleaned = _clean_text(item)
            if cleaned:
                cleaned_items.append(f"{idx}. {cleaned}")
        if cleaned_items:
            return "\n".join(cleaned_items)
            
    text_val = _clean_text(value)
    lines = []
    import re
    parts = re.split(r'[;\n；\n]+', text_val)
    idx = 1
    for part in parts:
        cleaned = part.strip().lstrip("-•* ").strip()
        cleaned = re.sub(r'^\d+[\.、\)\）]\s*', '', cleaned)
        if cleaned:
            lines.append(f"{idx}. {cleaned}")
            idx += 1
            
    if lines:
        return "\n".join(lines)
    return text_val


def _personal_reminder(value: Any) -> str:
    text_val = _clean_text(value)
    text_val = _clean_prefix(text_val, "提醒")
    generic = {"注意坚持执行。", "请按计划执行，如有不适请及时就医。", "本文件不替代医疗建议。"}
    if not text_val or text_val in generic or len(text_val) < 18:
        return "结果看连续性，不看单日完美。不建议跑跳冲刺和极端断食；如睡眠被工作或照护节奏打乱，含咖啡因的早C/午B必须放在计划睡眠前8小时以外。持续不适请及时就医。"
    if "结果看连续性" not in text_val and "不看单日完美" not in text_val:
        return f"结果看连续性，不看单日完美。{text_val}"
    return text_val


def re_sub_multi_space(value: str) -> str:
    import re

    return re.sub(r"\s+", " ", value).strip()


def _clean_prefix(value: Any, prefix: str) -> str:
    text_val = _text(value)
    if not text_val:
        return ""
    normalized_prefixes = [prefix]
    if "–" in prefix:
        normalized_prefixes.append(prefix.replace("–", "-"))
    elif "-" in prefix:
        normalized_prefixes.append(prefix.replace("-", "–"))

    for p in normalized_prefixes:
        for sep in ("：", ":", "，", ",", ""):
            test_p = p + sep
            if test_p and text_val.startswith(test_p):
                return text_val[len(test_p):].strip()
    return text_val


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
