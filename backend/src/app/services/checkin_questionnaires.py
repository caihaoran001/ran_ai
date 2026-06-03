from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_setting import AppSetting


CHECKIN_SCHEDULE_SETTING_KEY = "checkin_questionnaire_schedule"


def default_checkin_questionnaire_templates() -> list[dict[str, Any]]:
    return [
        {
            "template_key": "daily_checkin_standard",
            "name": "每日打卡标准版",
            "title": "HB.AI 每日打卡｜标准版",
            "description": "第 1 天、第 3 天使用，用于建立基础状态和识别早期执行问题。",
            "fields": _standard_fields(),
        },
        {
            "template_key": "daily_checkin_simple",
            "name": "每日打卡简版",
            "title": "HB.AI 每日打卡｜简版",
            "description": "日常高频打卡使用，降低用户负担。",
            "fields": _simple_fields(),
        },
        {
            "template_key": "stage_review",
            "name": "7天阶段复盘",
            "title": "HB.AI 阶段复盘",
            "description": "第 7/14/21/28 天使用，用于总结周期变化和制定下阶段目标。",
            "fields": _stage_review_fields(),
        },
    ]


def default_checkin_schedule() -> dict[str, Any]:
    return {
        "enabled": True,
        "send_at": "09:00",
        "timezone": "Asia/Shanghai",
        "intake_submit_reply": {
            "enabled": True,
            "message": (
                "问卷已收到，正在生成专属方案。\n\n"
                "我们会基于你的填写结果生成：\n"
                "1.《个人能量重启处方单》\n"
                "2.《28天执行标准与打卡要求》\n\n"
                "生成完成后会第一时间发送给你，请稍等。"
            ),
        },
        "inactive_followup": {
            "enabled": True,
            "hours": 24,
            "message": "今天先稳住一个小动作就好。你这边如果遇到执行卡点，可以直接发群里，我来帮你一起拆小一点。",
        },
        "rules": [
            {
                "day": 1,
                "template_key": "daily_checkin_standard",
                "message": (
                    "早上好，今天是第 {day} 天。请优先完成您的今日【必填】每日行动：\n"
                    "👉 {personalized_link} (仅需10秒)\n\n"
                    "如今天时间充裕，建议也完成本次【选填】节点复盘，方便我们了解您的整体状况：\n"
                    "📝 {questionnaire_link}"
                ),
            },
            {
                "day": 3,
                "template_key": "daily_checkin_standard",
                "message": (
                    "早上好，今天是第 {day} 天。请优先完成您的今日【必填】每日行动：\n"
                    "👉 {personalized_link} (仅需10秒)\n\n"
                    "如今天时间充裕，建议也完成本次【选填】节点复盘，方便判定早期执行情况：\n"
                    "📝 {questionnaire_link}"
                ),
            },
            {
                "day": 7,
                "template_key": "stage_review",
                "message": (
                    "早上好，今天是第 {day} 天。请优先完成您的今日【必填】每日行动：\n"
                    "👉 {personalized_link} (仅需10秒)\n\n"
                    "今天也是首个 7 天复盘日！有空请务必填写一下本次【选填】节点复盘，以便我们为您总结本周期变化和制定下阶段目标：\n"
                    "📝 {questionnaire_link}"
                ),
            },
            {
                "day": 14,
                "template_key": "stage_review",
                "message": (
                    "早上好，今天是第 {day} 天。请优先完成您的今日【必填】每日行动：\n"
                    "👉 {personalized_link} (仅需10秒)\n\n"
                    "今天迎来了第 14 天复盘日！有空请务必填写一下本次【选填】节点复盘，我们将会为您调整健康策略：\n"
                    "📝 {questionnaire_link}"
                ),
            },
            {
                "day": 21,
                "template_key": "stage_review",
                "message": (
                    "早上好，今天是第 {day} 天。请优先完成您的今日【必填】每日行动：\n"
                    "👉 {personalized_link} (仅需10秒)\n\n"
                    "今天来到了第 21 天复盘日！胜利在望，有空请务必填写一下本次【选填】节点复盘，以便为您锁定制胜重点：\n"
                    "📝 {questionnaire_link}"
                ),
            },
            {
                "day": 28,
                "template_key": "stage_review",
                "message": (
                    "早上好，今天是第 {day} 天。请优先完成您的今日【必填】每日行动：\n"
                    "👉 {personalized_link} (仅需10秒)\n\n"
                    "恭喜您完成了 28 天的服务周期！请优先完成固定打卡，并在有空时填写本次【选填】节点复盘，我们将为您输出最终交付报告：\n"
                    "📝 {questionnaire_link}"
                ),
            },
        ],
        "daily_rule": {
            "enabled": True,
            "template_key": "daily_checkin_simple",
            "message": (
                "早上好，今天是第 {day} 天。请优先完成您的今日【必填】每日行动：\n"
                "👉 {personalized_link} (仅需10秒)\n\n"
                "如今天时间充裕，建议也填写一下本次【选填】节点复盘：\n"
                "📝 {questionnaire_link}"
            ),
            "skip_days": [1, 3, 7, 14, 21, 28],
        },
    }


