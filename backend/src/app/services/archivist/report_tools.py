"""档案官单轮抽取器使用的 report-only schema。

LLM 读增量后一次返回零到多个 report_* tool_calls（一条消息可同时是打卡+成效+画像）。
**关键：这些工具只"上报"、不落库**——
它们仅承载 LLM 抽出的结构化意图（供 bind_tools 让模型看到 schema），真正的 DB 写
（按天 UPSERT、成效/画像投影等）全部留在 pipeline 的 commit 阶段确定性代码里。

因此这里工具函数是 no-op 占位（返回 "ok"）；extract_agent 直接读 AIMessage.tool_calls，
用下面的 Pydantic 模型校验入参后收集，不依赖 StructuredTool 的执行副作用。
"""

from __future__ import annotations

from typing import Any, Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.services.user_state_profile import OUTCOME_FIELD_DOMAIN_MAP

# 成效维度枚举。与入库闸门 outcome_engine.KNOWN_OUTCOME_FIELDS 同源，模型看得到全集。
# 不列出来的后果是模型自造名字（真实验证里出过 body_shape_change，正确名是
# waist_or_body_change），入库时按未知维度静默丢弃，统计凭空少一条。
OUTCOME_FIELD_KEYS: tuple[str, ...] = tuple(sorted(OUTCOME_FIELD_DOMAIN_MAP))

# 工具名（extract_agent 按名分流到 collector 的各个桶）。
REPORT_CHECKIN = "report_checkin"
REPORT_OUTCOME = "report_outcome"
REPORT_PROFILE_FACT = "report_profile_fact"
REPORT_STATE_FACT = "report_state_fact"
REPORT_CORRECTION = "report_correction"
REPORT_FAQ_CANDIDATE = "report_faq_candidate"
REPORT_MEMORY = "report_memory"
REPORT_CASE_CANDIDATE = "report_case_candidate"

# report_* 工具对应 collector 的桶名。
KIND_CHECKIN = "checkin"
KIND_OUTCOME = "outcome"
KIND_PROFILE = "profile"
KIND_STATE = "state"
KIND_CORRECTION = "correction"
KIND_FAQ = "faq"
KIND_MEMORY = "memory"
KIND_CASE = "case"


class ReportFollowupAnswerArgs(BaseModel):
    """用户对一条已发送追问的直接回答。"""

    item_id: str = Field(min_length=1, description="追问上下文中的原始项目 ID")
    cycle_date: str = Field(
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="追问所属周期日期（YYYY-MM-DD）",
    )
    target_date: str = Field(
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="回答应写入的打卡日期（YYYY-MM-DD）",
    )
    answer: str = Field(min_length=1, description="用户的直接回答")
    evidence_message_id: int = Field(
        gt=0,
        description="作为证据的用户消息数字 ID",
    )


class ReportChecklistAnswerArgs(BaseModel):
    """用户在普通聊天中对当前每日清单项目的明确回答。"""

    item_id: str = Field(min_length=1, description="每日清单上下文中的原始项目 ID")
    target_date: str = Field(
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="回答应写入的打卡日期（YYYY-MM-DD）",
    )
    answer: str = Field(min_length=1, description="用户的明确回答")
    evidence_message_id: int = Field(
        gt=0,
        description="作为证据的用户消息数字 ID",
    )


class ReportCheckinArgs(BaseModel):
    """上报当天可结构化的打卡字段；只填用户明确说了的，没提到的留空。"""

    target_date: str = Field(
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="这条打卡所属的北京时间自然日，必须来自输入里的日期分组（YYYY-MM-DD）",
    )
    evidence_message_ids: list[int] | None = Field(
        default=None,
        description=(
            "支持本次固定打卡字段（如产品、饮食、运动）的用户消息数字 ID；"
            "只列输入中直接提供这些信息的用户消息，不要列助教或班主任消息。"
        ),
    )
    product_schedule: dict[str, str] | None = Field(
        default=None,
        description=(
            "产品三段依从，子键 morning_c(早C)/noon_b(午B)/night_a(晚A)/summary；"
            "仅当用户明确说了喝没喝对应产品时填'已喝/未喝'，否则整个留空。"
        ),
    )
    water: str | None = Field(default=None, description="喝水量（保留原话口径，如'3升'）")
    sleep: str | None = Field(default=None, description="睡眠（时长或作息，如'睡了6小时'）")
    mood: str | None = Field(default=None, description="情绪/状态")
    exercise: str | None = Field(default=None, description="运动")
    weight: str | None = Field(default=None, description="体重（保留原话口径，如'168斤'）")
    waist: str | None = Field(default=None, description="腰围/围度")
    diet: str | None = Field(default=None, description="饮食内容（含餐食图描述）")
    bowel: str | None = Field(default=None, description="排便")
    hunger: str | None = Field(default=None, description="饥饿感")
    sensation: str | None = Field(default=None, description="身体感受")
    discomfort: str | None = Field(default=None, description="不适")
    tomorrow_focus: str | None = Field(default=None, description="明日重点")
    followup_answers: list[ReportFollowupAnswerArgs] | None = Field(
        default=None,
        description=(
            "仅当追问上下文明确列出了待答 item_id 时填写。每项包含 item_id、cycle_date(YYYY-MM-DD)、"
            "target_date(YYYY-MM-DD)、answer、evidence_message_id；只能记录用户对该追问的直接回答。"
        ),
    )
    checklist_answers: list[ReportChecklistAnswerArgs] | None = Field(
        default=None,
        description=(
            "根据上下文给出的当前每日五项，把用户在普通聊天中明确回答的个性化项上报。"
            "每项包含 item_id、target_date(YYYY-MM-DD)、answer、evidence_message_id；"
            "只填上下文中给出的题目，未明确回答不得猜测。"
        ),
    )


