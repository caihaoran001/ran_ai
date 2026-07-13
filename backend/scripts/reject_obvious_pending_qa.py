"""Reject the reviewed, clearly invalid production FAQ drafts from 2026-07-13.

Run inside the backend container. The default is a read-only preview; use
``--apply`` only after every expected title has been printed and verified.
This script never approves, edits, or migrates a draft.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone

from app.db.session import SessionLocal
from app.models.knowledge_draft import KnowledgeDraft


REJECTIONS: dict[int, tuple[str, str]] = {
    3: ("系统升级后", "临时系统说明，不是稳定 FAQ"),
    8: ("便秘时胶囊", "具体产品剂量，不自动沉淀为通用 FAQ"),
    10: ("埋线减肥", "医疗决策，不自动沉淀为通用 FAQ"),
    11: ("加大早C和午B", "具体产品加量，不自动沉淀为通用 FAQ"),
    16: ("Internal Server Error", "临时系统故障，不是稳定 FAQ"),
    17: ("卡点钥匙", "具体产品剂量，不自动沉淀为通用 FAQ"),
    20: ("出差或换环境后便秘", "健康处理建议，不自动沉淀为通用 FAQ"),
    24: ("甲状腺问题", "疾病相关建议，不自动沉淀为通用 FAQ"),
    25: ("亚临床甲亢", "疾病相关建议，不自动沉淀为通用 FAQ"),
    32: ("深色液体", "助教识别失误，没有真实用户 FAQ"),
    42: ("退款", "合同承诺必须来自权威规则，不从聊天自动沉淀"),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="确认标题与状态后才实际驳回；默认仅预览。",
    )
    return parser.parse_args()


async def run(apply: bool) -> None:
    async with SessionLocal() as session:
        drafts: list[KnowledgeDraft] = []
        for draft_id, (expected_title, reason) in REJECTIONS.items():
            draft = await session.get(KnowledgeDraft, draft_id)
            if draft is None:
                raise RuntimeError(f"草稿 #{draft_id} 不存在，已停止，未写入任何数据")
            if draft.status != "pending":
                raise RuntimeError(f"草稿 #{draft_id} 当前为 {draft.status}，已停止，未写入任何数据")
            if expected_title not in draft.title:
                raise RuntimeError(
                    f"草稿 #{draft_id} 标题不匹配：期望含 {expected_title!r}，实际为 {draft.title!r}" 
                )
            drafts.append(draft)
            print(f"#{draft.id} {draft.title} -> 驳回：{reason}")

        if not apply:
            print(f"预览完成：{len(drafts)} 条；未写入。确认无误后加 --apply。")
            return

        now = datetime.now(timezone.utc)
        for draft in drafts:
            _, reason = REJECTIONS[draft.id]
            draft.status = "rejected"
            draft.rejected_at = now
            draft.rejected_reason = f"自动清理：{reason}"
        await session.commit()
        print(f"已驳回 {len(drafts)} 条；没有自动批准或迁移任何草稿。")


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(run(args.apply))