async def get_checkin_questionnaire_schedule(session: AsyncSession) -> dict[str, Any]:
    setting = await session.get(AppSetting, CHECKIN_SCHEDULE_SETTING_KEY)
    if not setting:
        return default_checkin_schedule()
    return normalize_checkin_schedule(setting.value_json)


async def save_checkin_questionnaire_schedule(
    session: AsyncSession,
    schedule: dict[str, Any],
) -> dict[str, Any]:
    normalized = normalize_checkin_schedule(schedule)
    setting = await session.get(AppSetting, CHECKIN_SCHEDULE_SETTING_KEY)
    if setting:
        setting.value_json = normalized
    else:
        session.add(AppSetting(key=CHECKIN_SCHEDULE_SETTING_KEY, value_json=normalized))
    await session.commit()
    return normalized


def normalize_checkin_schedule(value: dict[str, Any] | None) -> dict[str, Any]:
    default = default_checkin_schedule()
    if not isinstance(value, dict):
        return default

    schedule = {**default, **value}
    schedule["enabled"] = bool(schedule.get("enabled", True))
    schedule["send_at"] = str(schedule.get("send_at") or default["send_at"])
    schedule["timezone"] = str(schedule.get("timezone") or default["timezone"])
    intake_submit_reply = schedule.get("intake_submit_reply")
    if not isinstance(intake_submit_reply, dict):
        intake_submit_reply = default["intake_submit_reply"]
    schedule["intake_submit_reply"] = {
        "enabled": intake_submit_reply.get("enabled", True) is not False,
        "message": str(
            intake_submit_reply.get("message")
            or default["intake_submit_reply"]["message"]
        ),
    }
    inactive_followup = schedule.get("inactive_followup")
    if not isinstance(inactive_followup, dict):
        inactive_followup = default["inactive_followup"]
    hours = inactive_followup.get("hours", default["inactive_followup"]["hours"])
    try:
        hours_int = int(hours)
    except (TypeError, ValueError):
        hours_int = default["inactive_followup"]["hours"]
    schedule["inactive_followup"] = {
        "enabled": inactive_followup.get("enabled", True) is not False,
        "hours": max(1, hours_int),
        "message": str(
            inactive_followup.get("message")
            or default["inactive_followup"]["message"]
        ),
    }

    rules = schedule.get("rules")
    if not isinstance(rules, list):
        rules = default["rules"]
    schedule["rules"] = [rule for rule in rules if isinstance(rule, dict)]

    daily_rule = schedule.get("daily_rule")
    if not isinstance(daily_rule, dict):
        daily_rule = default["daily_rule"]
    schedule["daily_rule"] = daily_rule
    return schedule


def _field(key: str, title: str, field_type: str = "SIMPLE", *, required: bool = False, options: list[str] | None = None, subtitle: str = "") -> dict[str, Any]:
    return {
        "id": key,
        "key": key,
        "type": field_type,
        "title": title,
        "label": title,
        "subtitle": subtitle,
        "required": required,
        "options": options or [],
    }


