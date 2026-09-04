from __future__ import annotations

"""第26→30天结营复盘的话术：读取当前生效版本、追加新版本、渲染占位符。

**代码里的 `SEED_SCRIPTS` 只是种子，不是运行时真相。** 线上以
`final_review_script_versions` 表为准（运营在后台改一句就追加一版，生效的是同语言里
version_no 最大的那条）。本仓库为此踩过一次：入营问卷模板改了代码里的默认 JSON，
线上问卷毫无变化，因为线上读的是 `questionnaire_templates` 表。所以：

- 上线要跑一次种子同步（`ensure_seeded`），把默认话术写进生产库；
- 排查"为什么发出去的还是旧话术"，先查库里当前生效版本，别看这个文件。

**每个选项都带一个不给运营改的语义标识 `key`。** 客户选「想看看其他咖啡」要触发发
会员券，选「暂停主动联系」要停掉全部主动消息——如果代码靠选项文案原文去匹配，运营
在后台改一个字这些分支就全部失效，而且不报错、测不出。所以：文案随便改，key 不动；
key 的取值由 `OPTION_KEYS` 白名单钉住，出现白名单外的 key 直接拒收整套话术。
运营可以改文案、删选项、调顺序，但**新增选项必须先在代码里加 key 和对应分支**——
一个没有代码语义支撑的选项，客户选了等于没选。

题干与选项文字出自 Lucy《阶段复盘指令_完整修订版_V2.0》第五节（2026-09-03），
落成种子时做了三处改动，均待她确认（后台可随时改回，改动成本为零）：

1. 删掉各题末尾的"其他：____"填空占位——群里是打字对话，客户想说别的直接说。
   注意"还没想好""暂时说不清"这类**保留**：它们是真实且必须被尊重的状态，
   删掉等于逼客户表态，违背她"不强迫选择续或不续"的要求。
2. "您"改成"你"——她原文用"您"，现有助教一直用"你"，同一个群两种人称很怪。
3. 权益告知与需求承接拆成两条消息——她的骨架写在同一条里，但她自己定了
   "跳过问卷不影响券资格"，合成一条就等于不答没券。

英文话术是本仓库翻译的：V2.0 末尾明确写了"本次未提供英文原文，若英文服务群共用
此流程，应在取得英文原文后同步规则"。英文群上线前应请业务复核英文措辞。
"""

import copy
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.final_review_script_version import FinalReviewScriptVersion

# 四个节点，顺序即推进顺序。
NODE_RESULTS = "results"
NODE_GOAL = "goal"
NODE_BENEFIT = "benefit"
NODE_HANDOFF = "handoff"
FINAL_REVIEW_NODES: tuple[str, ...] = (
    NODE_RESULTS,
    NODE_GOAL,
    NODE_BENEFIT,
    NODE_HANDOFF,
)

NODE_LABELS: dict[str, str] = {
    NODE_RESULTS: "结果回顾",
    NODE_GOAL: "下一阶段目标",
    NODE_BENEFIT: "权益告知与意愿",
    NODE_HANDOFF: "需求承接",
}

# 问哪道题由代码判断，不交给运营配置——这几个分支决定了会不会问错人
# （比如对明确说要暂停的客户问"你愿意继续主要是因为什么"）。
ASK_ALWAYS = "always"
ASK_OTHER_HEALTH_NEED = "other_health_need"  # R3 选了"其他健康困扰"或此前已提及
ASK_INTENT_CONTINUE = "intent_continue"  # R4 倾向继续或探索
ASK_INTENT_PAUSE = "intent_pause"  # R4 倾向暂缓或不购买
ASK_CONDITIONS: tuple[str, ...] = (
    ASK_ALWAYS,
    ASK_OTHER_HEALTH_NEED,
    ASK_INTENT_CONTINUE,
    ASK_INTENT_PAUSE,
)

