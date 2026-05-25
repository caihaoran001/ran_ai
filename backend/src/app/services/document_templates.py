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
        "title": "这28天最不能打折的一件事。必须来自用户最特异的个人风险信号或成败卡点，禁止写通用答案",
        "content": "原因和提醒",
    },
    "week_actions": ["本周直接执行，3-5条。至少包含1条运动建议，且运动建议必须写出类型+频率+时长+禁忌时间窗口"],
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
    "day28_goal": "第一个28天目标，必须写出具体斤数范围，不可只写定性描述",
    "current_phase_goal": "当前阶段目标",
    "principle": "执行总原则，一句话，强调先守早C/午B/晚A和关键生活动作，再抓结果",
    "weekly_plan": [
        {"week": "第1周", "focus": "稳住节律 / 先守底盘", "required_actions": "必须动作，1-2个极简短句，例如：醒后早C、工作中段午B、晚间晚A；晚餐无主食", "pass_standard": "通过标准，例如：≥5天完成固定节律"},
        {"week": "第2周", "focus": "拉高关键动作一致性", "required_actions": "必须动作，例如：晚餐边界+运动频次一起做", "pass_standard": "通过标准，例如：≥5天完成饮食和运动"},
        {"week": "第3周", "focus": "开始抓结果指标", "required_actions": "必须动作，例如：记录体重/腰腹/睡眠或情绪", "pass_standard": "通过标准，例如：至少2项指标变好"},
        {"week": "第4周", "focus": "稳定复现 / 做实结果", "required_actions": "必须动作，例如：不加码冒进，复现有效动作", "pass_standard": "通过标准，例如：连续5天不掉线"},
    ],
    "daily_checkin": "每日固定打卡要求。包含5项打卡清单，且针对用户的具体痛点进行个性化定制。早C/午B/晚A合并成一项；至少2项打卡必须是正向动作（做到什么），不能全是限制项；情绪稳定度和饭后困必须合并为1项综合观察，不要输出硬编码的通用套话。",
    "week5_12": "第5-8周和第9-12周提前说明",
    "weekly_standard": "每周通过标准，必须包含至少1次体重/围度/体感记录",
    "reminder": "个性化提醒，第一句必须是定心丸，例如“结果看连续性，不看单日完美”；必须结合用户作息/运动/睡眠/不适/夜班等情况；不要写通用套话；必要时提醒就医",
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


def combined_prompt_schema() -> dict[str, Any]:
    return {
        DocumentType.ENERGY_RESTART_PRESCRIPTION.value: ENERGY_FIELD_SPEC,
        DocumentType.DAY28_EXECUTION_STANDARD.value: EXECUTION_FIELD_SPEC,
    }


def stage_report_prompt_schema(stage: str) -> dict[str, Any]:
    if stage == "graduation":
        return GRADUATION_REPORT_SPEC
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
    display_title = title
    if "v6.3" not in display_title:
        display_title = f"{display_title} v6.3"
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
    return _payload(title=display_title, recipient=fields.get("recipient") or display_name or user_name, blocks=blocks)


