from __future__ import annotations

import logging
import re
from copy import deepcopy
from typing import Any

from app.models.user_document import DocumentType

logger = logging.getLogger(__name__)

# 产品排班每段的软上限，与提示词里的 ≤44 字对齐。全库的字数上限都是软约束：
# 超出只由 _length_checked 记一条告警，绝不截断（原因见该函数）。
PRODUCT_SLOT_SOFT_LIMIT = 44


ENERGY_FIELD_SPEC = {
    "recipient": "称呼，例如 易车汇 宝宝 或 蔡浩然",
    "overview": {
        "name": "用户姓名",
        "gender_age": "性别 / 年龄",
        "height_weight": "身高 / 体重",
        "bmi": "BMI 与阶段",
        "waist_bodyfat": "腰围 / 体脂率；缺一个必须明确写「腰围未提供」或「体脂率未提供」，不得只留孤立数字",
        "recent_change": "最近3个月变化",
        "schedule": "作息状态",
        "exercise": "运动基础",
        "main_need": "主要诉求",
        "current_focus": "当前主线，与 shared_analysis.current_mainline 一致",
    },
    "one_sentence": "一句话结论",
    "core_conclusions": {
        "management_band": "业务管理带，只能逐字复制系统按安全减重空间算出的「A带｜修复塑形 / B带｜轻盈启动 / C带｜明确减脂 / D带｜系统减重」之一。"
        "禁止写「需人工复核」等内部运营标签，用户看不到内部状态",
        "band_explanation": "用一句用户能听懂的话解释为什么属于这个管理带；A带只能定性说明更适合修复塑形、"
        "不设置体重下降指标，不得出现用户原始减重数字、安全减重空间具体数值或斤/kg区间",
        "body_trend": "入营问卷反映的近3个月身体趋势，只能取：正在改善 / 基本稳定 / 缓慢变沉 / 明显恶化 / 信息不足",
        "trend_explanation": "用一句话解释近3个月趋势；信息不足时明确无法判断，不得补编长期变化",
        "trend_direct_evidence": "1条能证明变化方向的直接趋势事实，优先来自问卷「最近3个月体重变化」；"
        "当前BMI/体重/腰围属于单点状态，不能写在这里",
        "trend_support_signals": ["1-2条当前状态支持信号，例如腰腹体感、睡眠、精力、食欲边界、活动量"],
        "day28_task": "这28天先解决什么，用一句话说清",
    },
    "goal_response": {
        "goal_type": "specific 或 general。specific=用户写了明确数字/明确结果；general=用户只勾选了期待变化或泛化诉求",
        "judgement_type": "reasonable 或 needs_adjustment。needs_adjustment=目标过激、当前不建议或需先安全复核",
        "content": "直接面向用户的一段完整回应。需要包含用户目标/期待、判断、标准目标、高承诺目标或当前更适合方向、启用/暂停条件；"
        "A带只能定性概括数字减重诉求，不得复述具体斤数、公斤数或目标体重；不要空泛，不承诺结果。",
        "user_goal": "兼容字段：概括用户原始目标或期待变化；A带不得复述具体减重斤数、公斤数或目标体重",
        "judgement": "兼容字段：直接回应目标是否适合当前阶段；A带只做定性判断，不得复述具体减重数字",
        "base_goal": "兼容字段：标准目标；A带禁止任何减重数字",
        "challenge_goal": "兼容字段：高承诺目标；不符合资格时不得出现高承诺数字",
        "conditions": "兼容字段：标准档60%、高承诺档85%的完整打卡约定；高承诺目标群内确认后生效",
    },
    "occupation_context": "基于问卷明确答案概括真实工作节奏、活动情况、精力消耗和家庭责任，≤100字；不凭职业名称推断",
    "weight_loss_history": "基于既往减重选项写可复用经验、难维持原因和不再重复的做法，≤100字；资料不足就明确未提供",
    "key_influence_chains": ["1–2条：明确场景→重复行为→当前表现→目标受阻；不得补用户未提供的时间或因果"],
    "desired_outcomes": [
        {
            "name": "用户明确选择的成果，1项主要成果、最多2项辅助成果",
            "role": "main 或 supporting，且只能有1项 main",
            "tracking_method": "简短、可执行的记录方式，不编造数值目标",
        }
    ],
    "judgement_points": [
        "为什么这样判断，严格3-5条。每条固定写成「现象/数据 → 说明什么问题」，控制在18-32字，宁短不长。"
        "每条尽量引用不同类型的问卷证据（节律 / 饮食 / 睡眠 / 情绪 / 运动 / 体型），不要从同一字段反复推导同一结论"
    ],
    "current_state": "当前身体状态真正该关注什么",
    # 这三栏渲染出去的小标题是「食欲与饭后状态 / 饱腹感与晚间收口 / 睡眠、压力与恢复」，
    # 字段名只是早期版本的历史包袱。说明必须写成"只写什么"：写成"不得写成激素诊断"时，
    # 模型会把禁令本身抄进正文，用户读到的就是一句「这不代表激素诊断」。
    "insulin_resistance": "只写食欲边界、饭后困、腰腹堆积这类生活方式信号，≤40字。",
    "leptin_sensitivity": "只写饱腹感、晚间失控、食欲边界这类生活方式信号，≤40字。",
    "hormone_stage": "只写年龄、睡眠、压力对恢复能力的影响，≤40字；不得仅凭年龄推测围绝经、更年期、绝经等特殊生理阶段。",
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
        "week5_8": "逐字使用 intake_facts.stage_targets.week5_8，可在其后补一句本阶段执行重点，不得改动其中数字",
        "week9_12": "逐字使用 intake_facts.stage_targets.week9_12，可在其后补一句本阶段执行重点，不得改动其中数字",
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
        "运动频次只能逐字使用 shared_analysis.activity_plan.sessions_per_week，不得另定次数或分首周/后续周。"
        "必须覆盖 priority_items 三件事之外的维度（产品节律 / 睡眠 / 情绪与环境设计中至少一项）"
    ],
    # 这三段原本的说明只有"早C""午B""晚A"三个字，等于没给要求：模型既不知道标准用量
    # （系统从不把产品口径喂给它），又被字数掐着，只能写出"剂量按个案确认"这种空话。
    # 用量、水量、时点是产品事实，按系统提示词的【产品标准口径】照写；因人而异的是
    # 对这位用户的作用，以及需要减量/前移/暂停时的个案调整。
    "product_schedule": {
        "morning_c": "早C：按【产品标准口径】写清用量、冲泡水量和饮用时点，再接一句对这位用户的作用，≤50字",
        "noon_b": "午B：同上，≤50字",
        # 早先这里写的是"并写清最晚饮用时间"，模型据此把「睡前1小时」换算成具体钟点，
        # 两份文件各算一次就出现了 21:30 / 22:00 两个值。最晚时间只写相对表述。
        "night_a": "晚A：同上，最晚饮用时间统一写「最晚睡前1小时完成」，不换算成钟点，≤50字",
        "summary": "产品排班总结；必须服从个案安全、耐受和暂停结论",
    },
    "product_meaning": "早C/午B/晚A 对这位用户的意义，三层递进共≤160字："
    "①连续28天反复建立「启动—续航—收口」的稳定节律信号，帮助白天启动、午后平稳、晚间收口；"
    "②按这位用户真实存在的卡点说明先减少什么（下午崩盘 / 晚间失控 / 节律混乱 / 白天启动困难中只挑问卷里真有的，不要全列）；"
    "③长期更有利于食欲控制、睡眠边界和代谢稳定。产品只用于帮助执行和稳定节律，不是减重捷径——这层含义写进正文表达即可",
    "special_reminder": "特别提醒，包含必要就医/检测提示",
    "missing_information": ["仍缺失或需要补测的信息；没有则输出空列表"],
}