class ReportOutcomeArgs(BaseModel):
    """上报一条成效维度的变化。每条事件调用一次。

    正向和负向都要报，方向由 event_type 表达——账本要能统计"多少人反馈变差"。
    优秀案例库只会取正向，负向不会被当成案例素材。
    与 report_state_fact 不是二选一，两者服务不同用途：这里是**带维度的变化账本**
    （用于统计"多少人睡眠变好/变差"），report_state_fact 是**当前怎么了**（喂给
    助教的用户背景）。同一句"睡得更差了"两边都该报，不要因为报了一边就省掉另一边。
    """

    field_key: Literal[OUTCOME_FIELD_KEYS] = Field(  # type: ignore[valid-type]
        description="成效维度，必须从枚举里选一个，不要自造名字；对不上任何维度就别调这个工具"
    )
    event_type: str = Field(
        description=(
            "必须从以下值中选择：improved（有改善）/ completed（已达成/已完成）/ "
            "worsened（变差了）/ unchanged（试了但没变化）/ not_completed（没做到/没达成）"
        )
    )
    value: str = Field(description="提取出的简短事实")
    evidence_text: str = Field(description="用户原话证据")
    confidence: float = Field(default=0.0, description="0.0-1.0 置信度")


class ReportProfileFactArgs(BaseModel):
    """上报一条长期画像信号（稳定偏好/习惯）。每条调用一次，一批最多 5 条。"""

    domain: Literal["personal_preferences", "lifestyle_habits"] = Field(
        description="personal_preferences 或 lifestyle_habits"
    )
    signal_type: Literal[
        "hobby",
        "activity_preference",
        "food_preference",
        "communication_preference",
        "motivation_preference",
        "meal_pattern",
        "hydration_habit",
        "supplement_habit",
        "schedule_constraint",
        "work_routine",
        "exercise_habit",
        "sleep_habit",
        "social_context",
    ] = Field(
        description=(
            "hobby / activity_preference / food_preference / communication_preference / "
            "motivation_preference / meal_pattern / hydration_habit / supplement_habit / "
            "schedule_constraint / work_routine / exercise_habit / sleep_habit / social_context"
        )
    )
    value: str = Field(description="简短、可长期复用的事实（不要写成建议/评价/医学判断）")
    supplement_name: str | None = Field(
        default=None,
        description=(
            "仅 signal_type=supplement_habit 时必填：用户自发在用/在吃/在咨询的具体补充剂名称。"
            "范围要放宽——除了鱼油/维生素D/辅酶Q10 这类，用户自己买的蛋白粉、膳食纤维、代餐、"
            "生酮/减脂产品、益生菌、保健品、人参/阿胶等补品、以及泡的药材/代用茶都算。"
            "只排除两类：①处方药/治疗性药物；②我们自己给的早C午B晚A（训练营配发产品）。"
            "普通一日三餐食材不算。"
        ),
    )
    supplement_category: Literal[
        "micronutrient",
        "macronutrient",
        "health_supplement",
        "herbal",
        "other",
    ] | None = Field(
        default=None,
        description=(
            "仅 signal_type=supplement_habit 时必填：给上面的补剂归类。"
            "micronutrient=维生素/矿物质等微量营养补充（维生素D、钙、铁、复合维生素等）；"
            "macronutrient=蛋白粉/膳食纤维/健康油脂/代餐等宏量营养补充；"
            "health_supplement=保健品/补品（人参、阿胶、燕窝、蜂蜜、益生菌、鱼油等成品保健品）；"
            "herbal=中式草本（泡的药材、代用茶、枸杞红枣等自泡饮）；"
            "other=确实无法归入以上四类的其他补剂"
        ),
    )
    habit_status: Literal["using", "considering", "stopped"] | None = Field(
        default=None,
        description=(
            "仅 signal_type=supplement_habit 时必填：using=明确正在/长期使用；"
            "considering=仅咨询能否吃或打算尝试，尚未确认使用；stopped=明确已停用"
        ),
    )
    evidence_text: str = Field(description="用户原话证据")
    confidence: float = Field(default=0.0, description="0.0-1.0 置信度")


