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
        "current_focus": "当前主线，与 shared_analysis.current_mainline 一致",
    },
    "one_sentence": "一句话结论",
    "core_conclusions": {
        "management_band": "业务管理带，只能取「A带｜修复塑形 / B带｜轻盈启动 / C带｜明确减脂 / D带｜系统减重 / 增肌增能带」之一。"
        "禁止写「需人工复核」等内部运营标签，用户看不到内部状态",
        "band_explanation": "用一句用户能听懂的话解释为什么属于这个管理带",
        "body_trend": "入营问卷反映的近3个月身体趋势，只能取：正在改善 / 基本稳定 / 缓慢变沉 / 明显恶化 / 信息不足",
        "trend_explanation": "用一句话解释近3个月趋势；信息不足时明确无法判断，不得补编长期变化",
        "trend_direct_evidence": "1条能证明变化方向的直接趋势事实，优先来自问卷「最近3个月体重变化」；"
        "当前BMI/体重/腰围属于单点状态，不能写在这里",
        "trend_support_signals": ["1-2条当前状态支持信号，例如腰腹体感、睡眠、精力、食欲边界、活动量"],
        "day28_task": "这28天先解决什么，用一句话说清",
        "long_term_defense": "长期如何避免回到原来的上升或失控轨迹，用一句话说清",
    },
    "goal_response": {
        "goal_type": "specific 或 general。specific=用户写了明确数字/明确结果；general=用户只勾选了期待变化或泛化诉求",
        "judgement_type": "reasonable 或 needs_adjustment。needs_adjustment=目标过激、当前不建议或需先安全复核",
        "content": "直接面向用户的一段完整回应。需要包含用户目标/期待、判断、基础目标、挑战目标或当前更适合方向、冲刺/下调条件；不要空泛，不承诺结果。",
        "user_goal": "兼容字段：复述用户原始目标或期待变化",
        "judgement": "兼容字段：直接回应目标是否适合当前阶段",
        "base_goal": "兼容字段：基础目标；A带/增肌增能带禁止任何减重数字",
        "challenge_goal": "兼容字段：挑战目标；A带/增肌增能带固定写不设置减重挑战目标",
        "conditions": "兼容字段：冲刺条件或下调条件",
    },
    "judgement_points": [
        "为什么这样判断，严格3-5条。每条固定写成「现象/数据 → 说明什么问题」，控制在18-32字，宁短不长。"
        "每条尽量引用不同类型的问卷证据（节律 / 饮食 / 睡眠 / 情绪 / 运动 / 体型），不要从同一字段反复推导同一结论"
    ],
    "current_state": "当前身体状态真正该关注什么",
    "insulin_resistance": "食欲边界、饭后困、腰腹堆积等生活方式信号。保留兼容字段名，但用户可见内容不得写成胰岛素抵抗诊断。",
    "leptin_sensitivity": "饱腹感、晚间失控、食欲边界等生活方式信号。保留兼容字段名，但用户可见内容不得写成瘦素抵抗诊断。",
    "hormone_stage": "年龄、睡眠、压力、恢复能力相关影响。保留兼容字段名，但用户可见内容不得写成内分泌或激素诊断。",
    "priority_direction": "当前最该先抓的方向",
    "priority_items": [
        "真正需要优先解决的三件事，严格3项，按优先级排列。"
        "每项写「问题 + 为什么它排在前面」，不写份量、次数、时长、时间点这类可量化执行参数——那些属于 week_actions"
    ],
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
    "week_actions": [
        "本周直接执行，4-5条。每条必须带数字和时间或场景，不解释原因（原因写在 priority_items）。"
        "至少包含1条运动建议，且运动建议必须写出类型+频率+时长+禁忌时间窗口。"
        "必须覆盖 priority_items 三件事之外的维度（产品节律 / 睡眠 / 情绪与环境设计中至少一项）"
    ],
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
    "current_mainline": "与 shared_analysis.current_mainline 一致的当前唯一主线，≤16字",
    "day28_goal": "这28天要完成的任务，≤48字，同时包含阶段任务和要观察的结果；没有明确数字目标时不得编造斤数或厘米数。",
    "current_phase_goal": "当前阶段目标，≤32字，用「先解决{核心问题}，不是{反面}」句式，聚焦排序而非平铺罗列，例如「先解决节律稳定，不是追求失控式掉秤」。",
    "principle": "执行总原则，≤28字，保持两段递进结构「先守{底盘动作}，再{放大动作}」，是通用指导语，不嵌入具体参数（时间点、拳数）。例如「先守底盘，再抓结果；先连续完成，再逐步加码」。",
    "product_schedule": {
        "morning_c": "早C个案剂量与时间，≤22字；需要暂停时明确写暂停",
        "noon_b": "午B个案剂量与时间，≤22字；需要减量、前移或暂停时明确写",
        "night_a": "晚A个案剂量与时间，≤22字；需要暂停时明确写",
    },
    "diet_rules": ["对应用户真实生活场景的饮食规则，最多3条，每条≤34字"],
    "exercise_rules": ["运动频率、单次时长、强度、动作和替代方案，最多2条，每条≤44字"],
    "weekly_plan": [
        {"week": "第1周", "core_task": "稳住底盘", "acceptance_standard": "1个短句，例如：按个案产品排班完成；晚餐主食≤1拳；完成计划内活动。"},
        {"week": "第2周", "core_task": "提高一致性", "acceptance_standard": "1个短句，例如：连续5天不漏早餐；晚餐后走15–20分钟；应酬日不加宵夜。"},
        {"week": "第3周", "core_task": "抓结果指标", "acceptance_standard": "1个短句，例如：每周2次轻力量；记录腰围和体重。"},
        {"week": "第4周", "core_task": "稳定复现", "acceptance_standard": "1个短句，例如：把睡前收口、计划内活动和2次轻力量做满。"},
    ],
    "daily_checkin": [
        {
            "order": "1-5 的整数，唯一、连续、严格递增；数组长度必须正好5项，禁止4项或6项",
            "category": "只能取 product / diet / activity / sleep / state；第1项固定 product，5项中必须有1项 activity",
            "title": "实际可回答的问题本身，针对这位用户的具体卡点定制。"
            "禁止写「每日固定打卡」「5项打卡」「打卡清单」等栏目标题或说明文字——那是章节名不是问题。"
            "不含「1.」「①」等显示序号（序号由程序按 order 渲染），不含括号注释（如「正向动作」「限制动作」），"
            "不含「今天/今日/今早/今晚/昨天/昨日/昨晚/当天/本日」等相对时间词。"
            "每项只能有一个回答意图：禁止「是否……并填写……」「是否……同时记录……」，"
            "也禁止一道题同时问时间和饮食结构；情绪稳定度评分和饭后困评分是两个独立问题，严禁合并成一项综合评分。"
            "涉及蔬菜只能写每日总量（如「蔬菜总量是否至少2拳」），禁止写「每餐蔬菜至少2拳」",
            "answer_type": "只能取 yes_no / score_1_5 / number / text，且必须与 title 匹配："
            "yes_no 对应「是否……」判断题；score_1_5 对应「……评分（1-5）」评分题；"
            "number 对应必须写明单位的填数题（如「晨起体重（kg）」）；text 对应必须用户描述的情况",
            "options": "answer_type=yes_no 固定 [\"是\",\"否\"]；score_1_5 固定 [\"1\",\"2\",\"3\",\"4\",\"5\"]；number 和 text 为空数组 []",
        }
    ],
    "weekly_standard": "每周通过标准，1句 ≤76字，必须包含4要素：个案产品排班完成情况 + 关键饮食动作 + 运动/步数达标 + 至少1次体重/围度/体感记录。减量或暂停用户不得写三杯完成要求。",
    "adjust_stop_conditions": "调整与停止条件，1句 ≤88字。必须说明什么情况减半、前移、暂停、联系班主任、及时就医。不要写成医疗方案。",
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
        _list_block("week_actions", "10. 本周直接执行", fields.get("week_actions"), limit=5),
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
            actions_val = _execution_sentence(actions_val, max_parts=2, max_len=46)

        table_rows.append(
            [
                _label(week),
                _editable(focus_val),
                _editable(actions_val),
            ]
        )

    blocks = [
        _execution_goal_summary_block(fields),
        _text_block(
            "current_phase_goal",
            "2. 当前阶段目标",
            _compact_field(fields.get("current_phase_goal"), max_len=32),
        ),
        _text_block(
            "principle",
            "3. 执行总原则",
            _compact_field(fields.get("principle"), max_len=28)
            or "先守底盘，再抓结果；先连续完成，再逐步加码。",
        ),
        _execution_product_block(fields),
        # 一页预算：饮食最多3条、运动最多2条，每条限长，避免真实模型输出把版面顶到第二页。
        _list_block("diet_rules", "5. 饮食规则", fields.get("diet_rules"), limit=3, max_item_len=34),
        _list_block("exercise_rules", "6. 运动规则", fields.get("exercise_rules"), limit=2, max_item_len=44),
        {"key": "weekly_plan", "title": "7. 四周执行表", "kind": "table", "rows": table_rows},
        _daily_checkin_block(fields.get("daily_checkin")),
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


def _list_block(
    key: str,
    title: str,
    items: Any,
    *,
    limit: int | None = None,
    max_item_len: int | None = None,
) -> dict[str, Any]:
    if not isinstance(items, list):
        items = [_text(items)] if _text(items) else []
    cleaned = [_clean_text(item) for item in items if _text(item)]
    if limit is not None:
        cleaned = cleaned[:limit]
    if max_item_len is not None:
        cleaned = [_compact_field(item, max_len=max_item_len) for item in cleaned]
    return {"key": key, "title": title, "kind": "list", "items": cleaned}


def _compact_field(value: Any, *, max_len: int) -> str:
    """按内容预算裁到限长，优先在句读边界断开，不硬切半个词。

    这是版面兜底，不是主约束：提示词已经给了同一套字数上限，这里只保证真实模型
    偶尔写长时仍然落在一页内。
    """

    text_val = _clean_text(value).replace("\n", " ")
    if not text_val or len(text_val) <= max_len:
        return text_val
    head = text_val[:max_len]
    for separator in ("；", "。", "，", "、", " "):
        cut = head.rfind(separator)
        if cut >= max_len // 2:
            return head[:cut].rstrip("；。，、 ")
    return head.rstrip("；。，、 ")


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
    # 趋势依据分两层：直接趋势证据能证明变化方向，支持信号只是当前状态，不能互相冒充。
    direct_evidence = _clean_text(value.get("trend_direct_evidence"))
    support_signals = value.get("trend_support_signals")
    if not isinstance(support_signals, list):
        support_signals = [support_signals] if _text(support_signals) else []
    support_text = "；".join(_clean_text(item) for item in support_signals[:2] if _text(item))
    if not direct_evidence and not support_text:
        legacy = value.get("trend_evidence")
        if isinstance(legacy, list) and legacy:
            direct_evidence = _clean_text(legacy[0])
            support_text = "；".join(_clean_text(item) for item in legacy[1:3] if _text(item))
    lines = [
        _text(fields.get("one_sentence")),
        f"管理带：{management_band}" if management_band else "",
        f"管理带解释：{_text(value.get('band_explanation'))}" if _text(value.get("band_explanation")) else "",
        f"近3个月身体趋势：{body_trend}",
        f"趋势解释：{_text(value.get('trend_explanation'))}" if _text(value.get("trend_explanation")) else "",
        f"近3个月趋势依据：{direct_evidence}" if direct_evidence else "",
        f"当前状态支持信号：{support_text}" if support_text else "",
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
    """目标摘要只留执行需要的两项。

    管理带、身体趋势、基础/挑战/长期目标是分析性结论，写在《个人能量重启诊断报告》里；
    在这份一页执行纸上重复展开会直接把版面顶到第二页。
    """

    lines = [
        f"28天任务：{_compact_field(fields.get('day28_goal') or fields.get('base_goal'), max_len=48)}",
        f"当前主线：{_compact_field(fields.get('current_mainline'), max_len=16)}",
    ]
    return _text_block(
        "goal_summary",
        "1. 目标摘要",
        "\n".join(line for line in lines if not line.endswith("：")),
    )


def _execution_product_block(fields: dict[str, Any]) -> dict[str, Any]:
    """产品排班压成一行：三段式内容短，占三行纯属浪费一页执行纸的版面。"""

    value = _dict(fields.get("product_schedule"))
    parts = [
        _product_slot_text("早C", value.get("morning_c")),
        _product_slot_text("午B", value.get("noon_b")),
        _product_slot_text("晚A", value.get("night_a")),
    ]
    return _text_block(
        "product_schedule",
        "4. 产品排班",
        " ｜ ".join(part for part in parts if part),
    )


def _product_slot_text(label: str, value: Any) -> str:
    """渲染「早C 半份随早餐」这类一段排班；模型自带前缀时不重复写标签。"""

    text_val = _clean_prefix(_clean_text(value), label)
    if not text_val:
        return ""
    return f"{label} {_compact_field(text_val, max_len=22)}"


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
    text_val = _execution_sentence(value, max_parts=3, max_len=76)
    if not text_val:
        return "每周至少5天完成个案产品排班和关键饮食动作；运动或步数达到计划；至少记录1次体重、围度或体感。"
    if "体重" not in text_val and "围度" not in text_val and "体感" not in text_val:
        text_val = text_val.rstrip("。") + "；至少记录1次体重或围度/体感。"
    return text_val


def _adjust_stop_conditions_text(value: Any) -> str:
    """调整与停止条件：一页预算可以砍长度，但不能把「及时就医」砍掉。

    这一栏的分句天然按严重度递增排列（减量 → 前移 → 暂停 → 联系班主任 → 就医），
    所以固定只留前两句，丢掉的正好是最重的那句安全边界，而落库的 content_md
    看不出被砍过。做法与 _weekly_standard_text 一致：截断后缺就医就补回来。
    """

    fallback_medical = "持续不适或出现胸痛、呼吸困难、明显心悸时，暂停执行并及时就医。"
    text_val = _execution_sentence(value, max_parts=2, max_len=88)
    if not text_val:
        return (
            "心慌、睡眠明显变差或胃部不适时，早C/午B先减半并前移；"
            f"{fallback_medical}"
        )
    if "就医" in text_val:
        return text_val
    medical = next(
        (part.strip() for part in re_split_standard(_clean_text(value)) if "就医" in part),
        "",
    )
    tail = medical.strip("；，,。 ") or fallback_medical.rstrip("。")
    return f"{text_val.rstrip('。')}；{tail}。"


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


def _daily_checkin_block(value: Any) -> dict[str, Any]:
    """每日打卡块：content 供 DOCX/Markdown 渲染，checkin_items 保留结构化定义。

    结构化项不参与正文渲染（渲染只读 content），但让 H5 每日行动问卷、五项追问清单和
    画像字段都能拿到同一份 answer_type/options，不必再靠题面关键词猜答题类型。
    """

    block = _text_block(
        "daily_checkin",
        "8. 每日固定打卡（每天只打这5项）",
        _daily_checkin_text(value),
    )
    if isinstance(value, list):
        items = [
            {
                "order": index,
                "category": str(item.get("category") or ""),
                "title": _clean_text(item.get("title") or item.get("label")),
                "answer_type": str(item.get("answer_type") or ""),
                "options": [str(option) for option in item.get("options") or []],
            }
            for index, item in enumerate(value, start=1)
            if isinstance(item, dict) and _clean_text(item.get("title") or item.get("label"))
        ]
        if items:
            block["checkin_items"] = items
    return block


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
            # 结构化项只取题面；显示序号由这里按顺序渲染，题面本身不带序号。
            raw = item.get("title") or item.get("label") if isinstance(item, dict) else item
            cleaned = _clean_text(raw)
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
