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
    "judgement_points": ["为什么这样判断，5条以内"],
    "current_state": "当前身体状态真正该关注什么",
    "insulin_resistance": "胰岛素抵抗倾向判断，避免诊断语气",
    "leptin_sensitivity": "瘦素敏感倾向判断，避免诊断语气",
    "hormone_stage": "激素/年龄阶段影响判断，避免医疗诊断",
    "priority_direction": "当前最该先抓的方向",
    "success_path": {
        "intro": "成功路径总述",
        "weight_band": "当前所属减重带",
        "total_goal": "总路径目标",
        "day28_goal": "第一个28天目标",
        "week5_8": "第5-8周目标",
        "week9_12": "第9-12周目标",
    },
    "constitution": "长期体质和当前失衡初步判断",
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
    "non_negotiable": {
        "title": "这28天最不能打折的一件事",
        "content": "原因和提醒",
    },
    "week_actions": ["本周直接执行，3-5条"],
    "product_schedule": {
        "morning_c": "早C",
        "noon_b": "午B",
        "night_a": "晚A",
        "summary": "产品排班总结",
    },
    "special_reminder": "特别提醒，包含必要就医/检测提示",
    "first_week_signals": "前7天重点看4个信号",
}


EXECUTION_FIELD_SPEC = {
    "weight_band": "当前所属减重带",
    "path_goal": "更适合的总路径目标",
    "day28_goal": "第一个28天目标",
    "current_phase_goal": "当前阶段目标",
    "principle": "执行总原则",
    "product_rhythm": "早C/午B/晚A节律说明",
    "weekly_plan": [
        {"week": "第1周", "focus": "重点", "standard": "执行标准"},
        {"week": "第2周", "focus": "重点", "standard": "执行标准"},
        {"week": "第3周", "focus": "重点", "standard": "执行标准"},
        {"week": "第4周", "focus": "重点", "standard": "执行标准"},
    ],
    "daily_checkin": "每日固定打卡要求",
    "week5_12": "第5-8周和第9-12周提前说明",
    "weekly_standard": "每周通过标准",
    "reminder": "提醒，包含不替代医疗建议",
}


STAGE_REPORT_SPEC = {
    "stage_summary": "阶段总结",
    "risk_reminder": "风险提醒",
    "adjustment_advice": "微调建议",
    "repurchase_advice": "复购建议",
}


GRADUATION_REPORT_SPEC = {
    "weight_change": "体重变化",
    "rhythm_change": "节律变化",
    "execution_rate": "执行率",
    "growth_summary": "用户成长总结",
    "next_route": "下一阶段路线图",
}


def prompt_schema_for(document_type: DocumentType) -> dict[str, Any]:
    if document_type == DocumentType.ENERGY_RESTART_PRESCRIPTION:
        return ENERGY_FIELD_SPEC
    return EXECUTION_FIELD_SPEC


def stage_report_prompt_schema(stage: str) -> dict[str, Any]:
    if stage == "graduation":
        return GRADUATION_REPORT_SPEC
    return STAGE_REPORT_SPEC


def build_document_content_json(
    *,
    document_type: DocumentType,
    title: str,
    user_name: str,
    fields: dict[str, Any],
) -> dict[str, Any]:
    if document_type == DocumentType.ENERGY_RESTART_PRESCRIPTION:
        return _build_energy(title, user_name, fields)
    return _build_execution(title, user_name, fields)


def build_stage_report_content_json(
    *,
    stage: str,
    title: str,
    user_name: str,
    fields: dict[str, Any],
) -> dict[str, Any]:
    if stage == "graduation":
        blocks = [
            _text_block("weight_change", "1. 体重变化", fields.get("weight_change")),
            _text_block("rhythm_change", "2. 节律变化", fields.get("rhythm_change")),
            _text_block("execution_rate", "3. 执行率", fields.get("execution_rate")),
            _text_block("growth_summary", "4. 用户成长总结", fields.get("growth_summary")),
            _text_block("next_route", "5. 下一阶段路线图", fields.get("next_route")),
        ]
    else:
        blocks = [
            _text_block("stage_summary", "1. 阶段总结", fields.get("stage_summary")),
            _text_block("risk_reminder", "2. 风险提醒", fields.get("risk_reminder")),
            _text_block("adjustment_advice", "3. 微调建议", fields.get("adjustment_advice")),
            _text_block("repurchase_advice", "4. 复购建议", fields.get("repurchase_advice")),
        ]
    payload = _payload(title=title, recipient=user_name, blocks=blocks)
    payload["stage"] = stage
    return payload


def render_document_content(content_json: dict[str, Any]) -> str:
    lines = ["Powered by HumanBurning.AI", f"# {content_json.get('title') or '交付文件'}"]
    recipient = _text(content_json.get("recipient"))
    if recipient:
        lines.append(f"To：{recipient}")
    for block in content_json.get("blocks") or []:
        title = _text(block.get("title"))
        kind = block.get("kind")
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
    lines.extend(["", "Powered by HumanBurning.AI"])
    return "\n".join(lines).strip()


