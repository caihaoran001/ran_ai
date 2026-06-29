"""add per-case agent toggle

Revision ID: 20260629_0024
Revises: 9a3e1c7b2f04
Create Date: 2026-06-29
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260629_0024"
down_revision = "9a3e1c7b2f04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "service_cases",
        sa.Column("ai_agent_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("service_cases", "ai_agent_enabled")