EXECUTION_FIELD_SPEC = {
    "weight_band": "与 shared_analysis.weight_band 一致的管理带，由系统强制覆写",
    "base_goal": "与 shared_analysis.base_goal 逐字一致的28天标准目标，由系统强制覆写",
    "challenge_goal": "与 shared_analysis.challenge_goal 逐字一致的高承诺目标，由系统强制覆写",
    "execution_path": "默认 standard；文档生成时不得写成已经进入高承诺档",
    "commitment_rule": "标准档完整打卡率60%；高承诺档85%，群内确认后生效；完整打卡=当天群内五项都有回答",
    "path_goal": "与 shared_analysis.long_term_goal 逐字一致的长期方向，由系统强制覆写",
    "current_mainline": "与 shared_analysis.current_mainline 一致的当前唯一主线，≤16字",
    "day28_goal": "这28天要完成的任务，≤48字，同时包含阶段任务和要观察的结果；没有明确数字目标时不得编造斤数或厘米数。",
    "current_phase_goal": "当前阶段目标，≤32字，用「先解决{核心问题}，不是{反面}」句式，聚焦排序而非平铺罗列，例如「先解决节律稳定，不是追求失控式掉秤」。",
    "principle": "执行总原则，≤28字，保持两段递进结构「先守{底盘动作}，再{放大动作}」，是通用指导语，不嵌入具体参数（时间点、拳数）。例如「先守底盘，再抓结果；先连续完成，再逐步加码」。",
    "product_schedule": {
        # 执行纸是清单，不重复诊断报告里的作用解释，但用量、水量、时点三样都必须写全。
        # 上限曾设 34 字：晚A 实测要 36 字，超出后截断按整句砍，"最晚睡前1小时完成"
        # 被静默丢掉；早C 则为了省字数直接漏掉时点。放到 44 字并逐段点名时点必写。
        "morning_c": "早C：按【产品标准口径】写用量+冲泡水量+饮用时点，三样缺一不可，≤44字；需要暂停时明确写暂停",
        "noon_b": "午B：同上，用量+水量+时点三样写全，≤44字；需要减量、前移或暂停时明确写",
        "night_a": "晚A：同上，用量+水量+时点写全，最晚时间统一写「最晚睡前1小时完成」不换算钟点，≤44字；需要暂停时明确写",
    },
    "diet_rules": ["对应用户真实生活场景的饮食规则，最多3条，每条≤34字"],
    "exercise_rules": ["运动频率只能使用 shared_analysis.activity_plan.sessions_per_week；再写单次时长、强度、动作和替代方案，最多2条，每条≤44字"],
    "core_actions": [
        {
            "event_anchor": "早餐后、午餐后、晚餐后、上床前等相对场景；只能来自真实作息或方案",
            "action": "核心动作，写清数量/时长/频次",
            "fallback": "现实中做不到完整版本时的简化替代",
            "checkin_mapping": "对应五项打卡中的题目或关注点",
        }
    ],
    "weekly_plan": [
        {"week": "第1周", "core_task": "稳住底盘", "acceptance_standard": "1个短句，例如：按个案产品排班完成；晚餐主食≤1拳；完成计划内活动。"},
        {"week": "第2周", "core_task": "提高一致性", "acceptance_standard": "1个短句，例如：连续5天不漏早餐；晚餐后走15–20分钟；应酬日不加宵夜。"},
        {"week": "第3周", "core_task": "抓结果指标", "acceptance_standard": "1个短句，例如：完成共同活动计划；记录腰围和体重。"},
        {"week": "第4周", "core_task": "稳定复现", "acceptance_standard": "1个短句，例如：把睡前收口和计划内活动做满。"},
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
            "example_answer": "与本项 title、answer_type 和 options 同时生成的一条简短已填写答案。"
            "yes_no 写「是，已完成」或与真实题意匹配的否定答案；score_1_5 写「4分，……」；"
            "number 必须带 title 要求的单位；text 直接写具体情况。第1项必须分别写清早C、午B、晚A，"
            "并至少展示一个未完成或按方案暂停的情况。不得出现「示例回答」「请填写」「按实际情况填写」「答：」",
        }
    ],
    "weekly_standard": "每周通过标准，1句 ≤76字，必须包含4要素：个案产品排班完成情况 + 关键饮食动作 + 运动/步数达标 + 至少1次体重/围度/体感记录。减量或暂停用户不得写三杯完成要求。",
    "adjust_stop_conditions": "调整与停止条件，1句 ≤88字。必须说明什么情况减半、前移、暂停、联系班主任、及时就医。不要写成医疗方案。",
}


ENERGY_FIELD_SPEC_EN = {
    "recipient": "Preferred name / nickname, e.g. Carol or Alex",
    "overview": {
        "name": "User's full legal name",
        "gender_age": "Gender / Age (e.g. Male / 30)",
        "height_weight": "Height / Weight (e.g. 175cm / 143.3 lbs)",
        "bmi": "BMI & status (e.g. 21.2 Normal)",
        "waist_bodyfat": "Waist / Body Fat percentage (if missing, write 'Waist not provided' or 'Body fat not provided')",
        "recent_change": "Recent 3-Month Trend (e.g. Basically stable / Slowly increasing)",
        "schedule": "Daily rhythm & sleep schedule",
        "exercise": "Physical activity baseline",
        "main_need": "Primary goal / need",
        "current_focus": "Current mainline, matching shared_analysis.current_mainline",
    },
    "one_sentence": "One-sentence core conclusion in natural English",
    "core_conclusions": {
        "management_band": "Deterministic business management band from system, e.g. Band A | Body Rejuvenation & Sculpting / Band B | Gentle Energy Kickstart / Band C | Targeted Fat Loss / Band D | Systematic Weight Loss",
        "band_explanation": "One clear sentence explaining why the user belongs to this band in English",
        "body_trend": "Trend in the past 3 months: Improving / Basically stable / Slowly increasing / Significantly worsening / Insufficient information",
        "trend_explanation": "One sentence explaining the 3-month trend in English",
        "trend_direct_evidence": "1 direct factual evidence supporting trend direction",
        "trend_support_signals": ["1-2 supporting signals from current state in English"],
        "day28_task": "What to solve first in these 28 days in one clear sentence",
    },
    "goal_response": {
        "goal_type": "specific or general",
        "judgement_type": "reasonable or needs_adjustment",
        "content": "A direct paragraph addressing the user's expectations, evaluation, standard goal, high commitment goal, and execution agreement in English.",
        "user_goal": "Summary of user's original goal",
        "judgement": "Direct assessment of goal feasibility",
        "base_goal": "Verbatim copy of shared_analysis.base_goal (already English, figures in lbs) or habit focus",
        "challenge_goal": "Verbatim copy of shared_analysis.challenge_goal (already English, figures in lbs) or review condition",
        "conditions": "Standard tier 60% completion, high commitment 85% completion agreement",
    },
    "occupation_context": "Summary of actual work rhythm, physical activity, and stress based on questionnaire in English (<=100 words)",
    "weight_loss_history": "Summary of reusable experience and past methods to avoid based on questionnaire (<=100 words)",
    "key_influence_chains": ["1-2 chains: Context -> Habit -> State -> Goal limitation in English"],
    "desired_outcomes": [
        {
            "name": "Desired outcome selected by user in English",
            "role": "main or supporting (exactly 1 main)",
            "tracking_method": "Brief actionable tracking method in English",
        }
    ],
    "judgement_points": [
        "Rationale & evidence in English, exactly 3-5 items. Each written as 'Phenomenon/Data -> Insight' (10-25 words each)."
    ],
    "current_state": "What to genuinely focus on regarding current physical state in English",
    "insulin_resistance": "Lifestyle signals regarding appetite control and post-meal energy in English (<=30 words)",
    "leptin_sensitivity": "Lifestyle signals regarding satiety and evening cravings in English (<=30 words)",
    "hormone_stage": "Impact of age, sleep, and stress on recovery capacity in English (<=30 words)",
    "priority_direction": "Top priority direction to focus on in English",
    "priority_items": [
        "Top 3 priority problems to solve in English, exactly 3 items ranked by priority."
    ],
    "success_path": {
        "intro": "Overall success pathway summary in English",
        "weight_band": "Current management band",
        "total_goal": "Total pathway goal in English",
        "day28_goal": "First 28 days goal in English",
        "week5_8": "Weeks 5-8 milestone in English",
        "week9_12": "Weeks 9-12 milestone in English",
    },
    "module_diagnosis": {
        "rhythm": "Daily rhythm diagnosis in English",
        "diet": "Diet & nutrition diagnosis in English",
        "sleep": "Sleep diagnosis in English",
        "pressure": "Stress & recovery diagnosis in English",
        "activity": "Physical activity diagnosis in English",
    },
    "expected_changes": {
        "day7": "Expected changes in first 7 days in English",
        "day14_28": "Expected changes in days 14-28 in English",
    },
    "week_actions": [
        "Immediate action items for this week in English, 4-5 items. Must specify numbers, timing, or context. Must include at least 1 exercise action with type+frequency+duration."
    ],
    "product_schedule": {
        "morning_c": "Morning C: 1 sachet with 300-330ml water during or within 30 min of breakfast, plus benefit for user (<=40 words)",
        "noon_b": "Noon B: 1 sachet with 300-330ml water during or within 30 min of lunch (<=40 words)",
        "night_a": "Evening A: 1 sachet with 300-330ml warm water, complete at latest 1 hour before bedtime (<=40 words)",
        "summary": "Product protocol summary in English",
    },
    "product_meaning": "Significance of Morning C / Noon B / Evening A for this user across 3 progressive layers in English (<=120 words)",
    "special_reminder": "Special reminders, medical advice, or safety notes in English",
    "missing_information": ["Missing information to be supplemented, or empty list"],
}


