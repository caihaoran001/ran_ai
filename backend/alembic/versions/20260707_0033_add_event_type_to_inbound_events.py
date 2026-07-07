"""add event_type to inbound_events

Revision ID: 20260707_0033
Revises: 20260703_0032
Create Date: 2026-07-07
"""

from __future__ import annotations

from alembic import op


revision = "20260707_0033"
down_revision = "20260703_0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE inbound_events "
        "ADD COLUMN IF NOT EXISTS event_type VARCHAR(32) NOT NULL DEFAULT 'message'"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_inbound_events_event_type "
        "ON inbound_events (event_type)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_inbound_events_event_type")
    op.execute("ALTER TABLE inbound_events DROP COLUMN IF EXISTS event_type")