# 题干里允许出现的占位符。运营写了别的占位符会在校验时报错，
# 避免"{}"原样发到客户眼前（V2.0 明令不得输出未替换占位符）。
# 中英各一套同义写法：改英文文案的人不该在题干里看到中文标记，反之亦然。
PLACEHOLDER_FOCUS = "{关注项}"
PLACEHOLDER_FOCUS_EN = "{focus_items}"
PLACEHOLDER_BENEFITS = "{权益清单}"
PLACEHOLDER_BENEFITS_EN = "{benefits}"
FOCUS_PLACEHOLDERS: tuple[str, ...] = (PLACEHOLDER_FOCUS, PLACEHOLDER_FOCUS_EN)
BENEFIT_PLACEHOLDERS: tuple[str, ...] = (PLACEHOLDER_BENEFITS, PLACEHOLDER_BENEFITS_EN)
ALLOWED_PLACEHOLDERS: tuple[str, ...] = (*FOCUS_PLACEHOLDERS, *BENEFIT_PLACEHOLDERS)

# 题目编号。B0 是权益告知句，不是提问，但同样要能被运营改文案，所以走同一套结构。
Q_RESULT_CHANGES = "R1"
Q_RESULT_HABITS = "R2"
Q_NEXT_GOAL = "R3"
Q_OTHER_HEALTH = "R3C"
Q_BENEFIT_NOTICE = "B0"
Q_SERVICE_INTENT = "R4"
Q_REASON_CONTINUE = "R5C"
Q_REASON_PAUSE = "R5P"
Q_CONTACT_PREFERENCE = "R6"

# 按提问顺序的全部题号。B0 在内——它虽然不是提问，但"权益有没有告知过"同样
# 要能被查到，否则重启或补扫时会把同一条权益消息再发一遍。
FINAL_REVIEW_QUESTION_IDS: tuple[str, ...] = (
    Q_RESULT_CHANGES,
    Q_RESULT_HABITS,
    Q_NEXT_GOAL,
    Q_OTHER_HEALTH,
    Q_BENEFIT_NOTICE,
    Q_SERVICE_INTENT,
    Q_REASON_CONTINUE,
    Q_REASON_PAUSE,
    Q_CONTACT_PREFERENCE,
)

# 选项语义白名单：只有这里列出的 key 允许出现，且只允许出现在对应题目下。
# 不在此表中的题目（R1/R2/R3C/B0）是开放题，一律不许带选项。
OPTION_KEYS: dict[str, tuple[str, ...]] = {
    Q_NEXT_GOAL: (
        "lose_weight",
        "maintain",
        "other_health",
        "daily_product",
        "pause",
        "undecided",
    ),
    Q_SERVICE_INTENT: (
        "continue_original",
        "explore_other",
        "finish_stock",
        "no_purchase_self",
        "no_followup",
        "undecided",
    ),
    Q_REASON_CONTINUE: (
        "real_change",
        "taste",
        "convenient",
        "group_service",
        "brand_trust",
        "other_need",
        "price",
    ),
    Q_REASON_PAUSE: (
        "stock_left",
        "no_result",
        "taste_mismatch",
        "discomfort",
        "price",
        "too_hard",
        "hard_to_buy",
        "service_mismatch",
        "no_need",
        "personal_time",
        "prefer_not_say",
    ),
    Q_CONTACT_PREFERENCE: (
        "normal",
        "less_contact",
        "self_serve",
        "coach_once",
        "no_product_rec",
        "pause_contact",
    ),
}

MAX_QUESTION_CHARS = 600
MAX_OPTION_CHARS = 120


def _option(key: str, text: str) -> dict[str, str]:
    return {"key": key, "text": text}


