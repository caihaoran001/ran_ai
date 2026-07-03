"""add recurrence to agent reminder tasks

Revision ID: 20260703_0032
Revises: 20260703_0031
Create Date: 2026-07-03
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260703_0032"
down_revision = "20260703_0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_reminder_tasks",
        sa.Column("recurrence", sa.String(length=16), nullable=False, server_default="none"),
    )


def downgrade() -> None:
    op.drop_column("agent_reminder_tasks", "recurrence")
