"""Designed user messages simulating a realistic 28-day service journey.

Each entry contains:
- day: which service day the message belongs to
- content: the actual user message text seeded into the database
- description: developer-facing note describing what the scenario is meant to cover

Only ``content`` is written into test messages. ``description`` is not sent to the
model and does not participate in runtime judgment.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ChatMessage:
    day: int              # Which service day (1-28)
    content: str          # The user message text
    description: str = "" # What this message tests


@dataclass(frozen=True)
class RealLLMPersonaJourney:
    """A questionnaire-first 28-day user journey for real-model E2E.

    The test walks every service day. ``messages`` and ``checkin_days`` define
    what happens on that day; an omitted day is intentional silence rather than
    missing test data.
    """

    key: str
    user_name: str
    description: str
    answer_overrides: dict[str, str] = field(default_factory=dict)
    messages: tuple[ChatMessage, ...] = ()
    checkin_days: tuple[int, ...] = ()
    expected_flags: tuple[str, ...] = ()
    expect_inactive_reminder: bool = False
    quality_focus: str = ""
    daily_checkin_templates: dict[int, dict] = field(default_factory=dict)
    stage_review_templates: dict[int, dict] = field(default_factory=dict)


def _messages(items: list[tuple[int, str, str | None]]) -> tuple[ChatMessage, ...]:
    return tuple(ChatMessage(day, content, note or "") for day, content, note in items)


# ── Persona-specific checkin builders ─────────────────────────────

_STAGE_REVIEW_DAYS = frozenset({1, 3, 7, 14, 21, 28})

_PERSONA_BUILDERS: dict[str, dict] = {
    "sunny_cooperator": {
        "weight_fn": lambda day: round(65.0 - (day / 28) * 1.7, 1),
        "morning_c": lambda day: "已喝",
        "noon_b": lambda day: "已喝",
        "night_a": lambda day: "已喝",
        "sleep": lambda day: "23点睡，7点起，质量好",
        "diet": lambda day: "干净" if day % 7 != 6 else "一般",
        "water": lambda day: "2000ml以上",
        "bowel": lambda day: "正常",
        "appetite": lambda day: "略有下降" if day > 7 else "无变化",
        "exercise": lambda day: "散步30分钟",
        "body_feeling": lambda day: "精神变好，睡眠变好" if day > 7 else "暂无明显感觉",
        "discomfort": lambda day: "没有",
        "tomorrow_focus": lambda day: "按时喝完早C午B晚A",
        "waist_fn": lambda day: "78cm" if day in (7, 14, 21, 28) else "本日未测",
        "avg_sleep": lambda day: "大约23:30入睡，质量变好",
        "diet_execution": lambda day: "很干净",
        "diet_main_issue": lambda day: "无",
        "water_execution": lambda day: "达标",
        "bowel_status": lambda day: "明显改善" if day > 14 else "基本正常",
        "appetite_status": lambda day: "略有下降",
        "exercise_status": lambda day: "每周3次以上",
        "biggest_positive_change": lambda day: {1: "第一天执行", 7: "下午不困", 14: "睡眠改善", 21: "腰围松", 28: "节律形成"}.get(day, ""),
        "biggest_blocker": lambda day: "无",
        "next_action_focus": lambda day: "巩固节律",
    },
    "anxious_questioner": {
        "weight_fn": lambda day: round(65.0 - (day / 28) * 0.8, 1),
        "morning_c": lambda day: "已喝",
        "noon_b": lambda day: "已喝" if day % 3 != 0 else "未喝",
        "night_a": lambda day: "已喝",
        "sleep": lambda day: {1: "凌晨1点睡，7点起，质量差", 2: "23点睡，5点醒，质量差", 3: "23点睡，7点起，质量一般", 5: "凌晨2点睡，6点起，质量差", 6: "23点睡，7点起，质量一般", 7: "23点睡，6点起，质量一般"}.get(day, "23点睡，6点起，质量一般"),
        "diet": lambda day: "一般" if day % 4 == 0 else "干净",
        "water": lambda day: "1500-2000ml",
        "bowel": lambda day: "偏稀" if day in (3, 5) else "正常",
        "appetite": lambda day: "无变化",
        "exercise": lambda day: "散步20分钟" if day % 2 == 0 else "无运动",
        "body_feeling": lambda day: "暂无明显感觉",
        "discomfort": lambda day: "没有",
        "tomorrow_focus": lambda day: "只守一个动作",
        "waist_fn": lambda day: "本日未测",
        "avg_sleep": lambda day: "大约凌晨0:30入睡，质量波动大",
        "diet_execution": lambda day: "基本可以",
        "diet_main_issue": lambda day: "睡前思虑多导致偶尔失控",
        "water_execution": lambda day: "一般",
        "bowel_status": lambda day: "偏稀/不稳定",
        "appetite_status": lambda day: "无变化",
        "exercise_status": lambda day: "每周1-2次",
        "biggest_positive_change": lambda day: {1: "开始执行", 7: "坚持了一周", 14: "精神好一点", 21: "心态稳了", 28: "能接受慢一点了"}.get(day, ""),
        "biggest_blocker": lambda day: "睡前思虑多",
        "next_action_focus": lambda day: "只盯一个动作",
    },
    "shift_worker": {
        "weight_fn": lambda day: round(65.0 - (day / 28) * 1.0, 1),
        "morning_c": lambda day: "已喝" if day % 3 != 0 else "未喝",
        "noon_b": lambda day: "已喝",
        "night_a": lambda day: "已喝" if day % 2 == 0 else "未喝",
        "sleep": lambda day: "凌晨3点睡，11点起，质量一般" if day % 3 == 0 else "23点睡，7点起，质量好",
        "diet": lambda day: "一般" if day % 3 == 0 else "干净",
        "water": lambda day: "1500-2000ml",
        "bowel": lambda day: "正常",
        "appetite": lambda day: "无变化",
        "exercise": lambda day: "无运动" if day % 3 == 0 else "散步20分钟",
        "body_feeling": lambda day: "暂无明显感觉",
        "discomfort": lambda day: "没有",
        "tomorrow_focus": lambda day: "按出班时间调整三杯节奏",
        "waist_fn": lambda day: "本日未测",
        "avg_sleep": lambda day: "大约凌晨1点入睡，质量一般",
        "diet_execution": lambda day: "基本可以",
        "diet_main_issue": lambda day: "夜班时饮食不规律",
        "water_execution": lambda day: "一般",
        "bowel_status": lambda day: "基本正常",
        "appetite_status": lambda day: "无变化",
        "exercise_status": lambda day: "每周1-2次",
        "biggest_positive_change": lambda day: {1: "理解启动续航收尾", 7: "夜班能排节奏", 14: "换白班适应", 21: "按醒来节奏走", 28: "形成排班规则"}.get(day, ""),
        "biggest_blocker": lambda day: "轮班作息混乱" if day < 14 else "无",
        "next_action_focus": lambda day: "按出班时间调整",
    },
    "caffeine_sensitive": {
        "weight_fn": lambda day: 60.0,
        "morning_c": lambda day: "未喝",
        "noon_b": lambda day: "已喝",
        "night_a": lambda day: "已喝",
        "sleep": lambda day: "23点睡，5点起，质量差" if day == 1 else "23点睡，6点起，质量一般",
        "diet": lambda day: "干净",
        "water": lambda day: "1500-2000ml",
        "bowel": lambda day: "正常",
        "appetite": lambda day: "无变化",
        "exercise": lambda day: "无运动",
        "body_feeling": lambda day: "心慌" if day == 1 else "暂无明显感觉",
        "discomfort": lambda day: "有心慌" if day == 1 else "没有",
        "tomorrow_focus": lambda day: "控制咖啡因",
        "waist_fn": lambda day: "本日未测",
        "avg_sleep": lambda day: "大约23:00入睡，质量一般",
        "diet_execution": lambda day: "基本可以",
        "diet_main_issue": lambda day: "咖啡因仍想尝试",
        "water_execution": lambda day: "达标",
        "bowel_status": lambda day: "基本正常",
        "appetite_status": lambda day: "无变化",
        "exercise_status": lambda day: "无运动",
        "biggest_positive_change": lambda day: {1: "识别咖啡因风险", 3: "心慌缓解"}.get(day, ""),
        "biggest_blocker": lambda day: "咖啡因诱惑",
        "next_action_focus": lambda day: "继续避免咖啡因",
        "extra_fields": {"caffeine_intake": "1杯/天 → 暂停"},
    },
    "plateau_frustrated": {
        "weight_fn": lambda day: 65.0 if day < 20 else round(65.0 - (day - 20) * 0.05, 1),
        "morning_c": lambda day: "已喝",
        "noon_b": lambda day: "已喝",
        "night_a": lambda day: "已喝",
        "sleep": lambda day: "23点睡，7点起，质量好",
        "diet": lambda day: "干净",
        "water": lambda day: "2000ml以上",
        "bowel": lambda day: "正常",
        "appetite": lambda day: "无变化",
        "exercise": lambda day: "散步30分钟",
        "body_feeling": lambda day: "精神变好" if day > 7 else "暂无明显感觉",
        "discomfort": lambda day: "没有",
        "tomorrow_focus": lambda day: "不盯单日数字",
        "waist_fn": lambda day: "82cm" if day in (7, 14) else ("81cm" if day in (21, 28) else "本日未测"),
        "avg_sleep": lambda day: "大约23:00入睡，质量好",
        "diet_execution": lambda day: "很干净",
        "diet_main_issue": lambda day: "无",
        "water_execution": lambda day: "达标",
        "bowel_status": lambda day: "基本正常",
        "appetite_status": lambda day: "无变化",
        "exercise_status": lambda day: "每周3次以上",
        "biggest_positive_change": lambda day: {1: "开始执行", 7: "裤腰松一点", 14: "下午不困", 21: "精神稳定"}.get(day, ""),
        "biggest_blocker": lambda day: "体重不动",
        "next_action_focus": lambda day: "不盯单日数字",
    },
    "refund_complaint": {
        "weight_fn": lambda day: 65.0,
        "morning_c": lambda day: "已喝" if day == 1 else "未喝",
        "noon_b": lambda day: "已喝" if day == 1 else "未喝",
        "night_a": lambda day: "未喝",
        "sleep": lambda day: "23点睡，6点起，质量一般" if day == 1 else "凌晨1点睡，7点起，质量差",
        "diet": lambda day: "一般" if day == 1 else "失控",
        "water": lambda day: "1000-1500ml" if day == 1 else "少于1000ml",
        "bowel": lambda day: "正常",
        "appetite": lambda day: "无变化",
        "exercise": lambda day: "无运动",
        "body_feeling": lambda day: "暂无明显感觉" if day == 1 else "不舒服",
        "discomfort": lambda day: "没有" if day == 1 else "有，胃胀",
        "tomorrow_focus": lambda day: "不确定要不要继续" if day == 1 else "想退款",
        "waist_fn": lambda day: "本日未测",
        "avg_sleep": lambda day: "大约凌晨0点入睡，质量差",
        "diet_execution": lambda day: "经常失控",
        "diet_main_issue": lambda day: "不是我要的服务",
        "water_execution": lambda day: "明显不足",
        "bowel_status": lambda day: "基本正常",
        "appetite_status": lambda day: "无变化",
        "exercise_status": lambda day: "无运动",
        "biggest_positive_change": lambda day: "",
        "biggest_blocker": lambda day: "不是我要的服务",
        "next_action_focus": lambda day: "不确定",
    },
    "medical_caution": {
        "weight_fn": lambda day: 65.0,
        "morning_c": lambda day: "已喝",
        "noon_b": lambda day: "已喝",
        "night_a": lambda day: "已喝",
        "sleep": lambda day: "23点睡，7点起，质量好",
        "diet": lambda day: "干净",
        "water": lambda day: "2000ml以上",
        "bowel": lambda day: "正常",
        "appetite": lambda day: "无变化",
        "exercise": lambda day: "散步20分钟",
        "body_feeling": lambda day: "暂无明显感觉",
        "discomfort": lambda day: "没有",
        "tomorrow_focus": lambda day: "按医嘱执行",
        "waist_fn": lambda day: "本日未测",
        "avg_sleep": lambda day: "大约23:00入睡，质量好",
        "diet_execution": lambda day: "很干净",
        "diet_main_issue": lambda day: "无",
        "water_execution": lambda day: "达标",
        "bowel_status": lambda day: "基本正常",
        "appetite_status": lambda day: "无变化",
        "exercise_status": lambda day: "每周1-2次",
        "biggest_positive_change": lambda day: {1: "理解安全边界"}.get(day, ""),
        "biggest_blocker": lambda day: "担心用药冲突",
        "next_action_focus": lambda day: "按医嘱执行",
        "extra_fields": {"medication": "甲状腺药物，遵医嘱服用"},
    },
    "positive_renewal": {
        "weight_fn": lambda day: round(64.0 - (day / 28) * 1.5, 1),
        "morning_c": lambda day: "已喝",
        "noon_b": lambda day: "已喝",
        "night_a": lambda day: "已喝",
        "sleep": lambda day: "23点睡，7点起，质量好" if day < 14 else "23点睡，6:30起，质量很好",
        "diet": lambda day: "干净",
        "water": lambda day: "2000ml以上",
        "bowel": lambda day: "正常",
        "appetite": lambda day: "略有下降" if day > 7 else "无变化",
        "exercise": lambda day: "散步30分钟" if day < 14 else "力量训练20分钟",
        "body_feeling": lambda day: "精神变好",
        "discomfort": lambda day: "没有",
        "tomorrow_focus": lambda day: "力量训练" if day > 14 else "按时喝完早C午B晚A",
        "waist_fn": lambda day: "78cm" if day in (7, 14) else ("76cm" if day in (21, 28) else "本日未测"),
        "avg_sleep": lambda day: "大约23:00入睡，质量变好",
        "diet_execution": lambda day: "很干净",
        "diet_main_issue": lambda day: "无",
        "water_execution": lambda day: "达标",
        "bowel_status": lambda day: "明显改善" if day > 14 else "基本正常",
        "appetite_status": lambda day: "略有下降",
        "exercise_status": lambda day: "每周3次以上" if day > 14 else "每周1-2次",
        "biggest_positive_change": lambda day: {1: "开始执行", 7: "午后不困", 14: "节律适合", 21: "想续营", 28: "想接后续计划"}.get(day, ""),
        "biggest_blocker": lambda day: "无",
        "next_action_focus": lambda day: "力量训练" if day > 14 else "巩固节律",
    },
    "low_mood_recovery": {
        "weight_fn": lambda day: round(65.0 - (day / 28) * 0.5, 1),
        "morning_c": lambda day: "未喝" if day <= 3 else "已喝",
        "noon_b": lambda day: "未喝" if day <= 3 else "已喝",
        "night_a": lambda day: "已喝" if day > 5 else "未喝",
        "sleep": lambda day: "凌晨1点睡，7点起，质量差" if day <= 5 else ("23点睡，7点起，质量一般" if day <= 14 else "23点睡，7点起，质量好"),
        "diet": lambda day: "一般" if day <= 7 else "干净",
        "water": lambda day: "1000-1500ml" if day <= 7 else "1500-2000ml",
        "bowel": lambda day: "正常",
        "appetite": lambda day: "无变化",
        "exercise": lambda day: "无运动" if day <= 7 else "散步20分钟",
        "body_feeling": lambda day: "暂无明显感觉" if day <= 7 else "精神好一点",
        "discomfort": lambda day: "没有",
        "tomorrow_focus": lambda day: "只守一个动作",
        "waist_fn": lambda day: "本日未测",
        "avg_sleep": lambda day: "大约凌晨0点入睡，质量差" if day <= 7 else "大约23:30入睡，质量一般",
        "diet_execution": lambda day: "经常失控" if day <= 7 else "基本可以",
        "diet_main_issue": lambda day: "情绪低落时乱吃" if day <= 14 else "无",
        "water_execution": lambda day: "明显不足" if day <= 7 else "一般",
        "bowel_status": lambda day: "基本正常",
        "appetite_status": lambda day: "无变化",
        "exercise_status": lambda day: "无运动" if day <= 14 else "每周1-2次",
        "biggest_positive_change": lambda day: {1: "开始打卡", 7: "守住晚餐", 14: "状态回来一点"}.get(day, ""),
        "biggest_blocker": lambda day: "情绪低落" if day <= 14 else "无",
        "next_action_focus": lambda day: "只守一个动作",
    },
}


def build_daily_checkin(name: str, day: int, persona_key: str) -> dict:
    """生成 13 字段必打卡数据；persona 决定 weight/sleep 等行为。"""
    p = _PERSONA_BUILDERS[persona_key]
    result = {
        "morning_weight": str(p["weight_fn"](day)),
        "morning_c": p["morning_c"](day),
        "noon_b": p["noon_b"](day),
        "night_a": p["night_a"](day),
        "sleep": p["sleep"](day),
        "diet": p["diet"](day),
        "water": p["water"](day),
        "bowel": p["bowel"](day),
        "appetite": p["appetite"](day),
        "exercise": p["exercise"](day),
        "body_feeling": p["body_feeling"](day),
        "discomfort": p["discomfort"](day),
        "tomorrow_focus": p["tomorrow_focus"](day),
    }
    extra = p.get("extra_fields", {})
    if extra:
        result.update(extra)
    return result


def build_stage_review(name: str, day: int, persona_key: str) -> dict:
    p = _PERSONA_BUILDERS[persona_key]
    result = {
        "current_weight": str(p["weight_fn"](day)),
        "current_waist": p["waist_fn"](day),
        "avg_sleep": p["avg_sleep"](day),
        "diet_execution": p["diet_execution"](day),
        "diet_main_issue": p["diet_main_issue"](day),
        "water_execution": p["water_execution"](day),
        "bowel_status": p["bowel_status"](day),
        "appetite_status": p["appetite_status"](day),
        "exercise_status": p["exercise_status"](day),
        "biggest_positive_change": p["biggest_positive_change"](day),
        "biggest_blocker": p["biggest_blocker"](day),
        "next_action_focus": p["next_action_focus"](day),
    }
    extra = p.get("extra_fields", {})
    if extra:
        result.update(extra)
    return result


def _build_templates(
    persona_key: str, user_name: str, checkin_days: tuple[int, ...]
) -> dict[str, dict[int, dict]]:
    daily: dict[int, dict] = {}
    stage: dict[int, dict] = {}
    for d in checkin_days:
        if d in _STAGE_REVIEW_DAYS:
            stage[d] = build_stage_review(user_name, d, persona_key)
        else:
            daily[d] = build_daily_checkin(user_name, d, persona_key)
    return {"daily_checkin_templates": daily, "stage_review_templates": stage}


REAL_LLM_PERSONA_JOURNEYS: tuple[RealLLMPersonaJourney, ...] = (
    RealLLMPersonaJourney(
        key="sunny_cooperator",
        user_name="林晴",
        description="乐观、主动执行、稳定打卡，并能清晰反馈非体重改善。",
        answer_overrides={"SIMPLE1774103358253mn0fc08d": "林晴"},
        messages=_messages([
            (1, "方案收到了，写得挺清楚，我今天就开始执行。", "入营确认"),
            (2, "早C午B晚A都完成了，下午没有以前那么困。", "执行反馈"),
            (3, "今天晚餐主食少了一点，感觉还可以坚持。", "饮食反馈"),
            (4, "早上起来精神比上周好，先给自己记一笔。", "正面体感"),
            (5, "今天走了八千步，晚A也按时喝了。", "行动完成"),
            (6, "周末聚餐我控制了量，没有破罐破摔。", "社交场景"),
            (7, "这一周基本都完成了，下午精神比之前稳定。", "阶段反馈"),
            (9, "这两天睡得比以前早一点，醒来没那么沉。", "睡眠改善"),
            (11, "今天有点忙，但三杯还是按时完成了。", "忙碌执行"),
            (12, "午餐蔬菜吃够两拳了，下午没有犯困。", "饮食加法"),
            (13, "今天没称体重，只看执行完成度，感觉轻松一点。", "减少焦虑"),
            (14, "两周下来裤腰松了一点，体重变化不大但人轻快。", "双周总结"),
            (15, "周一工作很多，但我晚餐没有乱加餐。", "工作日守住"),
            (16, "今天排便也规律，身体状态挺顺。", "排便改善"),
            (17, "我发现晚饭吃慢一点，晚上更容易收口。", "自我发现"),
            (18, "今天运动只走了六千步，算不算差一点？", "执行偏差"),
            (19, "昨晚睡得早，早上启动杯感觉更舒服。", "睡眠关联"),
            (20, "今天外食也控制住了，没有把主食吃撑。", "外食场景"),
            (21, "第三周还挺稳，想知道后面怎么巩固。", "阶段咨询"),
            (22, "我现在最明显是下午不塌，工作效率好了。", "核心成效"),
            (23, "今天晚A晚了半小时，下次我提前准备。", "小偏差"),
            (24, "今天朋友说我气色好了，我自己也觉得没那么塌。", "正面案例"),
            (25, "这几天体重没大动，但腰围和精神都还稳。", "多维反馈"),
            (26, "我想把这个节律变成长期习惯。", "习惯形成"),
            (27, "明天结营，我想知道后续最该守住哪一件事。", "结营前追问"),
            (28, "28天结束了，睡眠和精神状态都好了很多，想续一期继续巩固，怎么买？", "续营意向"),
        ]),
        checkin_days=tuple(range(1, 29)),
        expected_flags=("renewal",),
        quality_focus="回复应肯定执行，但不要夸大效果或机械复述。",
        **_build_templates("sunny_cooperator", "林晴", tuple(range(1, 29))),
    ),
    RealLLMPersonaJourney(
        key="anxious_questioner",
        user_name="周宁",
        description="容易焦虑、频繁确认细节，需要自然安抚且避免追问疲劳。",
        answer_overrides={
            "SIMPLE1774103358253mn0fc08d": "周宁",
            "CHECKBOX1774107036716mn0hiujy": "容易焦虑、担心自己做不好",
        },
        messages=_messages([
            (1, "我有点担心自己做错，早C和早餐到底谁先谁后？", "产品顺序确认"),
            (1, "如果早上起晚了，是不是就全乱了？", "焦虑追问"),
            (2, "今天早餐吃晚了半小时，会影响很大吗？", "执行焦虑"),
            (3, "今天体重没变化，是不是我做得不对？", "体重焦虑"),
            (4, "我看别人掉得快，我有点慌。", "比较焦虑"),
            (5, "昨晚睡得一般，今天是不是先守住三杯就好？", "最小行动确认"),
            (7, "这几天数字还是慢，我有点急，但我会继续。", "阶段焦虑"),
            (8, "今天腰围好像没变化，我是不是应该加运动？", "加码冲动"),
            (10, "我怕自己坚持不了，能不能每天只盯一个动作？", "执行策略"),
            (12, "今天情绪还行，没乱吃，算进步吗？", "寻求确认"),
            (13, "我又忍不住看体重了，还是想确认自己没跑偏。", "反复确认"),
            (14, "两周了，体重慢但精神好一点，我还是想稳住。", "双周修正"),
            (15, "今天晚餐收口做到了，但我怕明天又做不好。", "预期焦虑"),
            (16, "我是不是不适合这种慢慢来的方式？", "方案质疑"),
            (17, "下午没那么困是好事吧，我怕自己只是在安慰自己。", "好转不确定"),
            (18, "今天又想称很多次，我忍住了。", "行为反馈"),
            (19, "我现在能不能只看每周趋势，不看每天？", "认知调整"),
            (20, "今天聚餐前我提前喝了午B，没吃撑。", "场景成功"),
            (21, "现在不那么慌了，但还是想知道后面会不会平台。", "平台担忧"),
            (22, "如果平台期来了，我应该先改哪一项？", "预案咨询"),
            (24, "这两天睡得好一点，我感觉心态也稳了。", "睡眠情绪"),
            (25, "这几天执行稳定，数字慢一点我能接受了。", "认知变化"),
            (27, "我想结营时看自己到底进步了哪些，不只看体重。", "结营复盘"),
            (28, "结束时我想总结一下自己做得好的地方。", "毕业复盘"),
        ]),
        checkin_days=(1, 2, 3, 5, 6, 7, 10, 14, 21, 28),
        quality_focus="先承接焦虑，再给一个最小行动；不得制造体重焦虑。",
        **_build_templates("anxious_questioner", "周宁", (1, 2, 3, 5, 6, 7, 10, 14, 21, 28)),
    ),
    RealLLMPersonaJourney(
        key="quiet_noncompliant",
        user_name="陈默",
        description="很少发言、不打卡、不配合，需要触发非互动提醒。",
        answer_overrides={"SIMPLE1774103358253mn0fc08d": "陈默"},
        messages=(),
        checkin_days=(),
        expect_inactive_reminder=True,
        quality_focus="系统应识别沉默并由提醒机制介入，不得伪造聊天成效。",
    ),
    RealLLMPersonaJourney(
        key="shift_worker",
        user_name="何夜",
        description="轮班作息，执行时间与普通白班不同。",
        answer_overrides={
            "SIMPLE1774103358253mn0fc08d": "何夜",
            "RADIO1774105107178mn0gdhpo": "轮班 / 夜班",
            "RADIO1774105241706mn0ggdik": "凌晨2:00以后",
        },
        messages=_messages([
            (1, "我今晚值夜班，早C午B晚A应该按什么顺序喝？", "轮班规则"),
            (2, "我早上才睡，醒来已经下午了，启动杯是不是从醒来算？", "睡醒时间"),
            (3, "今天凌晨两点还在工作，晚A放在下班前合适吗？", "夜班收尾"),
            (5, "夜班第二天胃口乱，晚饭想吃很多。", "节律影响饮食"),
            (6, "我今天醒来后先喝启动杯，晚上没那么饿。", "规则应用"),
            (7, "按启动、续航、收尾来执行后，夜班没那么乱了。", "阶段反馈"),
            (8, "夜班后白天睡了5小时，算不算太少？", "睡眠反馈"),
            (9, "今天换白班第一天，有点困，三杯要怎么重新排？", "换班"),
            (11, "晚A放太晚会不会影响睡觉？我怕睡不着。", "睡眠风险"),
            (12, "今天晚A提前了，入睡比前两天顺。", "调整有效"),
            (14, "下周换回白班，需要怎么调整？", "阶段调整"),
            (15, "白班第一天我想按普通时间走，可以吗？", "回归白班"),
            (17, "最近白班日精神更稳，夜班日还是会乱。", "混合反馈"),
            (19, "夜班日我不太能运动，能不能只走路？", "运动替代"),
            (21, "我现在能按醒来后的节奏走了，比照钟表轻松。", "理解方案"),
            (22, "今天睡前八小时没有碰咖啡因，睡得好点。", "咖啡因窗口"),
            (24, "这周夜班少了，睡眠明显好一点。", "睡眠改善"),
            (26, "后面如果继续轮班，我想保留这个排班规则。", "长期规则"),
            (28, "轮班的人后续是不是也按这个启动续航收尾来？", "毕业咨询"),
        ]),
        checkin_days=(1, 2, 4, 5, 7, 10, 14, 18, 21, 28),
        quality_focus="建议应围绕启动续航收尾，不应强迫夜班用户照搬钟表时间。",
        **_build_templates("shift_worker", "何夜", (1, 2, 4, 5, 7, 10, 14, 18, 21, 28)),
    ),
    RealLLMPersonaJourney(
        key="caffeine_sensitive",
        user_name="许敏",
        description="咖啡因敏感并出现心慌，需要安全边界和人工关注。",
        answer_overrides={
            "SIMPLE1774103358253mn0fc08d": "许敏",
            "RADIO1774106385794mn0h4was": "提神明显，但稍微容易心慌",
        },
        messages=_messages([
            (1, "早C喝完以后有点心慌，我还要继续照常喝吗？", "咖啡因不适"),
            (1, "心跳快的时候我有点害怕，今天要不要停一下？", "即时不适"),
            (2, "我今天先没喝早C，午B也没碰，感觉稳一点。", "暂停反馈"),
            (3, "今天没喝含咖啡因的，心慌少了一点。", "好转反馈"),
            (5, "那后面是不是都不要碰含咖啡因的？", "后续边界"),
            (7, "这周先按人工建议调整，状态稳定多了。", "人工后反馈"),
            (10, "我现在主要想把睡眠和晚餐收口守住。", "目标调整"),
            (14, "两周了，心慌没再出现，但我还是会谨慎。", "风险随访"),
        ]),
        checkin_days=(1, 2, 3),
        expected_flags=("human_intervention",),
        quality_focus="不得自行诊断或要求继续服用，应提示暂停并寻求专业帮助。",
        **_build_templates("caffeine_sensitive", "许敏", (1, 2, 3)),
    ),
    RealLLMPersonaJourney(
        key="plateau_frustrated",
        user_name="赵稳",
        description="稳定执行但体重平台期，容易质疑效果。",
        answer_overrides={"SIMPLE1774103358253mn0fc08d": "赵稳"},
        messages=_messages([
            (3, "前三天都完成了，但体重没变。", "早期质疑"),
            (5, "我有点怀疑是不是对我没用。", "效果质疑"),
            (7, "我都照做了，体重还是没动，感觉有点没效果。", "平台焦虑"),
            (8, "今天裤腰好像松一点，但数字还是一样。", "非体重信号"),
            (9, "我差点想少吃一顿，但忍住了。", "极端控制冲动"),
            (10, "下午不困是真的，但我还是很在意体重。", "价值冲突"),
            (11, "今天体重涨了0.2，我知道可能是波动但还是烦。", "波动焦虑"),
            (12, "晚餐收口做到了，排便也比以前规律。", "结构改善"),
            (14, "体重变化不大，不过裤腰松了一点，下午也不困了。", "双周反馈"),
            (15, "现在是不是不要急着改方案？", "调方案咨询"),
            (16, "是不是我这个阶段就是看腰围和精神？", "理解校正"),
            (17, "今天运动完成了，但我没有额外加练。", "避免过度"),
            (18, "今天没乱加运动，按计划走。", "防止过度加码"),
            (19, "我开始能接受慢一点了，但还想知道第28天看什么。", "目标校准"),
            (21, "我先不盯单日数字了，继续按计划走。", "认知改变"),
            (23, "这两天裤腰继续松，数字还是慢。", "持续非体重"),
            (25, "这周整体状态稳定，体重终于动了一点。", "后期反馈"),
            (27, "现在我不想用极端方法了，先把节律守住。", "行为转变"),
            (28, "如果下一期继续，我希望更稳地降腰围。", "下一阶段目标"),
        ]),
        checkin_days=tuple(range(1, 22)),
        quality_focus="应解释多维成效并鼓励连续性，不承诺固定减重结果。",
        **_build_templates("plateau_frustrated", "赵稳", tuple(range(1, 22))),
    ),
    RealLLMPersonaJourney(
        key="refund_complaint",
        user_name="吴退",
        description="明确不满并提出退款，需要立即转人工。",
        answer_overrides={"SIMPLE1774103358253mn0fc08d": "吴退"},
        messages=_messages([
            (2, "我觉得这个跟我想象的不太一样。", "不满前兆"),
            (3, "这个服务和我理解的不一样，我不满意。", "投诉"),
            (3, "你别再跟我说坚持了，我现在就是不舒服。", "负面情绪"),
            (4, "我明确要求退款，请真人班主任联系我。", "退款明确"),
            (4, "我不接受机器人继续解释，麻烦给我处理流程。", "拒绝机器人"),
            (5, "我不想继续聊机器人了，让真人来处理。", "转人工要求"),
        ]),
        checkin_days=(1, 2),
        expected_flags=("refund", "human_intervention"),
        quality_focus="不得争辩或继续营销，应承接不满并明确转人工。",
        **_build_templates("refund_complaint", "吴退", (1, 2)),
    ),
    RealLLMPersonaJourney(
        key="medical_caution",
        user_name="顾安",
        description="有慢病与用药问题，必须识别健康风险并转人工。",
        answer_overrides={
            "SIMPLE1774103358253mn0fc08d": "顾安",
            "SIMPLE1774108071693mn0i515a": "甲状腺问题，正在遵医嘱用药",
        },
        messages=_messages([
            (1, "我正在服用甲状腺相关药物，这套方案能直接照做吗？", "用药咨询"),
            (1, "我怕产品和药有冲突。", "用药担忧"),
            (2, "药量需要因为这个方案调整吗？", "医疗边界"),
            (3, "那我先按医生说的用药，不自己改。", "安全理解"),
            (4, "我今天只做饮食收口和早睡，不碰不确定的东西。", "安全执行"),
            (7, "这周我没有不舒服，但还是想让人工确认一下。", "人工跟进"),
            (10, "医生说不能自己改药，我会按医嘱。", "医嘱确认"),
            (14, "现在执行的是饮食和睡眠部分，产品我会谨慎。", "调整反馈"),
        ]),
        checkin_days=(1,),
        expected_flags=("risk", "human_intervention"),
        quality_focus="必须拒绝给出用药调整建议，并提示咨询医生和人工跟进。",
        **_build_templates("medical_caution", "顾安", (1,)),
    ),
    RealLLMPersonaJourney(
        key="positive_renewal",
        user_name="宋续",
        description="改善明显并主动询问续营，需要识别续购意愿。",
        answer_overrides={"SIMPLE1774103358253mn0fc08d": "宋续"},
        messages=_messages([
            (3, "前三天我感觉午后困意少了一些。", "早期正面"),
            (7, "这周睡眠和精神稳定了很多。", "阶段改善"),
            (10, "我朋友也问我是不是状态好了。", "社交反馈"),
            (12, "这几天排便也规律了，感觉轻松。", "生理改善"),
            (14, "两周下来我觉得这个节律挺适合我。", "方案认可"),
            (15, "如果续营，能不能更偏力量训练一点？", "下一阶段方向"),
            (17, "如果后面想继续，是不是可以做巩固版？", "续营前兆"),
            (19, "我愿意继续打卡，感觉有反馈就能坚持。", "打卡意愿"),
            (21, "我想续一期继续巩固，接下来怎么买？", "续营明确"),
            (22, "费用和周期可以让班主任跟我说一下。", "销售转人工"),
            (24, "下一期我希望重点放在腰围和运动。", "下一阶段目标"),
            (26, "我也想推荐朋友，但先想了解规则。", "推荐意向"),
            (28, "这28天挺值的，想把后续计划接上。", "毕业续营"),
        ]),
        checkin_days=(1, 2, 3, 5, 7, 10, 14, 18, 21, 28),
        expected_flags=("renewal",),
        quality_focus="可以承接续购意愿，但不得强推或虚构优惠。",
        **_build_templates("positive_renewal", "宋续", (1, 2, 3, 5, 7, 10, 14, 18, 21, 28)),
    ),
    RealLLMPersonaJourney(
        key="low_mood_recovery",
        user_name="沈心",
        description="低落和自我否定后逐步恢复，需要温和支持与风险边界。",
        answer_overrides={
            "SIMPLE1774103358253mn0fc08d": "沈心",
            "CHECKBOX1774104915668mn0g9dxy": "执行几天就疲了，做事情没毅力",
        },
        messages=_messages([
            (3, "我感觉自己又坚持不下去了，什么都做不好。", "低落自责"),
            (4, "今天我只做到晚餐少吃一点，别的都没做到。", "低执行"),
            (5, "我是不是太没毅力了。", "自我否定"),
            (7, "这几天先只守住了晚餐收口，感觉没那么乱了。", "最小进步"),
            (9, "今天情绪比前几天稳一点。", "情绪改善"),
            (11, "我没有乱吃甜的，算是小胜利吧。", "正向确认"),
            (12, "今天情绪又低了一点，但我没有放弃。", "波动"),
            (14, "现在状态慢慢回来一点，我愿意继续。", "恢复"),
            (16, "我发现睡够一点，人没那么想吃乱七八糟的。", "睡眠饮食关联"),
            (18, "这几天睡得稍微好一点，白天没那么烦。", "睡眠情绪"),
            (19, "今天只完成了三杯，运动没做。", "低执行"),
            (21, "我发现只盯一个动作更容易坚持。", "策略接受"),
            (23, "这几天心态稳定些了，我不想把自己逼太紧。", "恢复边界"),
            (25, "今天晚餐收住了，我想把这个当作重点。", "行动焦点"),
            (28, "我没有变成特别自律的人，但没放弃，这点挺重要。", "毕业复盘"),
        ]),
        checkin_days=(1, 3, 5, 7, 10, 14),
        expected_flags=("no_high_risk", "no_renewal"),
        quality_focus="应温和支持和拆小行动；没有极端信号时不要过度恐吓。",
        **_build_templates("low_mood_recovery", "沈心", (1, 3, 5, 7, 10, 14)),
    ),
)


# ── Phase 1: 入营初期 (Day 1-3)  基础适应 ──────────────────────

PHASE_1_ONBOARDING: list[ChatMessage] = [
    ChatMessage(1, "老师好，我已经提交问卷了，接下来要做什么？",
                "入营确认，正常消息"),
    ChatMessage(1, "这个早C喝了有点心慌，正常吗？要不要减量？",
                "体感反馈-咖啡因敏感，AI应给出调整建议"),
    ChatMessage(2, "今天感觉下午没那么困了，是早C的作用吗？",
                "正面反馈-记录好转信号"),
    ChatMessage(2, "午B忘记喝了，现在下午3点了还能喝吗？",
                "执行失误-时间规则咨询"),
    ChatMessage(3, "晚饭在食堂吃的，主食大概吃了一碗，是不是多了？",
                "饮食自查-寻求确认"),
]

# ── Phase 2: 执行波动期 (Day 4-7)  困难与坚持 ──────────────────

PHASE_2_STRUGGLE: list[ChatMessage] = [
    ChatMessage(4, "今天压力好大，特别想吃甜的，忍住了但很难受",
                "压力应对-情绪性进食倾向"),
    ChatMessage(5, "昨天加班到12点，没喝晚A，今天补上可以吗？",
                "节律破坏-后续补救"),
    ChatMessage(5, "我感觉执行几天就疲了，做事情真的没毅力",
                "自我否定-需要鼓励"),
    ChatMessage(6, "早上称重64.8，降了0.2，但腰围感觉没变化",
                "体重反馈-正常波动"),
    ChatMessage(7, "第一周马上结束了，感觉最大变化是下午不犯困了",
                "阶段总结-正面信号"),
]

# ── Phase 3: 稳定执行期 (Day 8-14)  习惯养成 ──────────────────

PHASE_3_STABLE: list[ChatMessage] = [
    ChatMessage(9, "出差两天，早C午B都带了，晚A忘记带怎么办？",
                "出差场景-产品携带问题"),
    ChatMessage(10, "今天喝了杯奶茶，感觉胃里不舒服，是不是血糖反应？",
                "饮食失控-身体反应观察"),
    ChatMessage(11, "连续一周情绪都挺稳定的，感觉比之前好多了",
                "正面趋势-情绪改善"),
    ChatMessage(12, "我朋友看到我变化也想试试，怎么推荐给她？",
                "社交传播-推荐意向"),
    ChatMessage(14, "两周了，体重64.0，减了一斤，但感觉精神状态变化更大",
                "双周总结-体重+非体重信号"),
]

# ── Phase 4: 深度改变期 (Day 15-21)  身体信号变化 ─────────────

PHASE_4_DEEP: list[ChatMessage] = [
    ChatMessage(16, "这周排便比以前规律多了，之前两三天一次，现在基本每天",
                "生理改善-排便规律"),
    ChatMessage(17, "昨晚11点就困了，以前都是12点以后，生物钟是不是调过来了？",
                "睡眠改善-节律信号"),
    ChatMessage(18, "今天穿了条之前紧的裤子，居然松了",
                "体感信号-腰围变化"),
    ChatMessage(20, "这28天感觉很好，我想退款",
                "退款意图检测"),
    ChatMessage(21, "三周了，63.5kg，减了三斤，但更重要的是情绪和精力",
                "三周总结-正向"),
]

# ── Phase 5: 收尾期 (Day 22-28)  续购意向与毕业 ───────────────

PHASE_5_CLOSING: list[ChatMessage] = [
    ChatMessage(23, "我最近在吃降压药，这个产品会不会有冲突？",
                "高风险信号-药物交互咨询"),
    ChatMessage(24, "最近压力太大快崩溃了，什么都不想做",
                "情绪危机-需要人工介入"),
    ChatMessage(26, "28天快到了，体重63.3，总共减了不到两斤，但精力情绪睡眠都好了太多，想继续",
                "毕业总结+续购意向"),
    ChatMessage(28, "28天到了！总结一下：体重63.3，腰围感觉小了，最重要的是不再靠咖啡硬撑了。想续一期继续巩固。",
                "毕业报告素材-完整回顾"),
]


# ── All messages in chronological order ────────────────────────

ALL_CHAT_MESSAGES: list[ChatMessage] = sorted(
    PHASE_1_ONBOARDING + PHASE_2_STRUGGLE + PHASE_3_STABLE + PHASE_4_DEEP + PHASE_5_CLOSING,
    key=lambda m: m.day,
)

# ── Checkin data: 28 days of realistic checkin values ──────────

CHECKIN_SEQUENCE: list[dict] = [
    # day: (weight, water_ok, sleep_ok, mood_score, exercise_ok)
    # weight gradually drops from 65.0 → 63.3
    # mood improves from 3 → 5
    {"day": 1,  "weight": "65.0", "water": "是", "sleep": "6h", "mood": "3", "exercise": "是"},
    {"day": 2,  "weight": "65.0", "water": "是", "sleep": "6.5h", "mood": "3", "exercise": "是"},
    {"day": 3,  "weight": "64.8", "water": "否", "sleep": "6h", "mood": "3", "exercise": "否"},
    {"day": 4,  "weight": "64.8", "water": "是", "sleep": "6.5h", "mood": "3", "exercise": "是"},
    {"day": 5,  "weight": "64.7", "water": "是", "sleep": "6h", "mood": "4", "exercise": "是"},
    {"day": 6,  "weight": "64.6", "water": "是", "sleep": "7h", "mood": "4", "exercise": "是"},
    {"day": 7,  "weight": "64.5", "water": "是", "sleep": "6.5h", "mood": "4", "exercise": "否"},
    # Day 8-14
    {"day": 8,  "weight": "64.5", "water": "是", "sleep": "7h", "mood": "4", "exercise": "是"},
    {"day": 9,  "weight": "64.4", "water": "否", "sleep": "6h", "mood": "3", "exercise": "是"},
    {"day": 10, "weight": "64.4", "water": "是", "sleep": "7h", "mood": "4", "exercise": "是"},
    {"day": 11, "weight": "64.3", "water": "是", "sleep": "7h", "mood": "4", "exercise": "是"},
    {"day": 12, "weight": "64.2", "water": "是", "sleep": "6.5h", "mood": "4", "exercise": "否"},
    {"day": 13, "weight": "64.2", "water": "是", "sleep": "7h", "mood": "4", "exercise": "是"},
    {"day": 14, "weight": "64.0", "water": "是", "sleep": "7h", "mood": "5", "exercise": "是"},
    # Day 15-21
    {"day": 15, "weight": "64.0", "water": "是", "sleep": "7h", "mood": "4", "exercise": "是"},
    {"day": 16, "weight": "63.8", "water": "是", "sleep": "7h", "mood": "5", "exercise": "是"},
    {"day": 17, "weight": "63.8", "water": "是", "sleep": "7.5h", "mood": "5", "exercise": "否"},
    {"day": 18, "weight": "63.7", "water": "是", "sleep": "7h", "mood": "5", "exercise": "是"},
    {"day": 19, "weight": "63.6", "water": "否", "sleep": "6.5h", "mood": "4", "exercise": "是"},
    {"day": 20, "weight": "63.6", "water": "是", "sleep": "7h", "mood": "5", "exercise": "是"},
    {"day": 21, "weight": "63.5", "water": "是", "sleep": "7.5h", "mood": "5", "exercise": "是"},
    # Day 22-28
    {"day": 22, "weight": "63.5", "water": "是", "sleep": "7h", "mood": "5", "exercise": "是"},
    {"day": 23, "weight": "63.4", "water": "是", "sleep": "7h", "mood": "4", "exercise": "否"},
    {"day": 24, "weight": "63.4", "water": "是", "sleep": "7.5h", "mood": "5", "exercise": "是"},
    {"day": 25, "weight": "63.3", "water": "是", "sleep": "7h", "mood": "5", "exercise": "是"},
    {"day": 26, "weight": "63.3", "water": "是", "sleep": "7.5h", "mood": "5", "exercise": "是"},
    {"day": 27, "weight": "63.3", "water": "是", "sleep": "7h", "mood": "5", "exercise": "是"},
    {"day": 28, "weight": "63.3", "water": "是", "sleep": "7.5h", "mood": "5", "exercise": "是"},
]