EXECUTION_FIELD_SPEC_EN = {
    "weight_band": "Management band consistent with shared_analysis.weight_band",
    "base_goal": "Standard 28-day goal consistent with shared_analysis.base_goal",
    "challenge_goal": "High commitment goal consistent with shared_analysis.challenge_goal",
    "execution_path": "Default 'standard'",
    "commitment_rule": "Standard tier 60% completion rate; High commitment 85% completion rate",
    "path_goal": "Long-term direction consistent with shared_analysis.long_term_goal",
    "current_mainline": "Current mainline consistent with shared_analysis.current_mainline",
    "day28_goal": "Tasks and outcomes for these 28 days in English (<=35 words)",
    "current_phase_goal": "Current phase goal in English (<=25 words), e.g. 'Stabilize daily rhythm first, rather than chasing uncontrolled rapid weight drops.'",
    "principle": "Core execution principle in English (<=25 words), e.g. 'Build baseline consistency first, then gradually increase progression.'",
    "product_schedule": {
        "morning_c": "Morning C: 1 sachet + 300-330ml water with breakfast or within 30 min",
        "noon_b": "Noon B: 1 sachet + 300-330ml water with lunch or within 30 min",
        "night_a": "Evening A: 1 sachet + 300-330ml warm water, at latest 1 hour before sleep",
    },
    "diet_rules": ["Diet rules tailored to user's real context in English, max 3 items, each <=25 words"],
    "exercise_rules": ["Exercise rules specifying frequency, duration, intensity and action in English, max 2 items, each <=30 words"],
    "core_actions": [
        {
            "event_anchor": "Relative anchor: After breakfast, After lunch, After dinner, Before bed",
            "action": "Core action with quantity/duration in English",
            "fallback": "Simplified fallback if unable to complete full action in English",
            "checkin_mapping": "Corresponding daily check-in category or focus",
        }
    ],
    "weekly_plan": [
        {"week": "Week 1", "core_task": "Rhythm Stabilization / Foundation", "acceptance_standard": "Complete product protocol; eat 2 fists of vegetables daily; follow planned activity."},
        {"week": "Week 2", "core_task": "Consistency in Key Habits", "acceptance_standard": "Maintain diet rules 5 days/wk; 2x15-min bodyweight strength; 15-20 min post-dinner walk."},
        {"week": "Week 3", "core_task": "Tracking & Progress Evaluation", "acceptance_standard": "Log weight & waist; brisk walk & light strength; observe sleep, energy & digestion."},
        {"week": "Week 4", "core_task": "Sustainable Maintenance & Solidification", "acceptance_standard": "Solidify nutrition & activity routines; reproduce effective habits sustainably."},
    ],
    "daily_checkin": [
        {
            "order": "Integer 1-5, unique and strictly increasing; exactly 5 items total",
            "category": "Must be one of: product / diet / activity / sleep / state. Item 1 must be product, exactly 1 item must be activity",
            "title": "Clear English question targeting user's specific friction point (e.g. 'Did you complete Morning C, Noon B, and Evening A as planned?' for product; 'Did you control dinner carbohydrates to at most 1 fist?' for diet; 'How many minutes of brisk walking did you complete?' for activity; 'Did you start sleep wind-down by 23:30?' for sleep; 'Afternoon energy level rating (1-5)' for state). No leading numbers, no relative words like 'today/tonight'.",
            "answer_type": "One of: yes_no / score_1_5 / number / text matching the question format",
            "options": "[\"Yes\", \"No\"] for yes_no; [\"1\", \"2\", \"3\", \"4\", \"5\"] for score_1_5; [] for number and text",
            "example_answer": "Realistic filled answer in English matching title. Item 1 MUST explicitly write Morning C, Noon B, and Evening A respectively with at least one status (e.g. 'Morning C: completed; Noon B: completed; Evening A: paused'). For yes_no write 'Yes, completed'; for score_1_5 write '4 points, felt energetic'; for number include units like '25 minutes'.",
        }
    ],
    "weekly_standard": "Weekly completion criteria in English (<=50 words)",
    "adjust_stop_conditions": "Adjustment and stopping criteria in English (<=60 words)",
}


STAGE_REPORT_SPEC = {
    "stage_summary": "阶段总结",
    "risk_reminder": "风险提醒",
    "adjustment_advice": "微调建议",
    "followup_advice": "后续执行建议",
}


def prompt_schema_for(document_type: DocumentType, language: str = "zh") -> dict[str, Any]:
    if language == "en":
        if document_type == DocumentType.ENERGY_RESTART_PRESCRIPTION:
            return ENERGY_FIELD_SPEC_EN
        return EXECUTION_FIELD_SPEC_EN
    if document_type == DocumentType.ENERGY_RESTART_PRESCRIPTION:
        return ENERGY_FIELD_SPEC
    return EXECUTION_FIELD_SPEC


def combined_prompt_schema(language: str = "zh") -> dict[str, Any]:
    if language == "en":
        return {
            DocumentType.ENERGY_RESTART_PRESCRIPTION.value: ENERGY_FIELD_SPEC_EN,
            DocumentType.DAY28_EXECUTION_STANDARD.value: EXECUTION_FIELD_SPEC_EN,
        }
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
    language: str = "zh",
) -> dict[str, Any]:
    if document_type == DocumentType.ENERGY_RESTART_PRESCRIPTION:
        payload = _build_energy(title, user_name, display_name, fields, language=language)
    else:
        payload = _build_execution(title, user_name, display_name, fields, language=language)
    # 落库带上语言：Markdown 与 .docx 都由 content_json 二次渲染，渲染层要靠它决定
    # 用「To：」还是「To: 」这类标点。历史文档没有这个键，按中文渲染与原样一致。
    payload["language"] = language
    return payload


def build_stage_report_content_json(
    *,
    stage: str,
    title: str,
    user_name: str,
    fields: dict[str, Any],
    language: str = "zh",
) -> dict[str, Any]:
    if language == "en":
        blocks = [
            _text_block("stage_summary", "1. Stage Summary", fields.get("stage_summary")),
            _text_block("risk_reminder", "2. Risk Reminders", fields.get("risk_reminder")),
            _text_block("adjustment_advice", "3. Fine-tuning Recommendations", fields.get("adjustment_advice")),
            _text_block(
                "followup_advice",
                "4. Follow-up Action Plan",
                fields.get("followup_advice") or fields.get("repurchase_advice"),
            ),
        ]
    else:
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
    payload["language"] = language
    return payload


