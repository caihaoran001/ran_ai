"""Preview or repair check-ins dropped by the numeric evidence-ID schema bug.

Run inside the backend container. The default is read-only preview. Use ``--apply``
only after reviewing every affected service case/date printed by the preview.
The repair never replays outcomes, profiles, summaries, FAQs, or case drafts.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone

from app.db.session import SessionLocal
from app.services.archivist.checkin_recovery import recover_numeric_evidence_id_checkins


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="实际补写仍为空的打卡字段和答案；默认只预览。",
    )
    parser.add_argument(
        "--trace-id",
        action="append",
        type=int,
        dest="trace_ids",
        help="仅处理指定抽取留痕，可重复传入。",
    )
    parser.add_argument(
        "--since",
        help="仅处理该 UTC 时间之后的留痕，例如 2026-07-26 或 2026-07-26T00:00:00+00:00。",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="最多处理多少条受影响留痕；用于分批预览或补写。",
    )
    return parser.parse_args()


def _parse_since(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


async def run(args: argparse.Namespace) -> None:
    async with SessionLocal() as session:
        report = await recover_numeric_evidence_id_checkins(
            session,
            apply=args.apply,
            trace_ids=set(args.trace_ids or []) or None,
            since=_parse_since(args.since),
            limit=args.limit,
        )
        for detail in report["details"]:
            print(json.dumps(detail, ensure_ascii=False, sort_keys=True))
        print(json.dumps(report["stats"], ensure_ascii=False, sort_keys=True))
        if args.apply:
            await session.commit()
            print("补录完成：只写入原本为空的打卡内容，并已记录修复标记。")
        else:
            await session.rollback()
            print("预览完成：未写入任何数据；确认清单后加 --apply。")


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
