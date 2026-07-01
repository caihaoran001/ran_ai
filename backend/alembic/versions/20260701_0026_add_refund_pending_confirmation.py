"""add refund_pending_confirmation to service_cases

Revision ID: 20260701_0026
Revises: 20260701_0025
Create Date: 2026-07-01
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260701_0026"
down_revision = "20260701_0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "service_cases",
        sa.Column(
            "refund_pending_confirmation",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("service_cases", "refund_pending_confirmation")