def render_document_content(content_json: dict[str, Any]) -> str:
    lines = []
    # 英文文档不能带全角标点：客户看到的是 "To：NAR"「Key Factor 1：」这种中英混排。
    label_sep = ": " if content_json.get("language") == "en" else "："
    if content_json.get("show_header_brand", True):
        lines.append("Powered by HumanBurning.AI")
    lines.append(f"# {content_json.get('title') or '交付文件'}")
    recipient = _text(content_json.get("recipient"))
    if recipient and content_json.get("show_recipient", True):
        lines.append(f"To{label_sep}{recipient}")
    for block in content_json.get("blocks") or []:
        title = _text(block.get("title"))
        kind = block.get("kind")
        if block.get("display") == "inline":
            content = _text(block.get("content"))
            if title and content:
                lines.append(f"{title}{label_sep}{content}")
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


def _build_energy(
    title: str,
    user_name: str,
    display_name: str | None,
    fields: dict[str, Any],
    language: str = "zh",
) -> dict[str, Any]:
    overview = _dict(fields.get("overview"))
    is_en = language == "en"
    if is_en:
        blocks = [
            {
                "key": "overview",
                "title": "1. User Profile Overview",
                "kind": "table",
                "rows": [
                    [_label("Name"), _editable(overview.get("name") or user_name), _label("Gender / Age"), _editable(overview.get("gender_age"))],
                    [_label("Height / Weight"), _editable(overview.get("height_weight")), _label("BMI"), _editable(overview.get("bmi"))],
                    [_label("Waist / Body Fat"), _editable(overview.get("waist_bodyfat")), _label("Recent 3-Month Trend"), _editable(overview.get("recent_change"))],
                    [_label("Daily Rhythm"), _editable(overview.get("schedule")), _label("Exercise Baseline"), _editable(overview.get("exercise"))],
                    [_label("Main Goal"), _editable(overview.get("main_need")), _label("Current Focus"), _editable(overview.get("current_focus"))],
                ],
            },
            _core_conclusions_block(fields, is_en=True),
            _goal_response_block(fields, is_en=True),
            _personal_execution_basis_block(fields, is_en=True),
            _list_block("judgement_points", "5. Rationale & Evidence", fields.get("judgement_points")),
            _success_path_block(fields, is_en=True),
            _priority_items_block(fields, is_en=True),
            _rhythm_recovery_block(fields, is_en=True),
            _module_block(fields, is_en=True),
            _product_block(fields, is_en=True),
            _list_block("week_actions", "11. Actions for This Week", fields.get("week_actions"), limit=5),
            _expected_block(fields, is_en=True),
            _text_block("special_reminder", "13. Special Reminders", fields.get("special_reminder")),
            _missing_information_block(fields, is_en=True),
            _text_block(
                "notice",
                "15. Stop Immediately If You Experience",
                "In case of chest pain, palpitations, shortness of breath, persistent dizziness, melena, severe abdominal pain, or continuous vomiting, stop immediately and seek medical attention.",
            ),
        ]
        return _payload(title=title, recipient=fields.get("recipient") or display_name or user_name, blocks=blocks)

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
        _personal_execution_basis_block(fields),
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
            "14. 出现这些情况请立刻停下",
            # 原文案前半句是"本报告不替代医生诊断、治疗或药物调整"，对用户零信息量，
            # 只是把责任推回去；留下的这句是真正的安全动作。免责声明不再进用户可见正文，
            # 需要人工把关的个案改由后台的"需人工复核"标记和运营核对承接。
            "出现胸痛、明显心悸、呼吸困难、持续头晕、黑便、便血、剧烈腹痛或持续呕吐，立刻停止执行并就医。",
        ),
    ]
    return _payload(title=title, recipient=fields.get("recipient") or display_name or user_name, blocks=blocks)


def _build_execution(
    title: str,
    user_name: str,
    display_name: str | None,
    fields: dict[str, Any],
    language: str = "zh",
) -> dict[str, Any]:
    rows = fields.get("weekly_plan")
    if not isinstance(rows, list):
        rows = []
    rows_by_week = {str(row.get("week")): row for row in rows if isinstance(row, dict)}
    is_en = language == "en"

    if is_en:
        table_rows = [[_label("Week"), _label("Core Focus"), _label("Completion Criteria")]]
        fixed_focus_en = {
            "Week 1": "Rhythm Stabilization / Foundation",
            "Week 2": "Consistency in Key Habits",
            "Week 3": "Tracking & Progress Evaluation",
            "Week 4": "Sustainable Maintenance & Solidification",
        }
        fixed_actions_en = {
            "Week 1": "Complete product protocol; eat 2 fists of vegetables daily; follow planned activity.",
            "Week 2": "Maintain diet rules 5 days/wk; 2x15-min bodyweight strength; 15-20 min post-dinner walk.",
            "Week 3": "Log weight & waist; brisk walk & light strength; observe sleep, energy & digestion.",
            "Week 4": "Solidify nutrition & activity routines; reproduce effective habits sustainably.",
        }
        week_lookup = {
            "Week 1": ["Week 1", "第1周", "1"],
            "Week 2": ["Week 2", "第2周", "2"],
            "Week 3": ["Week 3", "第3周", "3"],
            "Week 4": ["Week 4", "第4周", "4"],
        }
        for week_label, aliases in week_lookup.items():
            row = {}
            for alias in aliases:
                if alias in rows_by_week:
                    row = rows_by_week[alias]
                    break
            focus_val = _execution_sentence(
                row.get("core_task") or row.get("focus") or fixed_focus_en[week_label],
                max_parts=1,
                max_len=48,
                language="en",
            )
            actions_val = (
                row.get("acceptance_standard")
                or row.get("required_actions")
                or row.get("standard")
                or fixed_actions_en[week_label]
            )
            table_rows.append([_label(week_label), _editable(focus_val), _editable(actions_val)])

        blocks = [
            _execution_goal_summary_block(fields, is_en=True),
            _text_block(
                "current_phase_goal",
                "2. Current Phase Goal",
                _length_checked(fields.get("current_phase_goal"), max_len=60),
            ),
            _text_block(
                "principle",
                "3. Core Execution Principles",
                _length_checked(fields.get("principle"), max_len=60)
                or "Build baseline consistency first; add progression gradually.",
            ),
            _execution_product_block(fields, is_en=True),
            _list_block("diet_rules", "5. Diet & Nutrition Rules", fields.get("diet_rules"), limit=3, max_item_len=60),
            _list_block("exercise_rules", "6. Exercise & Physical Activity Rules", fields.get("exercise_rules"), limit=2, max_item_len=60),
            _core_actions_block(fields.get("core_actions"), is_en=True),
            {"key": "weekly_plan", "title": "7. 4-Week Action Schedule", "kind": "table", "rows": table_rows},
            _daily_checkin_block(fields.get("daily_checkin"), is_en=True),
            _text_block(
                "weekly_standard",
                "9. Weekly Pass Criteria",
                _weekly_standard_text(fields.get("weekly_standard"), language="en"),
            ),
            _text_block(
                "adjust_stop_conditions",
                "10. Adjustment & Stopping Criteria",
                _adjust_stop_conditions_text(fields.get("adjust_stop_conditions"), language="en"),
            ),
        ]
        payload = _payload(title=f"{display_name or user_name} | {title}", recipient="", blocks=blocks)
        payload["show_recipient"] = False
        payload["show_footer_brand"] = False
        return payload

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
            _length_checked(fields.get("current_phase_goal"), max_len=32),
        ),
        _text_block(
            "principle",
            "3. 执行总原则",
            _length_checked(fields.get("principle"), max_len=28)
            or "先守底盘，再抓结果；先连续完成，再逐步加码。",
        ),
        _execution_product_block(fields),
        # 一页预算：饮食最多3条、运动最多2条，每条限长，避免真实模型输出把版面顶到第二页。
        _list_block("diet_rules", "5. 饮食规则", fields.get("diet_rules"), limit=3, max_item_len=34),
        _list_block("exercise_rules", "6. 运动规则", fields.get("exercise_rules"), limit=2, max_item_len=44),
        _core_actions_block(fields.get("core_actions")),
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
        cleaned = [_length_checked(item, max_len=max_item_len) for item in cleaned]
    return {"key": key, "title": title, "kind": "list", "items": cleaned}


