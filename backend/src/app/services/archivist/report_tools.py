"""档案官 extract Agent 的 report-only 工具（决策 A）。

档案官做成一个真 Agent：LLM 读增量后**自主决定**调哪几个 report_* 工具、调几次
（一条消息可同时是打卡+成效+画像）。**关键：这些工具只"上报"、不落库**——
它们仅承载 LLM 抽出的结构化意图（供 bind_tools 让模型看到 schema），真正的 DB 写
（按天 UPSERT、影子对账等）全部留在 graph 的 commit 节点确定性代码里。

因此这里工具函数是 no-op 占位（返回 "ok"）；extract_agent 直接读 AIMessage.tool_calls，
用下面的 Pydantic 模型校验入参后收集，不依赖 StructuredTool 的执行副作用。
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

# 工具名（extract_agent 按名分流到 collector 的三个桶）。
REPORT_CHECKIN = "report_checkin"
REPORT_OUTCOME = "report_outcome"
REPORT_PROFILE_FACT = "report_profile_fact"

# report_* 工具对应 collector 的桶名。
KIND_CHECKIN = "checkin"
KIND_OUTCOME = "outcome"
KIND_PROFILE = "profile"


class ReportCheckinArgs(BaseModel):
    """上报当天可结构化的打卡字段；只填用户明确说了的，没提到的留空。"""

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


class ReportOutcomeArgs(BaseModel):
    """上报一条成效事件（阶段性进展/变化）。每条事件调用一次。"""

    field_key: str = Field(description="成效字段 key（如 weight_change/sleep_change/energy_change 等）")
    event_type: str = Field(
        description="必须从以下值中选择：improved / worsened / unchanged / completed / not_completed / feedback / discomfort"
    )
    value: str = Field(description="提取出的简短事实")
    evidence_text: str = Field(description="用户原话证据")
    confidence: float = Field(default=0.0, description="0.0-1.0 置信度")


class ReportProfileFactArgs(BaseModel):
    """上报一条长期画像信号（稳定偏好/习惯）。每条调用一次，一批最多 3 条。"""

    domain: str = Field(description="personal_preferences 或 lifestyle_habits")
    signal_type: str = Field(
        description=(
            "hobby / activity_preference / food_preference / communication_preference / "
            "motivation_preference / meal_pattern / schedule_constraint / work_routine / "
            "exercise_habit / sleep_habit / social_context / other"
        )
    )
    value: str = Field(description="简短、可长期复用的事实（不要写成建议/评价/医学判断）")
    evidence_text: str = Field(description="用户原话证据")
    confidence: float = Field(default=0.0, description="0.0-1.0 置信度")


# 工具名 → (Pydantic 模型, collector 桶名)。extract_agent 用它校验入参并归桶。
REPORT_SPECS: dict[str, tuple[type[BaseModel], str]] = {
    REPORT_CHECKIN: (ReportCheckinArgs, KIND_CHECKIN),
    REPORT_OUTCOME: (ReportOutcomeArgs, KIND_OUTCOME),
    REPORT_PROFILE_FACT: (ReportProfileFactArgs, KIND_PROFILE),
}


def _noop(**_: Any) -> str:
    # 占位：真正的收集在 extract_agent 里做（读 tool_calls），此函数只为 StructuredTool 提供可调用体。
    return "ok"


def build_report_tools(kinds: list[str]) -> list[StructuredTool]:
    """按启用的 kinds 构建 report-only 工具（仅供 bind_tools 让 LLM 看到 schema）。

    kinds 取值：checkin / outcome / profile。
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
