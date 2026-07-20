from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.archivist_case_memory import ArchivistCaseMemory
from app.models.user_profile import UserProfile
from app.models.user_state_profile import UserStateProfile


AGENT_MEMORY_SECTIONS: tuple[str, ...] = (
    "stable_facts",
    "case_summary",
    "preferences",
    "habits",
    "current_state",
    "warnings",
)

SECTION_LABELS: dict[str, str] = {
    "stable_facts": "稳定事实",
    "case_summary": "历程小结",
    "preferences": "长期偏好",
    "habits": "稳定习惯",
    "current_state": "当前状态",
    "warnings": "风险边界",
}


@dataclass(frozen=True)
class ProfileMemoryContext:
    stable_facts: list[str] = field(default_factory=list)
    case_summary: list[str] = field(default_factory=list)
    preferences: list[str] = field(default_factory=list)
    habits: list[str] = field(default_factory=list)
    current_state: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def render_for_agent(
        self,
        *,
        max_lines: int = 8,
        sections: Sequence[str] = AGENT_MEMORY_SECTIONS,
    ) -> str:
        """Render a compact prompt fragment.

        The section argument is intentionally explicit so the next iteration can
        choose only relevant memory sections without changing extraction logic.
        """

        lines: list[str] = []
        for section in sections:
            label = SECTION_LABELS.get(section, section)
            values = getattr(self, section, [])
            for value in values:
                text = _short_text(value, 120)
                if text:
                    lines.append(f"{label}：{text}")
        compact = _dedupe_keep_order(lines)
        if not compact:
            return "暂无可用用户背景。"
        return "\n".join(f"- {line}" for line in compact[: max(1, max_lines)])


async def load_profile_memory_context(
    session: AsyncSession,
    *,
    service_case_id: int,
) -> ProfileMemoryContext:
    profile = await session.scalar(
        select(UserProfile)
        .where(UserProfile.service_case_id == service_case_id)
        .order_by(UserProfile.created_at.desc())
        .limit(1)
    )
    state_profile = await session.scalar(
        select(UserStateProfile)
        .where(UserStateProfile.service_case_id == service_case_id)
        .order_by(UserStateProfile.updated_at.desc())
        .limit(1)
    )
    case_memory = await session.scalar(
        select(ArchivistCaseMemory).where(
            ArchivistCaseMemory.service_case_id == service_case_id
        )
    )

    return build_profile_memory_context(
        profile=profile,
        state_profile=state_profile,
        case_memory=case_memory,
    )


def build_profile_memory_context(
    *,
    profile: UserProfile | None,
    state_profile: UserStateProfile | None,
    case_memory: ArchivistCaseMemory | None = None,
) -> ProfileMemoryContext:
    """把已批量读取的画像记录压缩为可复用上下文。

    Agent 单用户路径和群体洞察批处理路径共用同一套字段取舍，
    但群体路径可先批量查库，避免每位用户三次查询。
    """

    stable_facts, warnings = _memory_from_profile(profile)
    preferences, habits, current_state, state_warnings = _memory_from_state(
        state_profile.state_json if state_profile else None
    )
    warnings.extend(state_warnings)
    case_summary = _memory_from_case_memory(case_memory)
    return ProfileMemoryContext(
        stable_facts=_dedupe_keep_order(stable_facts),
        case_summary=_dedupe_keep_order(case_summary),
        preferences=_dedupe_keep_order(preferences),
        habits=_dedupe_keep_order(habits),
        current_state=_dedupe_keep_order(current_state),
        warnings=_dedupe_keep_order(warnings),
    )


def _memory_from_case_memory(
    case_memory: ArchivistCaseMemory | None,
) -> list[str]:
    """档案官 case_memory → 历程小结(按句拆行)。

    滚动小结拆成短句分行,规避 render 单行 120 字截断导致后半段丢失。
    """
    if case_memory is None:
        return []
    case_summary: list[str] = []
    summary = (case_memory.summary or "").strip()
    if summary:
        for sentence in re.split(r"(?<=[。！？!?；;\n])", summary):
            text = _short_text(sentence, 110)
            if text:
                case_summary.append(text)
    return case_summary


def _memory_from_profile(profile: UserProfile | None) -> tuple[list[str], list[str]]:
    if profile is None:
        return [], []
    profile_json = profile.profile_json if isinstance(profile.profile_json, dict) else {}
    overview = _dict(profile_json.get("overview"))
    need_analysis = _dict(profile_json.get("need_analysis"))

    stable_facts: list[str] = []
    warnings: list[str] = []
    for label, value, limit in (
        ("画像摘要", profile.summary or profile_json.get("summary"), 90),
        ("核心问题", profile_json.get("main_problem"), 80),
        ("28天目标", profile_json.get("day28_goal"), 80),
        ("真实需求", need_analysis.get("priority_need"), 80),
        ("减重带", overview.get("weight_loss_band") or profile_json.get("weight_loss_band"), 60),
    ):
        text = _short_text(value, limit)
        if text:
            stable_facts.append(f"{label}：{text}")

    risk_text = _short_text(profile_json.get("risks"), 100)
    if risk_text:
        warnings.append(f"明确风险：{risk_text}")
    return stable_facts, warnings


