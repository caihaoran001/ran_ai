"""add group command silence flag to service cases

Revision ID: 7fd7b730dd01
Revises: 559d7e733012
Create Date: 2026-06-25
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "7fd7b730dd01"
down_revision = "559d7e733012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "service_cases",
        sa.Column(
            "ai_silenced_by_group_command",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("service_cases", "ai_silenced_by_group_command")