def _length_checked(value: Any, *, max_len: int) -> str:
    """只检查内容预算并告警，不截断。

    这里原来会裁到 max_len 再回退到最近的标点，代价是超一个字就丢掉最后一整个
    分句，而且断口通顺、看不出被砍过：晚A 写了 36 字、上限 34 字，
    "最晚睡前1小时完成"被整句砍掉，页面上只剩"晚餐后饮用"。运动规则同理，
    要求写"类型+频率+时长+强度+禁忌时间窗口"五样，一超就可能丢掉禁忌时间窗口。

    这类丢失全是用户照着执行的依据和安全边界，比版面超一点严重得多。字数一律由
    提示词的同套上限约束（软约束），这里只留一条可观测的告警，内容照原样输出。
    仍然压掉字段内换行，避免单个字段的换行破坏目标摘要那种"一行一项"的版面。
    """

    text_val = _clean_text(value).replace("\n", " ")
    if text_val and len(text_val) > max_len:
        logger.warning(
            "document field exceeds soft length budget length=%s limit=%s text=%s",
            len(text_val),
            max_len,
            text_val,
        )
    return text_val


def _jin_to_lbs_match(m: re.Match) -> str:
    raw_range = m.group(1)
    sep = "–" if "–" in raw_range else ("-" if "-" in raw_range else ("~" if "~" in raw_range else None))
    if sep:
        parts = raw_range.split(sep)
        try:
            p1 = round(float(parts[0].strip()) * 1.10231131, 1)
            p2 = round(float(parts[1].strip()) * 1.10231131, 1)
            t1 = f"{int(p1)}" if p1 == int(p1) else f"{p1:.1f}"
            t2 = f"{int(p2)}" if p2 == int(p2) else f"{p2:.1f}"
            return f"{t1}–{t2} lbs"
        except (ValueError, IndexError):
            return f"{raw_range} lbs"
    try:
        val = round(float(raw_range.strip()) * 1.10231131, 1)
        text = f"{int(val)}" if val == int(val) else f"{val:.1f}"
        return f"{text} lbs"
    except ValueError:
        return f"{raw_range} lbs"


# 跨模块的中文哨兵值。看板分类统计和文档生成阻塞判据都按它们过滤，所以
# intake_facts 一律按中文产出，只在这里（英文渲染层）翻成英文展示。
_SENTINEL_TEXT_EN = {
    "未提供": "Not provided",
    "暂无数据": "No data yet",
    "暂未测量": "Not measured yet",
    "待补充": "To be supplemented",
    "待测量": "To be measured",
    "需后续补充": "To be added later",
    "待补齐身高体重后确定，班主任会尽快与您核对": (
        "To be confirmed once height and weight are provided; your coach will follow up shortly"
    ),
    "需补充身高体重后判断": "To be assessed once height and weight are provided",
    "待补齐身高体重后确定": "To be confirmed once height and weight are provided",
    "信息不足": "Insufficient information",
}


def _localize_band_and_units_en(text: Any) -> str:
    if not text:
        return ""
    val = _text(text)
    if not val:
        return ""
    # 缺失哨兵先整串比对，避免把「未提供」这类词替换进更长的句子里造成半中半英。
    stripped = val.strip()
    if stripped in _SENTINEL_TEXT_EN:
        return _SENTINEL_TEXT_EN[stripped]
    for sentinel, replacement in _SENTINEL_TEXT_EN.items():
        val = val.replace(sentinel, replacement)
    # Management band replacements
    val = val.replace("A带｜修复塑形", "Band A | Body Rejuvenation & Sculpting")
    val = val.replace("B带｜轻盈启动", "Band B | Gentle Energy Kickstart")
    val = val.replace("C带｜明确减脂", "Band C | Targeted Fat Loss")
    val = val.replace("D带｜系统减重", "Band D | Systematic Weight Loss")
    val = val.replace("A带", "Band A")
    val = val.replace("B带", "Band B")
    val = val.replace("C带", "Band C")
    val = val.replace("D带", "Band D")
    # 单位兜底（1 斤 = 1.1023 lbs）。目标文案本身已由 intake_facts 按语言原生生成为
    # lbs，这里只兜两种情况：历史英文文档里存的中文串，以及模型没照抄、自己写了中文。
    # 换算率必须和 intake_facts._LBS_PER_JIN 一致，改一处要同步另一处。
    val = re.sub(r"(\d+(?:\.\d+)?(?:[–\-~]\d+(?:\.\d+)?)?)\s*斤", _jin_to_lbs_match, val)
    val = re.sub(r"(\d+(?:\.\d+)?(?:[–\-~]\d+(?:\.\d+)?)?)\s*公斤", r"\1 kg", val)
    return val


def _non_negotiable_block(fields: dict[str, Any], is_en: bool = False) -> dict[str, Any]:
    value = _dict(fields.get("non_negotiable"))
    title = _text(value.get("title"))
    content = _text(value.get("content"))

    if title and content:
        body = f"{title}. {content}" if is_en else f"{title}。{content}".strip("。")
    elif title:
        body = title
    elif content:
        body = content
    else:
        body = "Protect the core action first" if is_en else "先守住最关键动作"

    return _text_block(
        "non_negotiable",
        "10. The One Non-Negotiable Commitment" if is_en else "10. 您这28天最不能打折的一件事",
        body,
    )


def _goal_response_block(fields: dict[str, Any], is_en: bool = False) -> dict[str, Any]:
    value = _dict(fields.get("goal_response"))
    title = "3. Direct Response to Your Target Goal" if is_en else "3. 对您目标的直接回应"

    content = _text(value.get("content"))
    if content:
        return _text_block("goal_response", title, _localize_band_and_units_en(content) if is_en else content)

    goal_type = _text(value.get("goal_type"))
    judgement_type = _text(value.get("judgement_type"))
    meta = []
    if goal_type in {"specific", "general"}:
        if is_en:
            meta.append(f"Goal Type: {'Specific Goal' if goal_type == 'specific' else 'General Expectation'}")
        else:
            meta.append(f"目标类型：{'具体目标' if goal_type == 'specific' else '泛化期待'}")
    if judgement_type in {"reasonable", "needs_adjustment"}:
        if is_en:
            meta.append(f"Assessment: {'Needs Adjustment' if judgement_type == 'needs_adjustment' else 'Reasonable Baseline'}")
        else:
            meta.append(f"判断：{'需要调整' if judgement_type == 'needs_adjustment' else '当前合理'}")
    if is_en:
        lines = [
            *meta,
            f"User Goal: {_localize_band_and_units_en(value.get('user_goal'))}",
            f"Direct Response: {_localize_band_and_units_en(value.get('judgement')) or ('Currently needs adjustment' if judgement_type == 'needs_adjustment' else '')}",
            f"Standard Goal: {_localize_band_and_units_en(value.get('base_goal'))}",
            f"High Commitment Goal: {_localize_band_and_units_en(value.get('challenge_goal'))}",
            f"Execution Agreement: {_localize_band_and_units_en(value.get('conditions'))}",
        ]
        return _text_block("goal_response", title, "\n".join(line for line in lines if not line.endswith(": ")))

    lines = [
        *meta,
        f"用户目标：{_text(value.get('user_goal'))}",
        f"直接回应：{_text(value.get('judgement')) or ('当前需要调整' if judgement_type == 'needs_adjustment' else '')}",
        f"标准目标：{_text(value.get('base_goal'))}",
        f"高承诺目标：{_text(value.get('challenge_goal'))}",
        f"执行约定：{_text(value.get('conditions'))}",
    ]
    return _text_block("goal_response", title, "\n".join(line for line in lines if not line.endswith("：")))


