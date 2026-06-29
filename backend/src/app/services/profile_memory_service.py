from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user_profile import UserProfile
from app.models.user_state_profile import UserStateProfile


AGENT_MEMORY_SECTIONS: tuple[str, ...] = (
    "stable_facts",
    "preferences",
    "habits",
    "current_state",
    "warnings",
)

SECTION_LABELS: dict[str, str] = {
    "stable_facts": "稳定事实",
    "preferences": "长期偏好",
    "habits": "稳定习惯",
    "current_state": "当前状态",
    "recent_topics": "最近话题",
    "warnings": "风险边界",
}


@dataclass(frozen=True)
class ProfileMemoryContext:
    stable_facts: list[str] = field(default_factory=list)
    preferences: list[str] = field(default_factory=list)
    habits: list[str] = field(default_factory=list)
    current_state: list[str] = field(default_factory=list)
    recent_topics: list[str] = field(default_factory=list)
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

    stable_facts, warnings = _memory_from_profile(profile)
    preferences, habits, current_state, state_warnings = _memory_from_state(
        state_profile.state_json if state_profile else None
    )
    warnings.extend(state_warnings)
    return ProfileMemoryContext(
        stable_facts=_dedupe_keep_order(stable_facts),
        preferences=_dedupe_keep_order(preferences),
        habits=_dedupe_keep_order(habits),
        current_state=_dedupe_keep_order(current_state),
        warnings=_dedupe_keep_order(warnings),
    )


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

    preferences = _domain_lines(domains.get("personal_preferences"), label="个人偏好")
    habits = _domain_lines(domains.get("lifestyle_habits"), label="稳定习惯")
    current_state: list[str] = []
    for key, label in (
        ("action_focus", "当前行动重点"),
        ("need_analysis", "当前需求"),
        ("body_feedback", "近期体感"),
        ("product_usage", "产品执行"),
        ("outcome_changes", "成效变化"),
    ):
        current_state.extend(_domain_lines(domains.get(key), label=label, action_focus=(key == "action_focus")))
    warnings = _domain_lines(domains.get("risk_profile"), label="风险信息")
    return preferences, habits, current_state, warnings


def _domain_lines(domain: object, *, label: str, action_focus: bool = False) -> list[str]:
    current = _domain_current(domain)
    if not current:
        return []
    if action_focus:
        focus = current.get("current_focus")
        if isinstance(focus, dict):
            text = _join_parts(
                _short_text(focus.get("label"), 32),
                _short_text(focus.get("action"), 70),
                _short_text(focus.get("reason"), 70),
            )
        else:
            text = _join_parts(
                _short_text(current.get("label"), 32),
                _short_text(current.get("action"), 70),
                _short_text(current.get("reason"), 70),
            )
        return [f"{label}：{text}"] if text else []

    values = []
    for value in list(current.values())[:5]:
        text = _memory_value_text(value, 80)
        if text:
            values.append(text)
    compact = "；".join(values)[:180]
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


def _join_parts(*parts: str) -> str:
    return "；".join(part for part in parts if part)


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
