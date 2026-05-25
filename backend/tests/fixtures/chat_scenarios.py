"""Designed user messages simulating a realistic 28-day service journey.

Each entry: (day, content, expected_behavior)
expected_behavior is used by the MockAIService to simulate the right risk/intent response.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ChatMessage:
    day: int              # Which service day (1-28)
    content: str          # The user message text
    description: str = "" # What this message tests


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