def _core_conclusions_block(fields: dict[str, Any], is_en: bool = False) -> dict[str, Any]:
    value = _dict(fields.get("core_conclusions"))
    success_path = _dict(fields.get("success_path"))
    overview = _dict(fields.get("overview"))
    raw_management_band = _text(value.get("management_band") or success_path.get("weight_band"))
    management_band = _localize_band_and_units_en(raw_management_band) if is_en else raw_management_band
    body_trend = _text(value.get("body_trend") or overview.get("recent_change")) or ("Insufficient information" if is_en else "信息不足")
    direct_evidence = _clean_text(value.get("trend_direct_evidence"))
    support_signals = value.get("trend_support_signals")
    if not isinstance(support_signals, list):
        support_signals = [support_signals] if _text(support_signals) else []
    sep = "; " if is_en else "；"
    support_text = sep.join(_clean_text(item) for item in support_signals[:2] if _text(item))
    if not direct_evidence and not support_text:
        legacy = value.get("trend_evidence")
        if isinstance(legacy, list) and legacy:
            direct_evidence = _clean_text(legacy[0])
            support_text = sep.join(_clean_text(item) for item in legacy[1:3] if _text(item))
    trend_explanation = _text(value.get("trend_explanation"))
    trend_prefix = "Recent 3-Month Body Trend: " if is_en else "近3个月身体趋势："
    trend_line = f"{trend_prefix}{body_trend}"
    if trend_explanation:
        trend_line = f"{trend_line} — {trend_explanation}" if is_en else f"{trend_line}——{trend_explanation}"
    trend_basis = direct_evidence
    if support_text:
        trend_basis = f"{trend_basis}{sep}{support_text}" if trend_basis else support_text
    if is_en:
        lines = [
            _text(fields.get("one_sentence")),
            f"Management Band: {management_band}" if management_band else "",
            f"Band Rationale: {_text(value.get('band_explanation'))}" if _text(value.get("band_explanation")) else "",
            trend_line,
            f"Trend Evidence: {trend_basis}" if trend_basis else "",
            f"First 28 Days Focus: {_text(value.get('day28_task'))}" if _text(value.get("day28_task")) else "",
        ]
        return _text_block(
            "core_conclusions",
            "2. Summary Conclusion & Core Assessments",
            "\n".join(line for line in lines if line),
        )
    lines = [
        _text(fields.get("one_sentence")),
        f"管理带：{management_band}" if management_band else "",
        f"管理带解释：{_text(value.get('band_explanation'))}" if _text(value.get("band_explanation")) else "",
        trend_line,
        f"趋势依据：{trend_basis}" if trend_basis else "",
        f"这28天先做什么：{_text(value.get('day28_task'))}" if _text(value.get("day28_task")) else "",
    ]
    return _text_block(
        "core_conclusions",
        "2. 一句话结论与两项核心判断",
        "\n".join(line for line in lines if line),
    )


def _priority_items_block(fields: dict[str, Any], is_en: bool = False) -> dict[str, Any]:
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
        "7. Top 3 Priorities for You" if is_en else "6. 您真正需要优先解决的三件事",
        items,
        limit=3,
    )


def _rhythm_recovery_block(fields: dict[str, Any], is_en: bool = False) -> dict[str, Any]:
    if is_en:
        lines = [
            _text(fields.get("current_state")),
            f"Appetite & Post-Meal State: {_text(fields.get('insulin_resistance'))}" if _text(fields.get('insulin_resistance')) else "",
            f"Satiety & Evening Wind-Down: {_text(fields.get('leptin_sensitivity'))}" if _text(fields.get('leptin_sensitivity')) else "",
            f"Sleep, Stress & Recovery: {_text(fields.get('hormone_stage'))}" if _text(fields.get('hormone_stage')) else "",
            f"Primary Recommended Direction: {_text(fields.get('priority_direction'))}" if _text(fields.get('priority_direction')) else "",
        ]
        return _text_block(
            "rhythm_recovery",
            "8. Metabolic Rhythm & Recovery Assessment",
            "\n".join(line for line in lines if line),
        )
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


def _missing_information_block(fields: dict[str, Any], is_en: bool = False) -> dict[str, Any]:
    value = fields.get("missing_information")
    if isinstance(value, list):
        items = value
    elif _text(value):
        items = [_text(value)]
    else:
        overview = _dict(fields.get("overview"))
        items = []
        if "未提供" in _text(overview.get("waist_bodyfat")) or "暂未测量" in _text(overview.get("waist_bodyfat")) or "Not provided" in _text(overview.get("waist_bodyfat")):
            items.append("Waist circumference or body fat % to be measured" if is_en else "腰围或体脂率待补测")
    return _list_block("missing_information", "14. Missing Information to Update" if is_en else "13. 需要补充的信息", items)


def _execution_goal_summary_block(fields: dict[str, Any], is_en: bool = False) -> dict[str, Any]:
    """V2.2 要求执行文件也能独立核对管理带及三层目标，文案保持紧凑。"""

    if is_en:
        lines = [
            f"Management Band: {_length_checked(_localize_band_and_units_en(fields.get('weight_band')), max_len=60)}",
            f"Standard Goal: {_length_checked(_localize_band_and_units_en(fields.get('base_goal')), max_len=60)}",
            f"High Commitment Goal: {_length_checked(_localize_band_and_units_en(fields.get('challenge_goal')), max_len=60)}",
            # 英文预算按中文的两倍给（中文 72）：同一句英文的字符数本来就是中文的 2 倍多。
            f"Execution Agreement: {_length_checked(_localize_band_and_units_en(fields.get('commitment_rule')), max_len=150)}",
            f"Long-term Direction: {_length_checked(_localize_band_and_units_en(fields.get('path_goal') or fields.get('long_term_goal')), max_len=80)}",
            f"Current Mainline: {_length_checked(_localize_band_and_units_en(fields.get('current_mainline')), max_len=50)}",
        ]
        return _text_block(
            "goal_summary",
            "1. Target & Goal Summary",
            "\n".join(line for line in lines if not line.endswith(": ")),
        )

    lines = [
        f"管理带：{_length_checked(fields.get('weight_band'), max_len=18)}",
        f"标准目标：{_length_checked(fields.get('base_goal'), max_len=36)}",
        f"高承诺目标：{_length_checked(fields.get('challenge_goal'), max_len=28)}",
        f"执行约定：{_length_checked(fields.get('commitment_rule'), max_len=72)}",
        f"长期方向：{_length_checked(fields.get('path_goal') or fields.get('long_term_goal'), max_len=42)}",
        f"当前主线：{_length_checked(fields.get('current_mainline'), max_len=16)}",
    ]
    return _text_block(
        "goal_summary",
        "1. 目标摘要",
        "\n".join(line for line in lines if not line.endswith("：")),
    )


def _personal_execution_basis_block(fields: dict[str, Any], is_en: bool = False) -> dict[str, Any]:
    lines: list[str] = []
    occupation = _clean_text(fields.get("occupation_context"))
    history = _clean_text(fields.get("weight_loss_history"))
    if occupation:
        lines.append(f"{'Lifestyle & Work Context: ' if is_en else '生活与工作场景：'}{occupation}")
    if history:
        lines.append(f"{'Past Experience: ' if is_en else '既往经验：'}{history}")

    chains = fields.get("key_influence_chains")
    if isinstance(chains, list):
        for index, item in enumerate(chains[:2], start=1):
            text = _clean_text(item)
            if text:
                label = f"Key Factor {index}: " if is_en else f"关键影响{index}："
                lines.append(f"{label}{text}")

    outcomes = fields.get("desired_outcomes")
    if isinstance(outcomes, list):
        outcome_parts = []
        for item in outcomes[:3]:
            if not isinstance(item, dict):
                continue
            name = _clean_text(item.get("name"))
            method = _clean_text(item.get("tracking_method"))
            if name:
                outcome_parts.append(f"{name} ({method})" if is_en and method else (f"{name}（{method}）" if method else name))
        if outcome_parts:
            sep = "; " if is_en else "；"
            lines.append(f"{'Key Target Outcomes: ' if is_en else '本期成果：'}{sep.join(outcome_parts)}")
    return _text_block(
        "personal_execution_basis",
        "4. Execution Basis Tailored to Your Life" if is_en else "与您生活相匹配的执行依据",
        "\n".join(lines),
    )


