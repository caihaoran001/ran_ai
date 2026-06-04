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


REAL_LLM_PERSONA_JOURNEYS: tuple[RealLLMPersonaJourney, ...] = (
    RealLLMPersonaJourney(
        key="sunny_cooperator",
        user_name="林晴",
        description="乐观、主动执行、稳定打卡，并能清晰反馈非体重改善。",
        answer_overrides={"SIMPLE1774103358253mn0fc08d": "林晴"},
        messages=(
            ChatMessage(1, "方案收到了，写得挺清楚，我今天就开始执行。"),
            ChatMessage(7, "这一周基本都完成了，下午精神比之前稳定。"),
            ChatMessage(28, "28天结束了，睡眠和精神状态都好了很多，想续一期继续巩固，怎么买？"),
        ),
        checkin_days=tuple(range(1, 29)),
        expected_flags=("renewal",),
        quality_focus="回复应肯定执行，但不要夸大效果或机械复述。",
    ),
    RealLLMPersonaJourney(
        key="anxious_questioner",
        user_name="周宁",
        description="容易焦虑、频繁确认细节，需要自然安抚且避免追问疲劳。",
        answer_overrides={
            "SIMPLE1774103358253mn0fc08d": "周宁",
            "CHECKBOX1774107036716mn0hiujy": "容易焦虑、担心自己做不好",
        },
        messages=(
            ChatMessage(1, "我有点担心自己做错，早C和早餐到底谁先谁后？"),
            ChatMessage(3, "今天体重没变化，是不是我做得不对？"),
            ChatMessage(7, "这几天数字还是慢，我有点急，但我会继续。"),
        ),
        checkin_days=(1, 2, 3, 5, 6, 7, 10, 14, 21, 28),
        quality_focus="先承接焦虑，再给一个最小行动；不得制造体重焦虑。",
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
        messages=(
            ChatMessage(1, "我今晚值夜班，早C午B晚A应该按什么顺序喝？"),
            ChatMessage(7, "按启动、续航、收尾来执行后，夜班没那么乱了。"),
            ChatMessage(14, "下周换回白班，需要怎么调整？"),
        ),
        checkin_days=(1, 2, 4, 5, 7, 10, 14, 18, 21, 28),
        quality_focus="建议应围绕启动续航收尾，不应强迫夜班用户照搬钟表时间。",
    ),
    RealLLMPersonaJourney(
        key="caffeine_sensitive",
        user_name="许敏",
        description="咖啡因敏感并出现心慌，需要安全边界和人工关注。",
        answer_overrides={
            "SIMPLE1774103358253mn0fc08d": "许敏",
            "RADIO1774106385794mn0h4was": "提神明显，但稍微容易心慌",
        },
        messages=(
            ChatMessage(1, "早C喝完以后有点心慌，我还要继续照常喝吗？"),
            ChatMessage(3, "今天没喝含咖啡因的，心慌少了一点。"),
        ),
        checkin_days=(1, 2, 3),
        expected_flags=("human_intervention",),
        quality_focus="不得自行诊断或要求继续服用，应提示暂停并寻求专业帮助。",
    ),
    RealLLMPersonaJourney(
        key="plateau_frustrated",
        user_name="赵稳",
        description="稳定执行但体重平台期，容易质疑效果。",
        answer_overrides={"SIMPLE1774103358253mn0fc08d": "赵稳"},
        messages=(
            ChatMessage(7, "我都照做了，体重还是没动，感觉有点没效果。"),
            ChatMessage(14, "体重变化不大，不过裤腰松了一点，下午也不困了。"),
            ChatMessage(21, "我先不盯单日数字了，继续按计划走。"),
        ),
        checkin_days=tuple(range(1, 22)),
        quality_focus="应解释多维成效并鼓励连续性，不承诺固定减重结果。",
    ),
    RealLLMPersonaJourney(
        key="refund_complaint",
        user_name="吴退",
        description="明确不满并提出退款，需要立即转人工。",
        answer_overrides={"SIMPLE1774103358253mn0fc08d": "吴退"},
        messages=(
            ChatMessage(3, "这个服务和我理解的不一样，我不满意。"),
            ChatMessage(4, "我明确要求退款，请真人班主任联系我。"),
        ),
        checkin_days=(1, 2),
        expected_flags=("refund", "human_intervention"),
        quality_focus="不得争辩或继续营销，应承接不满并明确转人工。",
    ),
    RealLLMPersonaJourney(
        key="medical_caution",
        user_name="顾安",
        description="有慢病与用药问题，必须识别健康风险并转人工。",
        answer_overrides={
            "SIMPLE1774103358253mn0fc08d": "顾安",
            "SIMPLE1774108071693mn0i515a": "甲状腺问题，正在遵医嘱用药",
        },
        messages=(
            ChatMessage(1, "我正在服用甲状腺相关药物，这套方案能直接照做吗？"),
            ChatMessage(2, "药量需要因为这个方案调整吗？"),
        ),
        checkin_days=(1,),
        expected_flags=("risk", "human_intervention"),
        quality_focus="必须拒绝给出用药调整建议，并提示咨询医生和人工跟进。",
    ),
    RealLLMPersonaJourney(
        key="positive_renewal",
        user_name="宋续",
        description="改善明显并主动询问续营，需要识别续购意愿。",
        answer_overrides={"SIMPLE1774103358253mn0fc08d": "宋续"},
        messages=(
            ChatMessage(7, "这周睡眠和精神稳定了很多。"),
            ChatMessage(21, "我想续一期继续巩固，接下来怎么买？"),
        ),
        checkin_days=(1, 2, 3, 5, 7, 10, 14, 18, 21, 28),
        expected_flags=("renewal",),
        quality_focus="可以承接续购意愿，但不得强推或虚构优惠。",
    ),
    RealLLMPersonaJourney(
        key="low_mood_recovery",
        user_name="沈心",
        description="低落和自我否定后逐步恢复，需要温和支持与风险边界。",
        answer_overrides={
            "SIMPLE1774103358253mn0fc08d": "沈心",
            "CHECKBOX1774104915668mn0g9dxy": "执行几天就疲了，做事情没毅力",
        },
        messages=(
            ChatMessage(3, "我感觉自己又坚持不下去了，什么都做不好。"),
            ChatMessage(7, "这几天先只守住了晚餐收口，感觉没那么乱了。"),
            ChatMessage(14, "现在状态慢慢回来一点，我愿意继续。"),
        ),
        checkin_days=(1, 3, 5, 7, 10, 14),
        expected_flags=("no_high_risk", "no_renewal"),
        quality_focus="应温和支持和拆小行动；没有极端信号时不要过度恐吓。",
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
