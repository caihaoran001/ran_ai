"""Preview or create cumulative check-in reward qualification ledgers.

Run inside the backend container so DATABASE_URL points to the intended database.
The default is a read-only preview. ``--apply`` creates idempotent qualification /
progress rows but does not send group messages; keep CHECKIN_REWARDS_ENABLED=false
until the preview and apply output have been reviewed.
"""

from __future__ import annotations

import argparse
import asyncio
import json

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from zoneinfo import ZoneInfo

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.services.checkin_reward_settings import (
    DEFAULT_REWARD_TIERS,
    CheckinRewardTierConfig,
    get_reward_tier_config,
)
from app.services.checkin_rewards import valid_reward_checkin_dates
from app.services.service_case_queries import checkin_first_day


async def main(*, apply: bool) -> None:
    async with SessionLocal() as session:
        cases = list(
            await session.scalars(
                select(ServiceCase)
                .where(
                    ServiceCase.deleted_at.is_(None),
                    ServiceCase.status == ServiceCaseStatus.IN_SERVICE,
                )
                .options(selectinload(ServiceCase.user))
                .order_by(ServiceCase.id)
            )
        )
        details = []
        config: CheckinRewardTierConfig | None = None
        try:
            config = await get_reward_tier_config(session)
        except ValueError:
            config = None
        tier_days = (
            [tier.days for tier in config.tiers]
            if config is not None
            else [tier.days for tier in DEFAULT_REWARD_TIERS]
        )
        tz = ZoneInfo(get_settings().reminder_timezone)
        for service_case in cases:
            participating = (
                config.participates(checkin_first_day(service_case, tz))
                if config is not None
                else False
            )
            valid_dates = (
                await valid_reward_checkin_dates(
                    session,
                    service_case=service_case,
                )
                if participating
                else []
            )
            earned = [days for days in tier_days if len(valid_dates) >= days]
            if participating or valid_dates or earned:
                details.append(
                    {
                        "service_case_id": service_case.id,
                        "case_no": service_case.case_no,
                        "user_name": service_case.user.name if service_case.user else None,
                        "group_id": service_case.group_id,
                        "participating": participating,
                        "complete_checkin_days": len(valid_dates),
                        "valid_dates": [item.isoformat() for item in valid_dates],
                        "earned_tiers": earned,
                    }
                )

        if apply:
            # 延迟导入，预览路径不会意外创建任何账本行。
            from app.services.checkin_rewards import reconcile_case_rewards

            for service_case in cases:
                await reconcile_case_rewards(
                    session,
                    service_case_id=service_case.id,
                )
            await session.commit()
        else:
            await session.rollback()

    print(
        json.dumps(
            {
                "apply": apply,
                "active_cases": len(cases),
                "cases_with_complete_checkin_days": len(details),
                "cases_with_earned_tiers": sum(bool(row["earned_tiers"]) for row in details),
                "details": details,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print(
        "已创建资格账本，未发群消息。"
        if apply
        else "预览完成：未写入任何数据；确认名单后加 --apply。"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Create missing reward ledgers. This still does not send messages.",
    )
    args = parser.parse_args()
    asyncio.run(main(apply=args.apply))
