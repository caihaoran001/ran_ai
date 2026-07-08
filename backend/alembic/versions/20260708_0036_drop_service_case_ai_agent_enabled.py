"""drop service_cases.ai_agent_enabled

Revision ID: 20260708_0036
Revises: 20260708_0035
Create Date: 2026-07-08
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260708_0036"
down_revision = "20260708_0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("service_cases", "ai_agent_enabled")


def downgrade() -> None:
    op.add_column(
        "service_cases",
        sa.Column("ai_agent_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
