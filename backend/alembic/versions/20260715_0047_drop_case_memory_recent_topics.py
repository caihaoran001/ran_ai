"""drop recent_topics from archivist_case_memory (过度设计下线)

Revision ID: 20260715_0047
Revises: 20260715_0046
Create Date: 2026-07-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260715_0047"
down_revision = "20260715_0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # recent_topics 是可再生的记忆派生数据、非权威事实源，已确认无任何消费方（前端/API/Agent
    # 隐性背景均不再使用），随功能下线一并删列。
    op.drop_column("archivist_case_memory", "recent_topics")


def downgrade() -> None:
    op.add_column(
        "archivist_case_memory",
        sa.Column(
            "recent_topics",
            sa.JSON(),
            nullable=False,
            server_default="[]",
        ),
    )
    # 去掉 server_default，回到模型的 default=list（应用侧默认），与原始定义一致。
    op.alter_column("archivist_case_memory", "recent_topics", server_default=None)
