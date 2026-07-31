"""add intake_pending_generation_at to service_cases

Revision ID: 20260731_0064
Revises: 20260730_0063
Create Date: 2026-07-31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260731_0064"
down_revision = "20260730_0063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "service_cases",
        sa.Column(
            "intake_pending_generation_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("service_cases", "intake_pending_generation_at")
