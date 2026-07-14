"""drop service_cases.checkin_link_enabled

日打卡 H5 链接推送已整体下线（改由档案官从聊天抽取打卡），
per-case 开关 checkin_link_enabled 不再有任何引用，随列一并移除。

Revision ID: 20260714_0043
Revises: 20260714_0042
Create Date: 2026-07-14
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260714_0043"
down_revision = "20260714_0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("service_cases", "checkin_link_enabled")


def downgrade() -> None:
    op.add_column(
        "service_cases",
        sa.Column(
            "checkin_link_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
