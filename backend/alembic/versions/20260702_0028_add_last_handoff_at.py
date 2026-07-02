"""add last_handoff_at to service_cases

Revision ID: 20260702_0028
Revises: 20260701_0027
Create Date: 2026-07-02
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260702_0028"
down_revision = "20260701_0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "service_cases",
        sa.Column(
            "last_handoff_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("service_cases", "last_handoff_at")
