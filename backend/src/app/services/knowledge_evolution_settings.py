from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models.app_setting import AppSetting


SETTING_KEY = "knowledge_evolution_config"


@dataclass
class KnowledgeEvolutionConfig:
    enabled: bool
    interval_seconds: int
    qa_at: str
    daily_qa_limit: int
    case_days: str
    case_limit_per_case: int
    timezone: str


async def get_knowledge_evolution_config(
    session: AsyncSession,
    *,
    settings: Settings | None = None,
) -> KnowledgeEvolutionConfig:
    settings = settings or get_settings()
    defaults = _default_config(settings)
    setting = await session.get(AppSetting, SETTING_KEY)
    if not setting:
        return defaults
    data = setting.value_json or {}
    return KnowledgeEvolutionConfig(
        enabled=_bool(data.get("enabled"), defaults.enabled),
        interval_seconds=_int(data.get("interval_seconds"), defaults.interval_seconds, minimum=60, maximum=86400),
        qa_at=_time_text(data.get("qa_at"), defaults.qa_at),
        daily_qa_limit=_int(data.get("daily_qa_limit"), defaults.daily_qa_limit, minimum=20, maximum=2000),
        case_days=_case_days_text(data.get("case_days"), defaults.case_days),
        case_limit_per_case=_int(data.get("case_limit_per_case"), defaults.case_limit_per_case, minimum=20, maximum=1000),
        timezone=str(data.get("timezone") or defaults.timezone),
    )


async def set_knowledge_evolution_config(
    session: AsyncSession,
    payload: dict,
    *,
    settings: Settings | None = None,
) -> KnowledgeEvolutionConfig:
    current = await get_knowledge_evolution_config(session, settings=settings)
    merged = {
        "enabled": payload.get("enabled", current.enabled),
        "interval_seconds": payload.get("interval_seconds", current.interval_seconds),
        "qa_at": payload.get("qa_at", current.qa_at),
        "daily_qa_limit": payload.get("daily_qa_limit", current.daily_qa_limit),
        "case_days": payload.get("case_days", current.case_days),
        "case_limit_per_case": payload.get("case_limit_per_case", current.case_limit_per_case),
        "timezone": payload.get("timezone", current.timezone),
    }
    normalized = KnowledgeEvolutionConfig(
        enabled=_bool(merged["enabled"], current.enabled),
        interval_seconds=_int(merged["interval_seconds"], current.interval_seconds, minimum=60, maximum=86400),
        qa_at=_time_text(merged["qa_at"], current.qa_at),
        daily_qa_limit=_int(merged["daily_qa_limit"], current.daily_qa_limit, minimum=20, maximum=2000),
        case_days=_case_days_text(merged["case_days"], current.case_days),
        case_limit_per_case=_int(merged["case_limit_per_case"], current.case_limit_per_case, minimum=20, maximum=1000),
        timezone=str(merged["timezone"] or current.timezone),
    )
    setting = await session.get(AppSetting, SETTING_KEY)
    value_json = normalized.__dict__
    if setting:
        setting.value_json = value_json
    else:
        session.add(AppSetting(key=SETTING_KEY, value_json=value_json))
    await session.commit()
    return normalized


def parse_case_days(value: str) -> list[int]:
    return sorted({int(item.strip()) for item in value.split(",") if item.strip().isdigit() and int(item.strip()) > 0})


def _default_config(settings: Settings) -> KnowledgeEvolutionConfig:
    return KnowledgeEvolutionConfig(
        enabled=settings.knowledge_evolution_enabled,
        interval_seconds=int(settings.knowledge_evolution_interval_seconds),
        qa_at=settings.knowledge_evolution_qa_at,
        daily_qa_limit=500,
        case_days=settings.knowledge_evolution_case_days,
        case_limit_per_case=200,
        timezone=settings.reminder_timezone,
    )


def _bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}
    return default


def _int(value: object, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, minimum), maximum)


def _time_text(value: object, default: str) -> str:
    text = str(value or default).strip()
    parts = text.split(":")
    if len(parts) < 2:
        return default
    try:
        hour = min(max(int(parts[0]), 0), 23)
        minute = min(max(int(parts[1]), 0), 59)
    except ValueError:
        return default
    return f"{hour:02d}:{minute:02d}"


def _case_days_text(value: object, default: str) -> str:
    days = parse_case_days(str(value or default))
    return ",".join(str(day) for day in days) or default