def _core_actions_block(value: Any, is_en: bool = False) -> dict[str, Any]:
    items: list[str] = []
    if isinstance(value, list):
        for action in value[:5]:
            if not isinstance(action, dict):
                continue
            anchor = _clean_text(action.get("event_anchor"))
            body = _clean_text(action.get("action"))
            fallback = _clean_text(action.get("fallback"))
            mapping = _clean_text(action.get("checkin_mapping"))
            parts = [part for part in (anchor, body) if part]
            text = (": " if is_en else "：").join(parts)
            if fallback:
                text += f"; If unable to complete: {fallback}" if is_en else f"；做不到时：{fallback}"
            if mapping:
                text += f"; Check-in: {mapping}" if is_en else f"；打卡：{mapping}"
            if text:
                items.append(text)
    return _list_block("core_actions", "7. Core Action Windows" if is_en else "核心执行动作", items, limit=5)


def _execution_product_block(fields: dict[str, Any], is_en: bool = False) -> dict[str, Any]:
    value = _dict(fields.get("product_schedule"))
    parts = [
        _product_slot_text("Morning C" if is_en else "早C", value.get("morning_c")),
        _product_slot_text("Noon B" if is_en else "午B", value.get("noon_b")),
        _product_slot_text("Evening A" if is_en else "晚A", value.get("night_a")),
    ]
    return _text_block(
        "product_schedule",
        "4. Product Schedule & Protocol" if is_en else "4. 产品排班",
        "\n".join(part for part in parts if part),
    )


def _product_slot_text(label: str, value: Any) -> str:
    text_val = _clean_prefix(_clean_text(value), label)
    if not text_val:
        return ""
    return f"{label} {_length_checked(text_val, max_len=PRODUCT_SLOT_SOFT_LIMIT)}"


def _first_week_signals_block(fields: dict[str, Any], is_en: bool = False) -> dict[str, Any]:
    value = fields.get("first_week_signals")
    title = "14. Key Signals for First 7 Days" if is_en else "14. 前7天重点看4个信号"
    if isinstance(value, list):
        lines = [_clean_text(item) for item in value if _clean_text(item)]
        return _list_block("first_week_signals", title, lines, limit=4)
    text_val = _clean_text(value)
    if not text_val:
        return _list_block("first_week_signals", title, [], limit=4)
    lines = [part.strip() for part in re_split_standard(text_val) if part.strip()]
    return _list_block("first_week_signals", title, lines or [text_val], limit=4)


def _success_path_block(fields: dict[str, Any], is_en: bool = False) -> dict[str, Any]:
    value = _dict(fields.get("success_path"))
    intro = _text(value.get("intro")) or ("Your roadmap to success extends well beyond just these 28 days." if is_en else "您的成功路径，不是只看这28天。")
    
    bullets = []
    
    wb = _text(value.get("weight_band"))
    if wb:
        if not wb.startswith("•"):
            cleaned_wb = _clean_prefix(_clean_prefix(wb, '您当前管理带'), '您当前所属减重带')
            wb = f"• {'Current Management Band: ' if is_en else '您当前管理带：'}{_localize_band_and_units_en(cleaned_wb) if is_en else cleaned_wb}"
        bullets.append(wb)
        
    tg = _text(value.get("total_goal"))
    if tg:
        if not tg.startswith("•"):
            cleaned_tg = _clean_prefix(tg, '您当前更适合的总路径目标')
            tg = f"• {'Overall Path Target: ' if is_en else '您当前更适合的总路径目标：'}{_localize_band_and_units_en(cleaned_tg) if is_en else cleaned_tg}"
        bullets.append(tg)
        
    d28 = _text(value.get("day28_goal"))
    if d28:
        if not d28.startswith("•"):
            cleaned_d28 = _clean_prefix(d28, '第一个28天')
            d28 = f"• {'First 28 Days: ' if is_en else '第一个28天：'}{_localize_band_and_units_en(cleaned_d28) if is_en else cleaned_d28}"
        bullets.append(d28)
        
    w5_8 = _text(value.get("week5_8"))
    if w5_8:
        if not w5_8.startswith("•"):
            cleaned_w5_8 = _clean_prefix(w5_8, '第5–8周')
            w5_8 = f"• {'Weeks 5–8: ' if is_en else '第5–8周：'}{_localize_band_and_units_en(cleaned_w5_8) if is_en else cleaned_w5_8}"
        bullets.append(w5_8)
        
    w9_12 = _text(value.get("week9_12"))
    if w9_12:
        if not w9_12.startswith("•"):
            cleaned_w9_12 = _clean_prefix(w9_12, '第9–12周')
            w9_12 = f"• {'Weeks 9–12: ' if is_en else '第9–12周：'}{_localize_band_and_units_en(cleaned_w9_12) if is_en else cleaned_w9_12}"
        bullets.append(w9_12)
        
    lines = [intro] + bullets
    return _text_block("success_path", "6. 28-Day Success Roadmap" if is_en else "5. 您的成功路径，不是只看这28天", "\n".join(line for line in lines if line))


def _module_block(fields: dict[str, Any], is_en: bool = False) -> dict[str, Any]:
    value = _dict(fields.get("module_diagnosis"))
    if is_en:
        lines = [
            f"Rhythm: {_text(value.get('rhythm'))}",
            f"Diet: {_text(value.get('diet'))}",
            f"Sleep: {_text(value.get('sleep'))}",
            f"Stress: {_text(value.get('pressure'))}",
            f"Physical Activity: {_text(value.get('activity'))}",
        ]
        return _text_block("module_diagnosis", "9. Modular Lifestyle Analysis", "\n".join(line for line in lines if not line.endswith(": ")))
    lines = [
        f"节律：{_text(value.get('rhythm'))}",
        f"饮食：{_text(value.get('diet'))}",
        f"睡眠：{_text(value.get('sleep'))}",
        f"压力：{_text(value.get('pressure'))}",
        f"身体活动：{_text(value.get('activity'))}",
    ]
    return _text_block("module_diagnosis", "8. 模块诊断：节律 / 饮食 / 睡眠 / 压力 / 身体活动", "\n".join(line for line in lines if not line.endswith("：")))


def _expected_block(fields: dict[str, Any], is_en: bool = False) -> dict[str, Any]:
    value = _dict(fields.get("expected_changes"))
    lines = [
        _text(value.get("day7")),
        _text(value.get("day14_28")),
    ]
    labelled = []
    if lines[0]:
        labelled.append(f"{'First 7 Days: ' if is_en else '前7天先看：'}{lines[0]}")
    if lines[1]:
        labelled.append(f"{'Days 14–28: ' if is_en else '第14–28天再看：'}{lines[1]}")
    return _text_block("expected_changes", "12. Expected Milestones" if is_en else "11. 您可能先看到的变化", "\n".join(labelled))


