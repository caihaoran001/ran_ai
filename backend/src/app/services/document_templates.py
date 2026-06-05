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
        "title": "短标签，2-12 字。例如「23:30 前闭眼」「守住午B 时间」",
        "content": "这28天最不能打折的具体动作 + 一句原因。必须来自用户最特异的个人风险信号或成败卡点，禁止写通用答案",
    },
    "week_actions": ["本周直接执行，固定3条。至少包含1条运动建议，且运动建议必须写出类型+频率+时长+禁忌时间窗口"],
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
    "weight_band": "当前所属减重带，格式为「{带名}：{通俗描述}」，例如「A带：0-2斤维持优化带」「D带：20斤及以上带」。禁止「维持优化带（A带）」这种括号反转写法。",
    "path_goal": "更适合的总路径目标，必须包含具体数字锚点（斤数范围或目标体重），例如「维持63-65kg，同时提升精力、情绪稳定度和下午续航」或「12-16斤，先把BMI和腰腹维度拉回更安全区间」。",
    "day28_goal": "第一个 28 天目标（必须 1-2 句完整描述，至少 25 字）。"
                   "必须以数字范围开头、再用分号接解释，例如「0-2斤；重点不是减重，而是让白天能量和睡眠边界稳定」。"
                   "禁止以否定句开头（如「不以减重数字为核心目标」），数字范围必须在最前面。"
                   "A带/增肌增能带：先写斤数范围，再用分号写本 28 天主线（关联 priority_direction），最后以「仅作参考」收尾。"
                   "B/C/D带：先写斤数范围，再写配套策略。",
    "current_phase_goal": "当前阶段目标，用「先解决{核心问题}，不是{反面}」句式，聚焦排序而非平铺罗列，例如「先解决节律稳定，不是追求失控式掉秤」。",
    "principle": "执行总原则，保持两段递进结构「先守{底盘动作}，再{放大动作}」，是通用指导语，不嵌入具体参数（时间点、拳数）。例如「先守底盘，再抓结果；先连续完成，再逐步加码」。",
    "weekly_plan": [
        {"week": "第1周", "focus": "稳住节律 / 先守底盘", "required_actions": "执行标准，1个短句，例如：醒后早C、工作中段午B、晚间晚A；晚餐主食≤1拳；每天7000步。"},
        {"week": "第2周", "focus": "拉高关键动作一致性", "required_actions": "执行标准，1个短句，例如：连续5天不漏早餐；晚餐后走15–20分钟；应酬日不加宵夜。"},
        {"week": "第3周", "focus": "开始抓结果指标", "required_actions": "执行标准，1个短句，例如：每周2次轻力量；记录腰围和体重。"},
        {"week": "第4周", "focus": "稳定复现 / 做实结果", "required_actions": "执行标准，1个短句，例如：把睡前收口、7000步、2次轻力量做满。"},
    ],
    "daily_checkin": "每日固定打卡要求。包含5项打卡清单，且针对用户的具体痛点进行个性化定制。"
                     "第1项固定为早C/午B/晚A合并打卡。"
                     "5项中必须包含1项运动/步数完成情况（如「是否完成30分钟快走或≥7000步」）。"
                     "至少2项打卡必须是正向动作（做到什么），不能全是限制项。"
                     "每项必须天然适合转成问卷字段：要么是含「是否」的判断题，要么是需要用户填写数值/描述的填空题。"
                     "情绪稳定度和饭后困必须合并为1项综合观察，不要输出硬编码的通用套话。"
                     "禁止在每项后加括号注释（如「正向动作」「限制动作」）。",
    "week5_12": "第5-8周和第9-12周提前说明，保持一句连贯叙述，描述前28天建立底盘后的自然延伸预期，不拆分时间段。",
    "weekly_standard": "每周通过标准，1句，必须包含4要素：三杯完成≥5天 + 关键饮食动作≥5天 + 运动/步数达标 + 至少1次体重/围度/体感记录。",
    "reminder": "个性化提醒，总共控制在2句内。"
                "第一句必须是定心丸（如「结果看连续性，不看单日完美」）。"
                "第二句是不适处理指引（如「出现持续不适、明显心慌，先停下异常动作并及时咨询专业人士」），结合用户作息/运动/睡眠/不适/夜班等信息。"
                "禁止在reminder中重复non_negotiable的具体内容（它已经单独在处方单中）。"
                "禁止写成长段说明书。必要时提醒就医。",
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
        _non_negotiable_block(fields),
        _list_block("week_actions", "10. 本周直接执行：不要等想明白再开始", fields.get("week_actions"), limit=3),
        _product_block(fields),
        _text_block("special_reminder", "12. 特别提醒", fields.get("special_reminder")),
        _first_week_signals_block(fields),
        _text_block("notice", "14. 说明", "本处方单用于生活方式管理和燃AI 28天训练营执行指导，不替代医疗诊断、药物治疗或医生建议。"),
    ]
    return _payload(title=display_title, recipient=fields.get("recipient") or display_name or user_name, blocks=blocks)