def _build_execution(title: str, user_name: str, display_name: str | None, fields: dict[str, Any]) -> dict[str, Any]:
    rows = fields.get("weekly_plan")
    if not isinstance(rows, list):
        rows = []
    rows_by_week = {str(row.get("week")): row for row in rows if isinstance(row, dict)}
    table_rows = [[_label("周次"), _label("核心目标"), _label("必须动作"), _label("通过标准")]]
    
    fixed_focus = {
        "第1周": "稳住节律 / 先守底盘",
        "第2周": "拉高关键动作一致性",
        "第3周": "开始抓结果指标",
        "第4周": "稳定复现 / 做实结果",
    }
    
    fixed_actions = {
        "第1周": "早C午B晚A不断；每天至少吃2拳蔬菜；完成每周3次30分钟快走。",
        "第2周": "连续5天守住三杯节律；每周2次15分钟徒手力量；晚餐后步行15–20分钟。",
        "第3周": "记录体重和腰围；继续快走和轻力量；观察睡眠、饭后困和排便变化。",
        "第4周": "把蔬菜、快走和轻力量做满；复现有效动作，不过度加码。",
    }
    
    fixed_pass = {
        "第1周": "≥5天完成固定节律，并至少记录1次体重/围度/体感。",
        "第2周": "≥5天完成饮食和运动，并至少记录1次体重/围度/体感。",
        "第3周": "至少完成1次体重/围度/体感记录，且至少2项指标变好。",
        "第4周": "连续5天不掉线，并至少完成1次体重/围度/体感记录。",
    }
    
    for week in ("第1周", "第2周", "第3周", "第4周"):
        row = rows_by_week.get(week, {})
        focus_val = row.get("focus") or fixed_focus[week]
        
        # Backward compatibility for old single 'standard' field
        actions_val = row.get("required_actions") or row.get("standard")
        if not actions_val:
            actions_val = fixed_actions[week]
        else:
            actions_val = _clean_text(actions_val)
            
        pass_val = row.get("pass_standard")
        if not pass_val:
            pass_val = fixed_pass[week]
        else:
            pass_val = _clean_text(pass_val)
            
        table_rows.append(
            [
                _label(week),
                _editable(focus_val),
                _editable(actions_val),
                _editable(pass_val),
            ]
        )
        
    blocks = [
        _inline_block("weight_band", "您当前所属减重带", fields.get("weight_band")),
        _inline_block("path_goal", "更适合的总路径目标", fields.get("path_goal")),
        _inline_block("day28_goal", "第一个28天目标", fields.get("day28_goal")),
        _inline_block("current_phase_goal", "当前阶段目标", fields.get("current_phase_goal")),
        _inline_block("principle", "执行总原则", fields.get("principle") or "先守早C/午B/晚A和晚间收口底盘，再用饮食、步行和睡眠放大结果。"),
        _text_block(
            "fixed_rhythm",
            "",
            "这28天，早C / 午B / 晚A按时饮用，是整个执行标准的底盘：它在每天重复给身体“启动—续航—收口”的节律信号，帮助白天更稳、下午不塌、晚上更容易收口。",
        ),
        {"key": "weekly_plan", "title": "28天逐周执行标准", "kind": "table", "rows": table_rows},
        _text_block(
            "daily_checkin",
            "每日固定打卡（每天只打这5项）",
            _daily_checkin_text(fields.get("daily_checkin")),
        ),
        _text_block("week5_12", "第5–8周和第9–12周提前说明", fields.get("week5_12") or "第5–8周继续放大已经稳定的动作，第9–12周再根据体重、腰围、睡眠和排便反馈微调目标；不要在第一个28天里一次性加太多动作。"),
        _text_block("weekly_standard", "每周通过标准", fields.get("weekly_standard") or "本周不追求完美，重点看三杯节律是否稳定，并至少完成1次体重 / 围度 / 体感记录，确认自己比上一周更稳。"),
        _text_block("reminder", "提醒", _personal_reminder(fields.get("reminder"))),
    ]
    payload = _payload(title=f"{display_name or user_name}｜{title}（1页版）", recipient="", blocks=blocks)
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
        "blocks": [block for block in blocks if block],
    }


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


def _list_block(key: str, title: str, items: Any) -> dict[str, Any]:
    if not isinstance(items, list):
        items = [_text(items)] if _text(items) else []
    return {"key": key, "title": title, "kind": "list", "items": [_clean_text(item) for item in items if _text(item)]}


def _success_path_block(fields: dict[str, Any]) -> dict[str, Any]:
    value = _dict(fields.get("success_path"))
    intro = _text(value.get("intro")) or "您的成功路径，不是只看这28天。"
    
    bullets = []
    
    wb = _text(value.get("weight_band"))
    if wb:
        if not wb.startswith("•"):
            wb = f"• 您当前所属减重带：{_clean_prefix(wb, '您当前所属减重带')}"
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


def re_split_standard(value: str) -> list[str]:
    import re

    return re.split(r"[。；;\n]+", value)


def _default_week_standard(week: str) -> str:
    defaults = {
        "第1周": "早C/午B/晚A按时完成；晚餐主食控制在1拳内；每天完成基础步数。",
        "第2周": "连续5天守住三杯节律；晚餐后走15–20分钟；不额外加宵夜。",
        "第3周": "记录体重和腰围；每周安排2次轻力量；继续观察睡眠和排便。",
        "第4周": "把收口、步数和轻力量做满；复盘腰腹、睡眠和排便变化。",
    }
    return defaults.get(week, "")


def _daily_checkin_text(value: Any) -> str:
    if not value:
        return "1. 早C/午B/晚A是否全部完成\n2. 今天是否至少吃到2拳蔬菜\n3. 今天是否完成计划内快走/力量训练\n4. 睡眠是否按计划收口\n5. 情绪稳定度和饭后困综合评分。"
    
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