def _product_block(fields: dict[str, Any], is_en: bool = False) -> dict[str, Any]:
    value = _dict(fields.get("product_schedule"))
    summary = _text(value.get("summary"))
    morning = _text(value.get("morning_c"))
    noon = _text(value.get("noon_b"))
    night = _text(value.get("night_a"))
    
    bullets = []
    if morning:
        if not morning.startswith("•"):
            morning = f"• {'Morning C: ' if is_en else '早C：'}{_clean_prefix(morning, 'Morning C' if is_en else '早C')}"
        bullets.append(morning)
    if noon:
        if not noon.startswith("•"):
            noon = f"• {'Noon B: ' if is_en else '午B：'}{_clean_prefix(noon, 'Noon B' if is_en else '午B')}"
        bullets.append(noon)
    if night:
        if not night.startswith("•"):
            night = f"• {'Evening A: ' if is_en else '晚A：'}{_clean_prefix(night, 'Evening A' if is_en else '晚A')}"
        bullets.append(night)
        
    lines = []
    if summary:
        lines.append(summary)
    lines.extend(bullets)
    
    meaning = _text(fields.get("product_meaning")) or (
        ("Morning C, Noon B, and Evening A establish steady rhythm cues across the 28 days to energize mornings, sustain afternoons, and ease evening recovery. Diet and exercise remain foundational, while products support habit consistency." if is_en else
        "早C、午B、晚A 的作用是在连续28天里反复建立「启动—续航—收口」的节律信号，"
        "帮助白天启动、午后平稳、晚间收口；饮食和运动仍是主力，产品负责让执行更容易稳住。")
    )
    return _text_block(
        "product_schedule",
        "10. Product Protocol (Morning C / Noon B / Evening A)" if is_en else "9. 早C、午B、晚A对您的意义",
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


def _execution_sentence(value: Any, *, max_parts: int, max_len: int, language: str = "zh") -> str:
    is_en = language == "en"
    joiner = "; " if is_en else "；"
    strip_chars = "；;，,。. " if is_en else "；，,。 "
    text_val = _clean_text(value)
    if not text_val:
        return ""
    parts = [part.strip() for part in re_split_standard(text_val, language=language) if part.strip()]
    if not parts:
        parts = [text_val]
    selected: list[str] = []
    for part in parts:
        candidate = joiner.join([*selected, part]).strip(strip_chars)
        if selected and len(candidate) > max_len:
            break
        selected.append(part)
        if len(selected) >= max_parts:
            break
    compact = joiner.join(selected or parts[:1]).strip(strip_chars)
    if not compact:
        return ""
    if is_en:
        return compact + ("" if compact[-1] in ".!?" else ".")
    return compact + ("" if compact[-1] in "。！？" else "")


_WEEKLY_MEASURE_HINTS_EN = ("weight", "waist", "measure", "how you feel", "body feel")


def _weekly_standard_text(value: Any, language: str = "zh") -> str:
    """每周通过标准：英文侧的兜底与补句必须是英文，长度预算也要放宽。

    英文表达同一件事的字符数约为中文的 2 倍多，沿用中文预算会把第三句（正好是
    「记录体重/围度」那句）挤掉，然后又被下面的补句用中文加回来——客户看到的就是
    一行英文后面跟一句中文。
    """

    is_en = language == "en"
    text_val = _execution_sentence(
        value,
        max_parts=3,
        max_len=170 if is_en else 76,
        language=language,
    )
    if is_en:
        if not text_val:
            return (
                "Complete the product protocol and key diet actions on at least 5 days each week; "
                "hit the planned activity or step target; log weight, waist or how you feel at least once."
            )
        lowered = text_val.lower()
        if not any(hint in lowered for hint in _WEEKLY_MEASURE_HINTS_EN):
            text_val = text_val.rstrip(".") + "; log weight or waist/how you feel at least once."
        return text_val
    if not text_val:
        return "每周至少5天完成个案产品排班和关键饮食动作；运动或步数达到计划；至少记录1次体重、围度或体感。"
    if "体重" not in text_val and "围度" not in text_val and "体感" not in text_val:
        text_val = text_val.rstrip("。") + "；至少记录1次体重或围度/体感。"
    return text_val


_MEDICAL_HINTS_EN = ("medical", "physician", "doctor", "emergency")
_FALLBACK_MEDICAL_EN = (
    "If discomfort persists, or you have chest pain, difficulty breathing or noticeable "
    "palpitations, stop and seek medical care promptly."
)


def _adjust_stop_conditions_text(value: Any, language: str = "zh") -> str:
    """调整与停止条件：一页预算可以砍长度，但不能把「及时就医」砍掉。

    这一栏的分句天然按严重度递增排列（减量 → 前移 → 暂停 → 联系班主任 → 就医），
    所以固定只留前两句，丢掉的正好是最重的那句安全边界，而落库的 content_md
    看不出被砍过。做法与 _weekly_standard_text 一致：截断后缺就医就补回来。

    英文侧同理，但补回来的必须是英文——这是安全边界，混排中文等于英文客户读不到。
    """

    is_en = language == "en"
    fallback_medical = _FALLBACK_MEDICAL_EN if is_en else "持续不适或出现胸痛、呼吸困难、明显心悸时，暂停执行并及时就医。"
    text_val = _execution_sentence(
        value,
        max_parts=2,
        max_len=200 if is_en else 88,
        language=language,
    )
    if is_en:
        if not text_val:
            return (
                "If you feel palpitations, notably worse sleep or stomach discomfort, halve "
                f"Morning C / Noon B and take them earlier. {fallback_medical}"
            )
        if any(hint in text_val.lower() for hint in _MEDICAL_HINTS_EN):
            return text_val
        medical = next(
            (
                part.strip()
                for part in re_split_standard(_clean_text(value), language="en")
                if any(hint in part.lower() for hint in _MEDICAL_HINTS_EN)
            ),
            "",
        )
        tail = medical.strip("；;，,。. ") or fallback_medical.rstrip(".")
        return f"{text_val.rstrip('.')}; {tail}."
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


def re_split_standard(value: str, language: str = "zh") -> list[str]:
    import re

    if language == "en":
        # 英文一句话用句号收尾，不切句号就整段算一句，长度预算一比就整段被丢。
        # 小数点（0.5kg）和缩写（e.g.）不能切，所以只切「句号 + 空白/行尾」。
        return re.split(r"[。；;\n]+|(?<!\d)\.(?=\s|$)", value)
    return re.split(r"[。；;\n]+", value)


def _default_week_standard(week: str) -> str:
    defaults = {
        "第1周": "按个案产品排班完成；晚餐主食控制在1拳内；完成计划内活动。",
        "第2周": "连续5天完成关键饮食动作；晚餐后走15–20分钟；不额外加宵夜。",
        "第3周": "记录体重和腰围；每周安排2次轻力量；继续观察睡眠和排便。",
        "第4周": "把收口、步数和轻力量做满；复盘腰腹、睡眠和排便变化。",
    }
    return defaults.get(week, "")


def _daily_checkin_block(value: Any, is_en: bool = False) -> dict[str, Any]:
    """每日打卡块：content 供 DOCX/Markdown 渲染，checkin_items 保留结构化定义。

    结构化项不参与正文渲染（渲染只读 content），但让 H5 每日行动问卷、五项追问清单和
    画像字段都能拿到同一份 answer_type/options，不必再靠题面关键词猜答题类型。
    """

    block = _text_block(
        "daily_checkin",
        "8. Daily Check-in Commitment (5 Items)" if is_en else "8. 每日固定打卡（每天只打这5项）",
        _daily_checkin_text(value, is_en=is_en),
    )
    if isinstance(value, list):
        items = [
            {
                "order": index,
                "category": str(item.get("category") or ""),
                "title": _clean_text(item.get("title") or item.get("label")),
                "answer_type": str(item.get("answer_type") or ""),
                "options": [str(option) for option in item.get("options") or []],
                "example_answer": _clean_text(item.get("example_answer")),
            }
            for index, item in enumerate(value, start=1)
            if isinstance(item, dict) and _clean_text(item.get("title") or item.get("label"))
        ]
        if items:
            block["checkin_items"] = items
    return block


def _daily_checkin_text(value: Any, is_en: bool = False) -> str:
    if not value:
        if is_en:
            return (
                "1. Product protocol completed as planned\n"
                "2. Ate at least 2 fists of vegetables\n"
                "3. Completed planned brisk walk or strength training\n"
                "4. Sleep routine wound down on schedule\n"
                "5. Post-meal alertness score (1 = very sleepy, 5 = fully alert)"
            )
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