def _build_execution(title: str, user_name: str, display_name: str | None, fields: dict[str, Any]) -> dict[str, Any]:
    rows = fields.get("weekly_plan")
    if not isinstance(rows, list):
        rows = []
    rows_by_week = {str(row.get("week")): row for row in rows if isinstance(row, dict)}
    table_rows = [[_label("周次"), _label("重点"), _label("执行标准")]]
    
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
    
    for week in ("第1周", "第2周", "第3周", "第4周"):
        row = rows_by_week.get(week, {})
        focus_val = _execution_sentence(row.get("focus") or fixed_focus[week], max_parts=1, max_len=18)
        
        # Backward compatibility for old single 'standard' field
        actions_val = row.get("required_actions") or row.get("standard")
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
        _inline_block("weight_band", "您当前所属减重带", _execution_sentence(fields.get("weight_band"), max_parts=1, max_len=38)),
        _inline_block("path_goal", "更适合的总路径目标", _execution_sentence(fields.get("path_goal"), max_parts=1, max_len=80)),
        _inline_block("day28_goal", "第一个28天目标", _execution_sentence(fields.get("day28_goal"), max_parts=3, max_len=140)),
        _inline_block("current_phase_goal", "当前阶段目标", _execution_sentence(fields.get("current_phase_goal"), max_parts=1, max_len=80)),
        _inline_block("principle", "执行总原则", _execution_sentence(fields.get("principle") or "先守底盘，再抓结果；先连续完成，再逐步加码。", max_parts=2, max_len=72)),
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
        _text_block("week5_12", "第5–8周和第9–12周提前说明", _execution_sentence(fields.get("week5_12") or "第5–8周继续放大已经稳定的动作，第9–12周再根据体重、腰围、睡眠和排便反馈微调目标；不要在第一个28天里一次性加太多动作。", max_parts=2, max_len=92)),
        _text_block("weekly_standard", "每周通过标准", _weekly_standard_text(fields.get("weekly_standard"))),
        _text_block("reminder", "提醒", _reminder_text(fields.get("reminder"))),
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
    """生成第 9 节「您这 28 天最不能打折的一件事」。

    旧版（已废弃）会把 LLM 给的具体动作拼到 title 末尾、再把 content 清空，
    导致 block.title = "9. 您这28天最不能打折的一件事是：<答案>"，content 为空，
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
        "9. 您这28天最不能打折的一件事",
        body,
    )


def _first_week_signals_block(fields: dict[str, Any]) -> dict[str, Any]:
    value = fields.get("first_week_signals")
    if isinstance(value, list):
        lines = [_clean_text(item) for item in value if _clean_text(item)]
        return _list_block("first_week_signals", "13. 前7天重点看4个信号", lines, limit=4)
    text_val = _clean_text(value)
    if not text_val:
        return _list_block("first_week_signals", "13. 前7天重点看4个信号", [], limit=4)
    lines = [part.strip() for part in re_split_standard(text_val) if part.strip()]
    return _list_block("first_week_signals", "13. 前7天重点看4个信号", lines or [text_val], limit=4)


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
        return "每周至少5天完成三杯节律；至少记录1次体重或围度/体感；结果不稳时先看睡眠、排便和晚餐边界。"
    if "体重" not in text_val and "围度" not in text_val and "体感" not in text_val:
        text_val = text_val.rstrip("。") + "；至少记录1次体重或围度/体感。"
    return text_val


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
