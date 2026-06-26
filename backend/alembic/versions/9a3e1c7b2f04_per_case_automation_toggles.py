"""add per-case automation toggles, drop group silence flag

Revision ID: 9a3e1c7b2f04
Revises: 7fd7b730dd01
Create Date: 2026-06-26
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "9a3e1c7b2f04"
down_revision = "7fd7b730dd01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "service_cases",
        sa.Column("checkin_link_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.add_column(
        "service_cases",
        sa.Column("morning_push_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.add_column(
        "service_cases",
        sa.Column("inactive_wake_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    # 统一群内机器人开关：把原“退下”静默状态搬到 ai_auto_reply_enabled，再删旧列
    op.execute(
        "UPDATE service_cases SET ai_auto_reply_enabled = false "
        "WHERE ai_silenced_by_group_command = true"
    )
    op.drop_column("service_cases", "ai_silenced_by_group_command")


def downgrade() -> None:
    op.add_column(
        "service_cases",
        sa.Column("ai_silenced_by_group_command", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.drop_column("service_cases", "inactive_wake_enabled")
    op.drop_column("service_cases", "morning_push_enabled")
    op.drop_column("service_cases", "checkin_link_enabled")
