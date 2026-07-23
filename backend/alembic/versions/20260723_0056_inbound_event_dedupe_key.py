"""add idempotency key for durable inbound events

Revision ID: 20260723_0056
Revises: 20260720_0055
Create Date: 2026-07-23
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260723_0056"
down_revision = "20260720_0055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "inbound_events",
        sa.Column("dedupe_key", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_inbound_events_dedupe_key",
        "inbound_events",
        ["dedupe_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_inbound_events_dedupe_key", table_name="inbound_events")
    op.drop_column("inbound_events", "dedupe_key")