def _memory_from_state(state_json: object) -> tuple[list[str], list[str], list[str], list[str]]:
    if not isinstance(state_json, dict):
        return [], [], [], []
    domains = state_json.get("domains")
    if not isinstance(domains, dict):
        return [], [], [], []

    preferences = _profile_domain_lines(domains.get("personal_preferences"), label="个人偏好")
    habits = _profile_domain_lines(domains.get("lifestyle_habits"), label="稳定习惯")
    current_state: list[str] = []
    for key, label in (
        ("service_state", "当前服务状态"),
        ("need_analysis", "当前需求"),
        ("body_feedback", "近期体感"),
        ("product_usage", "产品执行"),
        ("outcome_changes", "成效变化"),
        ("staff_guidance", "班主任确认安排"),
    ):
        current_state.extend(_domain_lines(domains.get(key), label=label))
    warnings = _domain_lines(domains.get("risk_profile"), label="风险信息")
    return preferences, habits, current_state, warnings


def _domain_lines(domain: object, *, label: str) -> list[str]:
    current = _domain_current(domain)
    if not current:
        return []
    values = []
    # 取最新 5 条（value 字典按写入顺序，末尾即最近的纠正/信号），避免新写入被旧值挤出注入。
    for value in list(current.values())[-5:]:
        text = _memory_value_text(value, 80)
        if text:
            values.append(text)
    compact = "；".join(values)[:180]
    return [f"{label}：{compact}"] if compact else []


def _profile_domain_lines(domain: object, *, label: str) -> list[str]:
    """Render the latest profile facts, including facts stranded in legacy history.

    Older writers kept only one current value per signal type, so two food
    preferences extracted in the same batch could overwrite each other even
    though both facts remained in domain history. New writers store a list;
    merging current and history here keeps both shapes readable without a data
    rewrite or another LLM call.
    """

    if not isinstance(domain, dict):
        return []
    current = domain.get("current") if isinstance(domain.get("current"), dict) else {}
    current_values = current.get("value") if isinstance(current.get("value"), dict) else {}
    fallback_time = str(current.get("updated_at") or "")
    candidates: dict[str, tuple[str, int]] = {}
    order = 0

    def add(raw: object, *, default_time: str = "") -> None:
        nonlocal order
        text = _memory_value_text(raw, 80)
        if not text:
            return
        occurred_at = default_time
        if isinstance(raw, dict):
            occurred_at = str(
                raw.get("last_confirmed_at")
                or raw.get("occurred_at")
                or default_time
                or ""
            )
        candidates[text] = (occurred_at, order)
        order += 1

    for raw in current_values.values():
        values = raw if isinstance(raw, list) else [raw]
        for item in values:
            add(item, default_time=fallback_time)

    history = domain.get("history") if isinstance(domain.get("history"), list) else []
    for item in history:
        add(item)

    selected = sorted(candidates.items(), key=lambda item: item[1])[-5:]
    compact = "、".join(text for text, _rank in selected)[:180]
    return [f"{label}：{compact}"] if compact else []


def _domain_current(domain: object) -> dict[str, Any]:
    if not isinstance(domain, dict):
        return {}
    current = domain.get("current")
    if not isinstance(current, dict):
        return {}
    value = current.get("value")
    return value if isinstance(value, dict) else {}


def _memory_value_text(value: object, limit: int) -> str:
    if isinstance(value, list):
        parts = [_memory_value_text(item, limit) for item in value]
        return "、".join(item for item in parts if item)[:limit]
    if isinstance(value, dict) and str(value.get("status") or "") == "resolved":
        return ""
    if isinstance(value, dict) and "value" in value:
        return _short_text(value.get("value"), limit)
    return _short_text(value, limit)


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _short_text(value: object, limit: int) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        text = "、".join(str(item).strip() for item in value if str(item).strip())
    elif isinstance(value, dict):
        text = "；".join(_memory_value_text(item, limit) for item in value.values())
    else:
        text = str(value).strip()
    text = " ".join(part for part in text.split() if part)
    if not text or text in {"未提供", "暂无数据", "None", "null"}:
        return ""
    return text[:limit]


def _dedupe_keep_order(lines: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for line in lines:
        item = line.strip()
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result
