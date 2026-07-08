"""drop service_cases.morning_push_enabled (removed 个性化提醒 feature)

Revision ID: 20260708_0034
Revises: 20260707_0033
Create Date: 2026-07-08
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260708_0034"
down_revision = "20260707_0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("service_cases", "morning_push_enabled")


def downgrade() -> None:
    op.add_column(
        "service_cases",
        sa.Column("morning_push_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
