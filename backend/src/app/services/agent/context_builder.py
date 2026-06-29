from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user_profile import UserProfile
from app.models.user_state_profile import UserStateProfile


MAX_MEMORY_LINES = 8


async def build_agent_memory_context(
    session: AsyncSession,
    *,
    service_case_id: int,
    max_lines: int = MAX_MEMORY_LINES,
) -> str:
    """Build short implicit user background for the agent.

    This is not a source of live facts like service day or weight trend. Those
    still need data tools. The goal is to let replies naturally reflect stable
    preferences, habits and current action focus without dumping a full profile.
    """

    lines: list[str] = []
    profile = await session.scalar(
        select(UserProfile)
        .where(UserProfile.service_case_id == service_case_id)
        .order_by(UserProfile.created_at.desc())
        .limit(1)
    )
    if profile:
        lines.extend(_profile_memory_lines(profile))

    state_profile = await session.scalar(
        select(UserStateProfile)
        .where(UserStateProfile.service_case_id == service_case_id)
        .order_by(UserStateProfile.updated_at.desc())
        .limit(1)
    )
    if state_profile:
        lines.extend(_state_memory_lines(state_profile.state_json))

    compact = _dedupe_keep_order(lines)
    if not compact:
        return "暂无可用用户背景。"
    return "\n".join(f"- {line}" for line in compact[: max(1, max_lines)])


def _profile_memory_lines(profile: UserProfile) -> list[str]:
    profile_json = profile.profile_json if isinstance(profile.profile_json, dict) else {}
    overview = _dict(profile_json.get("overview"))
    need_analysis = _dict(profile_json.get("need_analysis"))
    lines: list[str] = []

    summary = _short_text(profile.summary or profile_json.get("summary"), 90)
    if summary:
        lines.append(f"画像摘要：{summary}")
    for label, value in (
        ("核心问题", profile_json.get("main_problem")),
        ("28天目标", profile_json.get("day28_goal")),
        ("真实需求", need_analysis.get("priority_need")),
        ("减重带", overview.get("weight_loss_band") or profile_json.get("weight_loss_band")),
        ("明确风险", profile_json.get("risks")),
    ):
        text = _short_text(value, 80)
        if text:
            lines.append(f"{label}：{text}")
    return lines


def _state_memory_lines(state_json: object) -> list[str]:
    if not isinstance(state_json, dict):
        return []
    domains = state_json.get("domains")
    if not isinstance(domains, dict):
        return []

    lines: list[str] = []
    for key, label in (
        ("action_focus", "当前行动重点"),
        ("personal_preferences", "个人偏好"),
        ("lifestyle_habits", "稳定习惯"),
        ("need_analysis", "当前需求"),
        ("body_feedback", "近期体感"),
        ("product_usage", "产品执行"),
        ("outcome_changes", "成效变化"),
    ):
        value = _domain_current_value(domains.get(key))
        text = _format_domain_value(key, value)
        if text:
            lines.append(f"{label}：{text}")
    return lines


def _domain_current_value(domain: object) -> dict[str, Any]:
    if not isinstance(domain, dict):
        return {}
    current = domain.get("current")
    if not isinstance(current, dict):
        return {}
    value = current.get("value")
    return value if isinstance(value, dict) else {}


def _format_domain_value(key: str, value: dict[str, Any]) -> str:
    if not value:
        return ""
    if key == "action_focus":
        focus = value.get("current_focus")
        if isinstance(focus, dict):
            return _join_parts(
                _short_text(focus.get("label"), 32),
                _short_text(focus.get("action"), 70),
                _short_text(focus.get("reason"), 70),
            )
        return _join_parts(
            _short_text(value.get("label"), 32),
            _short_text(value.get("action"), 70),
            _short_text(value.get("reason"), 70),
        )
    parts = []
    for field_value in list(value.values())[:4]:
        text = _short_text(field_value, 70)
        if text:
            parts.append(text)
    return "；".join(parts)[:180]


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _short_text(value: object, limit: int) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        text = "、".join(str(item).strip() for item in value if str(item).strip())
    elif isinstance(value, dict):
        text = "；".join(str(item).strip() for item in value.values() if str(item).strip())
    else:
        text = str(value).strip()
    if not text or text in {"未提供", "暂无数据", "None", "null"}:
        return ""
    return " ".join(text.split())[:limit]


def _join_parts(*parts: str) -> str:
    return "；".join(part for part in parts if part)


def _dedupe_keep_order(lines: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for line in lines:
        item = line.strip()
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result