class ReportStateFactArgs(BaseModel):
    """上报影响当前服务的状态、阻塞或待解决事项。每条调用一次。"""

    state_type: Literal[
        "current_obstacle",
        "execution_status",
        "negative_change",
        "discomfort",
        "mood_state",
        "willingness",
        "unresolved_question",
        "user_commitment",
        "health_concern",
        "user_belief",
    ] = Field(description="当前状态类型")
    status: Literal["active", "resolved"] = Field(
        default="active",
        description="active=当前仍成立；resolved=用户明确表示该状态已解决/不再成立",
    )
    value: str = Field(description="简短的当前事实；不写建议，不做诊断，不补充原话没有的信息")
    evidence_text: str = Field(description="用户原话证据")
    confidence: float = Field(default=0.0, description="0.0-1.0 置信度")


class ReportCorrectionArgs(BaseModel):
    """上报一条“助教回答后，班主任追加纠正/补充”的监督信号。"""

    scope: Literal["personal", "general"] = Field(
        description=(
            "personal=纠正内容是当前用户的个性化安排，需写入用户档案；"
            "general=通用知识或助教回答错误，只记录纠错事件。"
            "该工具永远不创建 FAQ 草稿"
        )
    )
    summary: str = Field(description="班主任确认的正确事实，不写成 FAQ 问答")
    applicability: str = Field(description="这条纠正在什么用户/问题/场景下适用")
    bot_message_id: int = Field(gt=0, description="被纠正的助教消息 ID")
    staff_message_id: int = Field(gt=0, description="追加纠正的班主任消息 ID")
    confidence: float = Field(default=0.0, description="0.0-1.0 置信度")


class ReportFaqCandidateArgs(BaseModel):
    """上报一条通用 FAQ 或班主任确认的个人指导。

    可提炼范围与写法（一问一答、篇幅、禁写内容）统一由系统提示词给出，这里只约束字段本身，
    刻意不重复正文规则：两处各写一遍必然漂移——曾因这里多出"动作"一词，
    把"把截图发我看"这类只属于当次对话的临时动作写进了通用知识库。
    """

    scope: Literal["general", "personal"] = Field(
        default="general",
        description=(
            "general=班主任确认、其他用户也可复用，进入人工审核；personal=只适用于当前用户，"
            "直接写入该用户档案"
        ),
    )

    question: str = Field(
        description=(
            "从用户原话提炼成脱离当轮聊天也能看懂的独立问题，一次调用只表达一个业务问题；"
            "general 还必须通用、去个人隐私、去口语化并补全必要省略"
        )
    )
    answer: str = Field(
        description="按系统提示词的写法给出的参考回答：先结论，至多再补一条必需的条件或安全边界"
    )
    category: str = Field(
        description="从以下选一：饮食执行 / 体重波动 / 睡眠作息 / 运动打卡 / 心理焦虑 / 产品使用 / 规则咨询"
    )
    tags: list[str] | None = Field(default=None, description="2-3 个核心实体标签")
    evidence_text: str = Field(description="用户提问原话，作为入库证据")
    source_message_ids: list[int] | None = Field(
        default=None, description="原文方括号里的相关消息 ID（问句与答句）"
    )
    staff_message_id: int = Field(
        gt=0,
        description="直接回答或确认该问答的班主任消息 ID；缺失或不是本群班主任消息时整条候选会被丢弃",
    )


class ReportMemoryArgs(BaseModel):
    """上报本批归档后的两类滚动小结；只传更新后的完整文本。"""

    user_summary: str = Field(
        min_length=1,
        max_length=800,
        description=(
            "更新后的用户历程小结，融合既有小结与本批新事实，保留仍成立的重点、覆盖过时信息；"
            "不超过350个中文字符，不写建议、诊断或疗效承诺"
        ),
    )
    case_material_summary: str | None = Field(
        default=None,
        max_length=1600,
        description=(
            "更新后的优秀案例素材小结。只保留可能帮助未来形成真实案例的起点困难、关键行动、"
            "阶段转折、持续改善和仍欠缺的证据；融合既有素材，不复制历史聊天原话。"
            "本批没有任何案例价值且既有素材为空时可留空"
        ),
    )


