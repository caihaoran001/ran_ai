from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models.app_setting import AppSetting


SETTING_KEY = "stage_report_schedule"
DEFAULT_GENERATE_AT = "21:00"


@dataclass
class StageReportSchedule:
    """阶段报告集中生成时间配置。

    时区不在此配置内，沿用 settings.reminder_timezone（北京时间），避免出现两个时区真相来源。
    """

    generate_at: str  # "HH:MM" 本地时间（北京）

    @property
    def hour(self) -> int:
        return int(self.generate_at.split(":")[0])

    @property
    def minute(self) -> int:
        return int(self.generate_at.split(":")[1])


async def get_stage_report_schedule(
    session: AsyncSession,
    *,
    settings: Settings | None = None,
) -> StageReportSchedule:
    settings = settings or get_settings()
    default = _default_schedule(settings)
    setting = await session.get(AppSetting, SETTING_KEY)
    if not setting:
        return default
    data = setting.value_json or {}
    return StageReportSchedule(
        generate_at=_time_text(data.get("generate_at"), default.generate_at),
    )


async def set_stage_report_schedule(
    session: AsyncSession,
    payload: dict,
    *,
    settings: Settings | None = None,
) -> StageReportSchedule:
    current = await get_stage_report_schedule(session, settings=settings)
    normalized = StageReportSchedule(
        generate_at=_time_text(payload.get("generate_at"), current.generate_at),
    )
    value_json = {"generate_at": normalized.generate_at}
    setting = await session.get(AppSetting, SETTING_KEY)
    if setting:
        setting.value_json = value_json
    else:
        session.add(AppSetting(key=SETTING_KEY, value_json=value_json))
    await session.commit()
    return normalized


def _default_schedule(settings: Settings) -> StageReportSchedule:  # noqa: ARG001 - 保留 settings 便于后续扩展
    return StageReportSchedule(generate_at=DEFAULT_GENERATE_AT)


def _time_text(value: object, default: str) -> str:
    """校验并规范化 HH:MM，非法回落 default。"""
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
