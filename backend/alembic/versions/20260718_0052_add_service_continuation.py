"""add continuous service state and lifecycle audit

Revision ID: 20260718_0052
Revises: 20260716_0051
Create Date: 2026-07-18
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260718_0052"
down_revision = "20260716_0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "service_cases",
        sa.Column("service_continued_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "service_cases",
        sa.Column("service_lifecycle_events", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("service_cases", "service_lifecycle_events")
    op.drop_column("service_cases", "service_continued_at")