SEED_SCRIPTS: dict[str, dict[str, Any]] = {
    "zh": {
        NODE_RESULTS: {
            "questions": [
                {
                    "id": Q_RESULT_CHANGES,
                    "text": (
                        "回看这28天，你最关注的「{关注项}」分别有什么变化？"
                        "哪一点对你最有价值，哪一点没有达到期待或者变差了？"
                    ),
                    "options": [],
                    "ask_when": ASK_ALWAYS,
                },
                {
                    "id": Q_RESULT_HABITS,
                    "text": (
                        "这段时间哪些做法你能长期坚持，哪些最难做到？"
                        "产品使用、口味或群里的建议，有哪些对你有帮助，哪些需要我们改进？"
                    ),
                    "options": [],
                    "ask_when": ASK_ALWAYS,
                },
            ]
        },
        NODE_GOAL: {
            "questions": [
                {
                    "id": Q_NEXT_GOAL,
                    "text": (
                        "接下来你最想优先做什么？可以选一项，也可以有几个想法，"
                        "告诉我第一优先的那个就好："
                    ),
                    "options": [
                        _option("lose_weight", "继续减重／减脂"),
                        _option("maintain", "维持现在的体重、状态和习惯"),
                        _option("other_health", "先改善其他健康困扰"),
                        _option("daily_product", "主要找适合日常的饮品或产品"),
                        _option("pause", "暂时休息／先不安排"),
                        _option("undecided", "还没想好"),
                    ],
                    "ask_when": ASK_ALWAYS,
                },
                {
                    "id": Q_OTHER_HEALTH,
                    "text": (
                        "你更想改善哪一项？可选最多两项，并选出最重要的一项："
                        "消化／餐后胀感、睡眠、减少糖摄入、精力、排便，"
                        "也可以说别的，或者暂时说不清。"
                    ),
                    "options": [],
                    "ask_when": ASK_OTHER_HEALTH_NEED,
                },
            ]
        },
        NODE_BENEFIT: {
            "questions": [
                {
                    "id": Q_BENEFIT_NOTICE,
                    "text": "你这28天的权益已经可以领了：{权益清单}",
                    "options": [],
                    "ask_when": ASK_ALWAYS,
                },
                {
                    "id": Q_SERVICE_INTENT,
                    "text": (
                        "另外想问一句，围绕你刚才说的目标，"
                        "你目前更倾向哪种安排？可以多选，也可以先不决定："
                    ),
                    "options": [
                        _option("continue_original", "继续了解／使用28日早C午B晚A及群内服务"),
                        _option("explore_other", "想看看其他适合日常的咖啡或产品"),
                        _option("finish_stock", "先把现有产品用完，再决定"),
                        _option("no_purchase_self", "暂时不购买，先自己做习惯调整"),
                        _option("no_followup", "暂时不需要后续安排"),
                        _option("undecided", "还没想好"),
                    ],
                    "ask_when": ASK_ALWAYS,
                },
            ]
        },
        NODE_HANDOFF: {
            "questions": [
                {
                    "id": Q_REASON_CONTINUE,
                    "text": "你愿意继续、或者想试试别的产品，最主要是因为什么？",
                    "options": [
                        _option("real_change", "身体上的实际变化"),
                        _option("taste", "口味喜欢"),
                        _option("convenient", "融入日常方便"),
                        _option("group_service", "认可群里的服务"),
                        _option("brand_trust", "信任品牌"),
                        _option("other_need", "想探索其他需求"),
                        _option("price", "价格或权益合适"),
                    ],
                    "ask_when": ASK_INTENT_CONTINUE,
                },
                {
                    "id": Q_REASON_PAUSE,
                    "text": (
                        "目前让你暂缓的主要原因是什么？直接说真实情况就好，"
                        "我们会按你的节奏安排。"
                    ),
                    "options": [
                        _option("stock_left", "现有产品还没用完"),
                        _option("no_result", "变化不明显／没达到期待"),
                        _option("taste_mismatch", "口味或冲泡不合适"),
                        _option("discomfort", "有不舒服的地方"),
                        _option("price", "价格或预算"),
                        _option("too_hard", "执行太费劲"),
                        _option("hard_to_buy", "购买不方便"),
                        _option("service_mismatch", "群里的服务不适合我"),
                        _option("no_need", "暂时没有这个需求"),
                        _option("personal_time", "个人时间安排"),
                        _option("prefer_not_say", "不方便说明"),
                    ],
                    "ask_when": ASK_INTENT_PAUSE,
                },
                {
                    "id": Q_CONTACT_PREFERENCE,
                    "text": "接下来怎样联系你最合适？",
                    "options": [
                        _option("normal", "如继续服务，保持正常群内提醒与反馈"),
                        _option("less_contact", "少打扰，有关键情况再联系"),
                        _option("self_serve", "给我入口，我自己看，有需要再问"),
                        _option("coach_once", "请班主任联系我一次"),
                        _option("no_product_rec", "暂不接收产品推荐"),
                        _option("pause_contact", "暂停主动联系，需要时我再来找你"),
                    ],
                    "ask_when": ASK_ALWAYS,
                },
            ]
        },
    },
    "en": {
        NODE_RESULTS: {
            "questions": [
                {
                    "id": Q_RESULT_CHANGES,
                    "text": (
                        "Looking back on these 28 days, how did each of the things you cared "
                        "most about — {focus_items} — actually change? Which one mattered most "
                        "to you, and which one fell short of what you hoped for or got worse?"
                    ),
                    "options": [],
                    "ask_when": ASK_ALWAYS,
                },
                {
                    "id": Q_RESULT_HABITS,
                    "text": (
                        "Which of these habits could you keep up long term, and which were "
                        "hardest to do? Of the products, the taste, and the guidance in this "
                        "group — what helped you, and what should we improve?"
                    ),
                    "options": [],
                    "ask_when": ASK_ALWAYS,
                },
            ]
        },
        NODE_GOAL: {
            "questions": [
                {
                    "id": Q_NEXT_GOAL,
                    "text": (
                        "What would you most like to focus on next? Pick one, or tell me "
                        "several and which comes first:"
                    ),
                    "options": [
                        _option("lose_weight", "Keep losing weight / body fat"),
                        _option("maintain", "Maintain my current weight, condition and habits"),
                        _option("other_health", "Work on a different health concern first"),
                        _option(
                            "daily_product",
                            "Mainly looking for a drink or product for everyday use",
                        ),
                        _option("pause", "Take a break for now / nothing planned yet"),
                        _option("undecided", "Haven't decided"),
                    ],
                    "ask_when": ASK_ALWAYS,
                },
                {
                    "id": Q_OTHER_HEALTH,
                    "text": (
                        "Which would you most like to improve? Pick up to two and tell me the "
                        "most important one: digestion / bloating after meals, sleep, cutting "
                        "back on sugar, energy, bowel regularity — or something else, or you're "
                        "not sure yet."
                    ),
                    "options": [],
                    "ask_when": ASK_OTHER_HEALTH_NEED,
                },
            ]
        },
        NODE_BENEFIT: {
            "questions": [
                {
                    "id": Q_BENEFIT_NOTICE,
                    "text": "Your 28-day benefits are ready to claim: {benefits}",
                    "options": [],
                    "ask_when": ASK_ALWAYS,
                },
                {
                    "id": Q_SERVICE_INTENT,
                    "text": (
                        "One more thing — around the goal you just mentioned, which of these "
                        "fits you best right now? You can pick more than one, or hold off "
                        "deciding:"
                    ),
                    "options": [
                        _option(
                            "continue_original",
                            "Continue with the 28-day Morning C / Noon B / Evening A and this group",
                        ),
                        _option(
                            "explore_other", "Take a look at other products for everyday use"
                        ),
                        _option("finish_stock", "Finish what I already have first, then decide"),
                        _option(
                            "no_purchase_self",
                            "Not buying for now, I'll work on habits myself",
                        ),
                        _option("no_followup", "No follow-up needed for now"),
                        _option("undecided", "Haven't decided"),
                    ],
                    "ask_when": ASK_ALWAYS,
                },
            ]
        },
        NODE_HANDOFF: {
            "questions": [
                {
                    "id": Q_REASON_CONTINUE,
                    "text": (
                        "What's the main reason you'd like to continue, or to try something else?"
                    ),
                    "options": [
                        _option("real_change", "Real changes in my body"),
                        _option("taste", "I like the taste"),
                        _option("convenient", "It fits easily into my day"),
                        _option("group_service", "The support in this group"),
                        _option("brand_trust", "I trust the brand"),
                        _option("other_need", "I want to work on something else"),
                        _option("price", "The price or the offer works for me"),
                    ],
                    "ask_when": ASK_INTENT_CONTINUE,
                },
                {
                    "id": Q_REASON_PAUSE,
                    "text": (
                        "What's mainly holding you back right now? Just tell me how it really "
                        "is — we'll go at your pace."
                    ),
                    "options": [
                        _option("stock_left", "I still have product left"),
                        _option(
                            "no_result",
                            "Changes weren't clear / fell short of what I expected",
                        ),
                        _option("taste_mismatch", "The taste or the mixing didn't suit me"),
                        _option("discomfort", "Something didn't feel right physically"),
                        _option("price", "Price or budget"),
                        _option("too_hard", "Too much effort to keep up"),
                        _option("hard_to_buy", "Buying it is inconvenient"),
                        _option("service_mismatch", "This group's support isn't a fit for me"),
                        _option("no_need", "I don't need it right now"),
                        _option("personal_time", "Personal scheduling"),
                        _option("prefer_not_say", "I'd rather not say"),
                    ],
                    "ask_when": ASK_INTENT_PAUSE,
                },
                {
                    "id": Q_CONTACT_PREFERENCE,
                    "text": "How would you prefer we stay in touch from here?",
                    "options": [
                        _option(
                            "normal",
                            "If I continue, keep the normal reminders and feedback in this group",
                        ),
                        _option("less_contact", "Less often — only reach out when it matters"),
                        _option(
                            "self_serve",
                            "Just give me the link, I'll look myself and ask if I need to",
                        ),
                        _option("coach_once", "Have my coach contact me once"),
                        _option("no_product_rec", "No product recommendations for now"),
                        _option(
                            "pause_contact",
                            "Pause proactive contact, I'll come to you when I need to",
                        ),
                    ],
                    "ask_when": ASK_ALWAYS,
                },
            ]
        },
    },
}