class ReportCaseCandidateArgs(BaseModel):
    """上报一条已具备完整、稳定证据链的优秀案例候选；一批最多一条。"""

    title: str = Field(min_length=1, max_length=200, description="脱敏后的简短案例标题")
    question: str = Field(
        min_length=1,
        description="案例背景：用户原先遇到的困难或所处阶段，使用抽象事实，不复制历史原话",
    )
    answer: str = Field(
        min_length=1,
        description="脱敏后的完整案例正文：关键行动、转折、持续结果和可复用价值",
    )
    category: Literal["坚持执行", "行为改善", "情绪转正", "执行突破"] = Field(
        description="案例所属领域"
    )
    tags: list[str] | None = Field(default=None, description="2-3个核心标签")
    reason: str = Field(description="为什么该变化已显著、稳定并值得进入人工审核")
    evidence: str = Field(
        description="支撑案例的抽象事实链，不复制历史聊天原话、不含个人敏感信息"
    )
    source_message_ids: list[int] | None = Field(
        default=None,
        description="本批消息或历史结构化成效所关联的消息ID，最多20个",
    )


# 工具名 → (Pydantic 模型, collector 桶名)。extract_agent 用它校验入参并归桶。
REPORT_SPECS: dict[str, tuple[type[BaseModel], str]] = {
    REPORT_CHECKIN: (ReportCheckinArgs, KIND_CHECKIN),
    REPORT_OUTCOME: (ReportOutcomeArgs, KIND_OUTCOME),
    REPORT_PROFILE_FACT: (ReportProfileFactArgs, KIND_PROFILE),
    REPORT_STATE_FACT: (ReportStateFactArgs, KIND_STATE),
    REPORT_CORRECTION: (ReportCorrectionArgs, KIND_CORRECTION),
    REPORT_FAQ_CANDIDATE: (ReportFaqCandidateArgs, KIND_FAQ),
    REPORT_MEMORY: (ReportMemoryArgs, KIND_MEMORY),
    REPORT_CASE_CANDIDATE: (ReportCaseCandidateArgs, KIND_CASE),
}


def _noop(**_: Any) -> str:
    # 占位：真正的收集在 extract_agent 里做（读 tool_calls），此函数只为 StructuredTool 提供可调用体。
    return "ok"


def build_report_tools(kinds: list[str]) -> list[StructuredTool]:
    """按启用的 kinds 构建 report-only 工具（仅供 bind_tools 让 LLM 看到 schema）。

    kinds 取值：checkin / outcome / profile / state / correction / faq / memory / case。
    """
    tools: list[StructuredTool] = []
    specs = [
        (REPORT_CHECKIN, KIND_CHECKIN, ReportCheckinArgs, "上报当天可结构化的打卡字段。"),
        (REPORT_OUTCOME, KIND_OUTCOME, ReportOutcomeArgs, "上报一条成效事件（进展/变化）。"),
        (
            REPORT_PROFILE_FACT,
            KIND_PROFILE,
            ReportProfileFactArgs,
            "上报一条长期画像信号（稳定偏好/习惯）。",
        ),
        (
            REPORT_STATE_FACT,
            KIND_STATE,
            ReportStateFactArgs,
            "上报一条影响当前服务的状态、阻塞、负向变化或待解决事项。",
        ),
        (
            REPORT_CORRECTION,
            KIND_CORRECTION,
            ReportCorrectionArgs,
            "上报一条助教回答后由班主任追加的纠正或补充。",
        ),
        (
            REPORT_FAQ_CANDIDATE,
            KIND_FAQ,
            ReportFaqCandidateArgs,
            "上报班主任确认的问答；这是创建 FAQ 草稿或个人指导的唯一入口。",
        ),
        (
            REPORT_MEMORY,
            KIND_MEMORY,
            ReportMemoryArgs,
            "上报本批归档后的用户历程小结和优秀案例素材小结。",
        ),
        (
            REPORT_CASE_CANDIDATE,
            KIND_CASE,
            ReportCaseCandidateArgs,
            "上报一条已具备完整稳定证据链、可进入人工审核的优秀案例候选。",
        ),
    ]
    for name, kind, args_model, desc in specs:
        if kind in kinds:
            tools.append(
                StructuredTool.from_function(
                    func=_noop,
                    name=name,
                    description=desc,
                    args_schema=args_model,
                )
            )
    return tools