def _build_energy(title: str, user_name: str, fields: dict[str, Any]) -> dict[str, Any]:
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
        _text_block("one_sentence", "2. 一句话结论", fields.get("one_sentence")),
        _list_block("judgement_points", "3. 为什么我会这样判断", fields.get("judgement_points")),
        _text_block(
            "current_state",
            "4. 您现在的身体状态，真正该关注什么",
            "\n".join(
                item
                for item in [
                    _text(fields.get("current_state")),
                    f"胰岛素抵抗判断：{_text(fields.get('insulin_resistance'))}",
                    f"瘦素敏感判断：{_text(fields.get('leptin_sensitivity'))}",
                    f"激素阶段影响判断：{_text(fields.get('hormone_stage'))}",
                    f"当前最该先抓的方向：{_text(fields.get('priority_direction'))}",
                ]
                if item and not item.endswith("：")
            ),
        ),
        _success_path_block(fields),
        _text_block("constitution", "6. 长期体质和当前失衡初步判断", fields.get("constitution")),
        _module_block(fields),
        _expected_block(fields),
        _text_block(
            "non_negotiable",
            f"9. 您这28天最不能打折的一件事是：{_text(_dict(fields.get('non_negotiable')).get('title')) or '先守住最关键动作'}",
            _dict(fields.get("non_negotiable")).get("content"),
        ),
        _list_block("week_actions", "10. 本周直接执行：不要等想明白再开始", fields.get("week_actions")),
        _product_block(fields),
        _text_block("special_reminder", "12. 特别提醒", fields.get("special_reminder")),
        _text_block("first_week_signals", "13. 前7天重点看4个信号", fields.get("first_week_signals")),
        _text_block("notice", "14. 说明", "本处方单用于生活方式管理和燃AI 28天训练营执行指导，不替代医疗诊断、药物治疗或医生建议。"),
    ]
    return _payload(title=title, recipient=fields.get("recipient") or user_name, blocks=blocks)


def _build_execution(title: str, user_name: str, fields: dict[str, Any]) -> dict[str, Any]:
    rows = fields.get("weekly_plan")
    if not isinstance(rows, list):
        rows = []
    rows_by_week = {str(row.get("week")): row for row in rows if isinstance(row, dict)}
    table_rows = [[_label("周次"), _label("重点"), _label("执行标准")]]
    for week in ("第1周", "第2周", "第3周", "第4周"):
        row = rows_by_week.get(week, {})
        table_rows.append([_label(week), _editable(row.get("focus")), _editable(row.get("standard"))])
    blocks = [
        _text_block("weight_band", "您当前所属减重带", fields.get("weight_band")),
        _text_block("path_goal", "更适合的总路径目标", fields.get("path_goal")),
        _text_block("day28_goal", "第一个28天目标", fields.get("day28_goal")),
        _text_block("current_phase_goal", "当前阶段目标", fields.get("current_phase_goal")),
        _text_block("principle", "执行总原则", fields.get("principle")),
        _text_block("product_rhythm", "早C / 午B / 晚A底盘说明", fields.get("product_rhythm")),
        {"key": "weekly_plan", "title": "28天逐周执行标准", "kind": "table", "rows": table_rows},
        _text_block("daily_checkin", "每日固定打卡（每天只打这5项）", fields.get("daily_checkin")),
        _text_block("week5_12", "第5–8周和第9–12周提前说明", fields.get("week5_12")),
        _text_block("weekly_standard", "每周通过标准", fields.get("weekly_standard")),
        _text_block("reminder", "提醒", fields.get("reminder")),
    ]
    return _payload(title=f"{user_name}｜{title}（1页版）", recipient=user_name, blocks=blocks)


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
        "blocks": [block for block in blocks if block],
    }


def _text_block(key: str, title: str, content: Any) -> dict[str, Any]:
    return {"key": key, "title": title, "kind": "text", "content": _text(content)}


def _list_block(key: str, title: str, items: Any) -> dict[str, Any]:
    if not isinstance(items, list):
        items = [_text(items)] if _text(items) else []
    return {"key": key, "title": title, "kind": "list", "items": [_text(item) for item in items if _text(item)]}


def _success_path_block(fields: dict[str, Any]) -> dict[str, Any]:
    value = _dict(fields.get("success_path"))
    lines = [
        _text(value.get("intro")) or "您的成功路径，不是只看这28天。",
        _text(value.get("weight_band")),
        _text(value.get("total_goal")),
        _text(value.get("day28_goal")),
        _text(value.get("week5_8")),
        _text(value.get("week9_12")),
    ]
    return _text_block("success_path", "5. 您当前所属减重带与成功路径", "\n".join(line for line in lines if line))


def _module_block(fields: dict[str, Any]) -> dict[str, Any]:
    value = _dict(fields.get("module_diagnosis"))
    lines = [
        f"节律：{_text(value.get('rhythm'))}",
        f"饮食：{_text(value.get('diet'))}",
        f"睡眠：{_text(value.get('sleep'))}",
        f"压力：{_text(value.get('pressure'))}",
        f"身体活动：{_text(value.get('activity'))}",
    ]
    return _text_block("module_diagnosis", "7. 模块诊断：节律 / 饮食 / 睡眠 / 压力 / 身体活动", "\n".join(line for line in lines if not line.endswith("：")))


def _expected_block(fields: dict[str, Any]) -> dict[str, Any]:
    value = _dict(fields.get("expected_changes"))
    lines = [
        _text(value.get("day7")),
        _text(value.get("day14_28")),
    ]
    return _text_block("expected_changes", "8. 这28天您更可能看到的变化", "\n".join(line for line in lines if line))


def _product_block(fields: dict[str, Any]) -> dict[str, Any]:
    value = _dict(fields.get("product_schedule"))
    lines = [
        _text(value.get("morning_c")),
        _text(value.get("noon_b")),
        _text(value.get("night_a")),
        _text(value.get("summary")),
    ]
    return _text_block("product_schedule", "11. 产品排班：早C / 午B / 晚A", "\n".join(line for line in lines if line))


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
    return {"value": _text(value), "editable": True}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return ""
    return str(value).strip()


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