@dataclass(frozen=True)
class FinalReviewScript:
    """当前生效的一整套话术。`version_no` 为 None 表示库里还没有任何版本（用的是种子）。"""

    language: str
    version_no: int | None
    nodes: dict[str, Any]

    def questions_for(self, node: str) -> list[dict[str, Any]]:
        entry = self.nodes.get(node)
        if not isinstance(entry, dict):
            return []
        questions = entry.get("questions")
        if not isinstance(questions, list):
            return []
        return [item for item in questions if isinstance(item, dict)]

    def question(self, question_id: str) -> dict[str, Any] | None:
        for node in FINAL_REVIEW_NODES:
            for item in self.questions_for(node):
                if item.get("id") == question_id:
                    return item
        return None

    def option_text(self, question_id: str, option_key: str, *, default: str = "") -> str:
        """按语义 key 取当前文案。统计和承接消息要显示"客户选了什么"时用。"""
        question = self.question(question_id)
        for option in (question or {}).get("options") or []:
            if isinstance(option, dict) and option.get("key") == option_key:
                return str(option.get("text") or default)
        return default


def seed_nodes(language: str) -> dict[str, Any]:
    """种子话术的深拷贝，避免调用方改到模块级常量。"""
    return copy.deepcopy(SEED_SCRIPTS.get(language) or SEED_SCRIPTS["zh"])


