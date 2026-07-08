"""drop service_cases.inactive_wake_enabled (removed 无互动自动唤醒 feature)

Revision ID: 20260708_0035
Revises: 20260708_0034
Create Date: 2026-07-08
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260708_0035"
down_revision = "20260708_0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("service_cases", "inactive_wake_enabled")


def downgrade() -> None:
    op.add_column(
        "service_cases",
        sa.Column("inactive_wake_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
