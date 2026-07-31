"""add ai_balance_alerts throttle table

Revision ID: 20260731_0066
Revises: 20260731_0065
Create Date: 2026-07-31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260731_0066"
down_revision = "20260731_0065"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_balance_alerts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("alert_date", sa.Date(), nullable=False),
        sa.Column("balance_yuan", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("alert_date", name="uq_ai_balance_alert_date"),
    )


def downgrade() -> None:
    op.drop_table("ai_balance_alerts")