def normalize_nodes(nodes: object) -> dict[str, Any]:
    """校验并规范化一整套话术；任何一处不合规就整套拒收，不做局部兜底。

    整套拒收是刻意的：话术是发给客户的原话，半套合规、半套回落到种子会让运营
    以为改动生效了，实际发出去的是两个版本的混合体。
    """
    if not isinstance(nodes, dict):
        raise ValueError("话术必须是对象。")
    normalized: dict[str, Any] = {}
    seen_ids: set[str] = set()
    for node in FINAL_REVIEW_NODES:
        entry = nodes.get(node)
        if not isinstance(entry, dict):
            raise ValueError(f"缺少节点「{NODE_LABELS[node]}」的话术。")
        raw_questions = entry.get("questions")
        if not isinstance(raw_questions, list) or not raw_questions:
            raise ValueError(f"节点「{NODE_LABELS[node]}」至少要有一道题。")
        questions: list[dict[str, Any]] = []
        for raw in raw_questions:
            if not isinstance(raw, dict):
                raise ValueError(f"节点「{NODE_LABELS[node]}」的题目格式不对。")
            question_id = str(raw.get("id") or "").strip()
            if not question_id:
                raise ValueError(f"节点「{NODE_LABELS[node]}」有题目缺少编号。")
            if question_id in seen_ids:
                raise ValueError(f"题目编号「{question_id}」重复。")
            seen_ids.add(question_id)
            text = " ".join(str(raw.get("text") or "").split()).strip()
            if not text:
                raise ValueError(f"题目「{question_id}」的题干不能为空。")
            if len(text) > MAX_QUESTION_CHARS:
                raise ValueError(
                    f"题目「{question_id}」的题干不能超过 {MAX_QUESTION_CHARS} 字。"
                )
            _reject_unknown_placeholders(question_id, text)
            ask_when = str(raw.get("ask_when") or ASK_ALWAYS).strip()
            if ask_when not in ASK_CONDITIONS:
                raise ValueError(f"题目「{question_id}」的提问条件不支持：{ask_when}")
            questions.append(
                {
                    "id": question_id,
                    "text": text,
                    "options": _normalize_options(question_id, raw.get("options")),
                    "ask_when": ask_when,
                }
            )
        normalized[node] = {"questions": questions}
    _require_seeded_questions(seen_ids)
    return normalized