def _segment(key: str, title: str, subtitle: str = "") -> dict[str, Any]:
    return _field(key, title, "SEGMENTATION", subtitle=subtitle)


DEFAULT_PERSONALIZED_CHECKIN_FIELDS = [
    {
        "id": "personalized_weight",
        "key": "personalized_weight",
        "type": "SIMPLE",
        "title": "今日晨起体重（kg）",
        "label": "今日晨起体重（kg）",
        "subtitle": "",
        "required": True,
        "options": [],
    },
    {
        "id": "personalized_water",
        "key": "personalized_water",
        "type": "RADIO",
        "title": "今日饮水是否达到 2 升？",
        "label": "今日饮水是否达到 2 升？",
        "subtitle": "",
        "required": True,
        "options": ["是", "否"],
    },
    {
        "id": "personalized_sleep",
        "key": "personalized_sleep",
        "type": "RADIO",
        "title": "昨晚睡眠是否按计划收口？",
        "label": "昨晚睡眠是否按计划收口？",
        "subtitle": "",
        "required": True,
        "options": ["是", "否"],
    },
    {
        "id": "personalized_diet",
        "key": "personalized_diet",
        "type": "RADIO",
        "title": "今日晚餐主食是否控制在计划范围内？",
        "label": "今日晚餐主食是否控制在计划范围内？",
        "subtitle": "",
        "required": True,
        "options": ["是", "否"],
    },
    {
        "id": "personalized_exercise",
        "key": "personalized_exercise",
        "type": "RADIO",
        "title": "今日是否完成计划内步行或轻运动？",
        "label": "今日是否完成计划内步行或轻运动？",
        "subtitle": "",
        "required": True,
        "options": ["是", "否"],
    },
]


def personalized_checkin_fields_with_weight(weight_kg: float | None = None) -> list[dict[str, Any]]:
    if weight_kg is not None:
        try:
            water_liters = str(round((float(weight_kg) * 35 / 1000) * 2) / 2).rstrip("0").rstrip(".")
        except (ValueError, TypeError):
            water_liters = "2"
        fields = []
        for field in DEFAULT_PERSONALIZED_CHECKIN_FIELDS:
            if field["key"] == "personalized_water":
                field = {**field, "title": f"今日饮水是否达到 {water_liters} 升？", "label": f"今日饮水是否达到 {water_liters} 升？"}
            fields.append(field)
        return fields
    return [dict(f) for f in DEFAULT_PERSONALIZED_CHECKIN_FIELDS]


def _standard_fields() -> list[dict[str, Any]]:
    return [
        _segment("std_intro", "HB.AI 每日打卡｜标准版", "按健康管理优先级排序：先看结果，再看执行；先看节律，再看饮食；最后看体感和风险。"),
        _field("morning_weight", "今日晨起体重（kg）", required=True),
        _field("waist", "今日腰围/肚围（cm）", subtitle="腰围每 7 天测一次；如今天未测，填写「本日未测」。"),
        _field("morning_c", "早C完成情况", "RADIO", required=True, options=["已喝", "未喝"]),
        _field("noon_b", "午B完成情况", "RADIO", required=True, options=["已喝", "未喝"]),
        _field("night_a", "晚A完成情况", "RADIO", required=True, options=["已喝", "未喝"]),
        _field("sleep_time", "昨晚入睡时间"),
        _field("wake_time", "今天起床时间"),
        _field("sleep_quality", "睡眠质量", "RADIO", options=["好", "一般", "差"]),
        _field("breakfast", "早餐"),
        _field("lunch", "午餐"),
        _field("dinner", "晚餐"),
        _field("diet_risk", "今日是否吃了高糖/油炸/夜宵/酒/奶茶", "RADIO", options=["没有", "有"]),
        _field("diet_risk_detail", "如有，请具体说明"),
        _field("water", "今日饮水量", "RADIO", options=["少于1000ml", "1000-1500ml", "1500-2000ml", "2000ml以上"]),
        _field("bowel", "今日排便情况", "RADIO", options=["正常", "未排便", "偏稀", "便秘/排便困难"]),
        _field("appetite", "今日饥饿感/食欲", "RADIO", options=["明显下降", "略有下降", "无变化", "食欲很旺", "想吃甜食/碳水明显"]),
        _field("exercise", "今日运动/活动量", "SIMPLE", subtitle="例如：无运动 / 散步30分钟 / 有氧20分钟 / 力量训练20分钟。"),
        _field("body_feeling", "今日身体体感", "CHECKBOX", options=["身体发热", "精神变好", "水肿下降", "肚子更轻松", "睡眠变好", "暂无明显感觉", "其他"]),
        _field("discomfort", "今日是否有不适", "RADIO", required=True, options=["没有", "有"]),
        _field("discomfort_detail", "如有不适，请具体描述", "TEXT", subtitle="如有心慌、明显失眠、持续腹痛、严重腹泻、头晕等，请立即说明。"),
        _field("today_feedback", "今日一句话反馈", "TEXT"),
        _field("tomorrow_focus", "明天最重要的一件事", "RADIO", options=["早睡", "控制晚餐", "多喝水", "按时喝完早C午B晚A", "增加运动", "不吃夜宵", "其他"]),
    ]