def _require_seeded_questions(seen_ids: set[str]) -> None:
    """代码里有分支依赖的题目不能被运营删掉。

    删掉 R4 或 R6 不会报错，只会让"发会员券""少打扰生效"这些分支永远等不到答案，
    表现为功能静默失效——所以在保存时就拦住。
    """
    required = {
        Q_NEXT_GOAL,
        Q_BENEFIT_NOTICE,
        Q_SERVICE_INTENT,
        Q_CONTACT_PREFERENCE,
    }
    missing = sorted(required - seen_ids)
    if missing:
        raise ValueError(f"以下题目有代码分支依赖，不能删除：{'、'.join(missing)}")


def _normalize_options(question_id: str, values: object) -> list[dict[str, str]]:
    allowed = OPTION_KEYS.get(question_id)
    if values in (None, ""):
        values = []
    if not isinstance(values, list):
        raise ValueError(f"题目「{question_id}」的选项必须是列表。")
    if allowed is None:
        if values:
            raise ValueError(f"题目「{question_id}」是开放题，不能带选项。")
        return []
    options: list[dict[str, str]] = []
    seen_keys: set[str] = set()
    for value in values:
        if not isinstance(value, dict):
            raise ValueError(f"题目「{question_id}」的选项必须是对象（含 key 和 text）。")
        key = str(value.get("key") or "").strip()
        if key not in allowed:
            raise ValueError(
                f"题目「{question_id}」不支持选项标识「{key or '空值'}」。"
                "新增选项需要先在代码里加上对应语义，否则客户选了没有任何效果。"
            )
        if key in seen_keys:
            raise ValueError(f"题目「{question_id}」有重复选项标识：{key}")
        seen_keys.add(key)
        text = " ".join(str(value.get("text") or "").split()).strip()
        if not text:
            raise ValueError(f"题目「{question_id}」的选项「{key}」文案不能为空。")
        if len(text) > MAX_OPTION_CHARS:
            raise ValueError(
                f"题目「{question_id}」的选项文案不能超过 {MAX_OPTION_CHARS} 字。"
            )
        options.append({"key": key, "text": text})
    if not options:
        raise ValueError(f"题目「{question_id}」至少要保留一个选项。")
    return options


def _reject_unknown_placeholders(question_id: str, text: str) -> None:
    """题干里只允许已知占位符：未知占位符会原样发到客户眼前。"""
    remainder = text
    for placeholder in ALLOWED_PLACEHOLDERS:
        remainder = remainder.replace(placeholder, "")
    if "{" in remainder or "}" in remainder:
        allowed = "、".join(ALLOWED_PLACEHOLDERS)
        raise ValueError(f"题目「{question_id}」含不支持的占位符，只能使用：{allowed}")


async def get_active_script(
    session: AsyncSession, *, language: str = "zh"
) -> FinalReviewScript:
    """取该语言当前生效的话术；库里还没有版本时回落到代码种子。

    回落只为让本地开发和测试能跑起来——**生产必须先同步种子入库**，否则运营在后台
    看到的和实际发出的会是两回事（后台读库为空、发送走种子）。
    """
    lang = (language or "zh").strip() or "zh"
    row = await session.scalar(
        select(FinalReviewScriptVersion)
        .where(FinalReviewScriptVersion.language == lang)
        .order_by(FinalReviewScriptVersion.version_no.desc())
        .limit(1)
    )
    if row is None:
        return FinalReviewScript(language=lang, version_no=None, nodes=seed_nodes(lang))
    return FinalReviewScript(
        language=lang,
        version_no=row.version_no,
        nodes=normalize_nodes(row.nodes_json),
    )


async def save_script_version(
    session: AsyncSession,
    *,
    language: str,
    nodes: object,
    changed_by_name: str | None = None,
    note: str | None = None,
) -> FinalReviewScript:
    """追加一个新版本（不改旧行）。改坏了把旧版本的内容再存一次就是回滚。"""
    lang = (language or "zh").strip() or "zh"
    normalized = normalize_nodes(nodes)
    current_max = await session.scalar(
        select(FinalReviewScriptVersion.version_no)
        .where(FinalReviewScriptVersion.language == lang)
        .order_by(FinalReviewScriptVersion.version_no.desc())
        .limit(1)
    )
    version_no = int(current_max or 0) + 1
    session.add(
        FinalReviewScriptVersion(
            language=lang,
            version_no=version_no,
            nodes_json=normalized,
            changed_by_name=(changed_by_name or None),
            note=(note or None),
        )
    )
    await session.flush()
    return FinalReviewScript(language=lang, version_no=version_no, nodes=normalized)


async def ensure_seeded(
    session: AsyncSession, *, languages: tuple[str, ...] = ("zh", "en")
) -> list[str]:
    """把代码种子写进库（仅当该语言还没有任何版本）。返回本次写入的语言。

    幂等：已经有版本的语言一律不动，绝不用种子覆盖运营改过的话术。
    """
    written: list[str] = []
    for language in languages:
        existing = await session.scalar(
            select(FinalReviewScriptVersion.id)
            .where(FinalReviewScriptVersion.language == language)
            .limit(1)
        )
        if existing is not None:
            continue
        await save_script_version(
            session,
            language=language,
            nodes=seed_nodes(language),
            changed_by_name="system",
            note="代码种子首次入库",
        )
        written.append(language)
    return written


def render_question_text(
    text: str,
    *,
    focus_items: list[str] | None = None,
    benefits_text: str | None = None,
    language: str = "zh",
) -> str:
    """填充题干占位符。

    取不到关注项时**整句降级**成"最在意的那几项"，而不是留个空壳：客户看到
    "你最关注的「」"像是系统出故障，看到"你最在意的那几项"仍是一句人话。
    """
    rendered = text
    items = [str(item).strip() for item in (focus_items or []) if str(item).strip()]
    if items:
        joined = ", ".join(items[:3]) if language == "en" else "、".join(items[:3])
        for placeholder in FOCUS_PLACEHOLDERS:
            rendered = rendered.replace(placeholder, joined)
    else:
        fallback = (
            "the things you cared most about" if language == "en" else "最在意的那几项"
        )
        for placeholder in FOCUS_PLACEHOLDERS:
            # 中文题干把占位符包在书名号里，降级时要把括号一起吃掉。
            rendered = rendered.replace(f"「{placeholder}」", fallback)
            rendered = rendered.replace(placeholder, fallback)
    for placeholder in BENEFIT_PLACEHOLDERS:
        rendered = rendered.replace(placeholder, (benefits_text or "").strip())
    return " ".join(rendered.split()).strip()


def format_question_for_send(
    question: dict[str, Any],
    *,
    focus_items: list[str] | None = None,
    benefits_text: str | None = None,
    language: str = "zh",
) -> str:
    """一道题渲染成发出去的样子：题干 + 选项逐行。选项不编号——群里客户回
    "A" 还是回原话都得能对上，编号只会让"我选3"和后台改过顺序的清单错位。
    """
    text = render_question_text(
        str(question.get("text") or ""),
        focus_items=focus_items,
        benefits_text=benefits_text,
        language=language,
    )
    options = [
        str(option.get("text") or "").strip()
        for option in question.get("options") or []
        if isinstance(option, dict) and str(option.get("text") or "").strip()
    ]
    if not options:
        return text
    return "\n".join([text, *options])