def _simple_fields() -> list[dict[str, Any]]:
    return [
        _segment("simple_intro", "HB.AI 每日打卡｜简版", "适合群内高频执行，班主任每 3 天补一次详细版。"),
        _field("morning_weight", "晨起体重（kg）"),
        _field("morning_c", "早C", "RADIO", options=["已喝", "未喝"]),
        _field("noon_b", "午B", "RADIO", options=["已喝", "未喝"]),
        _field("night_a", "晚A", "RADIO", options=["已喝", "未喝"]),
        _field("sleep", "昨晚睡眠", "SIMPLE", subtitle="例如：23点睡，7点起，质量好/一般/差。"),
        _field("diet", "今日饮食", "RADIO", options=["干净", "一般", "失控"]),
        _field("water", "饮水量（ml）"),
        _field("bowel", "排便", "RADIO", options=["正常", "未排", "偏稀", "便秘"]),
        _field("appetite", "食欲", "RADIO", options=["下降", "无变化", "更旺"]),
        _field("exercise", "运动", "SIMPLE", subtitle="例如：无 / 有，30分钟。"),
        _field("body_feeling", "今日体感"),
        _field("discomfort", "今日是否不适", "SIMPLE", subtitle="无 / 有，请说明。"),
        _field("tomorrow_focus", "明天重点"),
    ]


def _stage_review_fields() -> list[dict[str, Any]]:
    return [
        _segment("stage_intro", "HB.AI 阶段复盘", "第 7/14/21/28 天使用，用于总结周期变化、判断执行质量，并制定下一个 7 天目标。"),
        _field("current_weight", "当前体重（kg）"),
        _field("current_waist", "当前腰围/肚围（cm）"),
        _field("avg_sleep", "过去7天平均睡眠", "SIMPLE", subtitle="例如：大约23:30入睡，质量变好/无变化/变差。"),
        _field("diet_execution", "饮食执行情况", "RADIO", options=["很干净", "基本可以", "经常失控"]),
        _field("diet_main_issue", "饮食主要问题"),
        _field("water_execution", "饮水执行情况", "RADIO", options=["达标", "一般", "明显不足"]),
        _field("bowel_status", "排便情况", "RADIO", options=["明显改善", "基本正常", "仍然便秘", "偏稀/不稳定"]),
        _field("appetite_status", "食欲和碳水欲望", "RADIO", options=["明显下降", "略有下降", "无变化", "更旺"]),
        _field("exercise_status", "运动情况", "RADIO", options=["无运动", "每周1-2次", "每周3次以上", "每天都有活动"]),
        _field("biggest_positive_change", "最大正向变化", "TEXT"),
        _field("biggest_blocker", "最大卡点", "TEXT"),
        _field("next_action_focus", "下一个7天行动重点", "TEXT"),
    ]
