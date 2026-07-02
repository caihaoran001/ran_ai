"""add inbound_events durable inbox table

Revision ID: 20260702_0030
Revises: 20260702_0029
Create Date: 2026-07-02
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260702_0030"
down_revision = "20260702_0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inbound_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("chat_id", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=True),
        sa.Column("message_id", sa.String(length=128), nullable=True),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("message_type", sa.String(length=32), nullable=False, server_default="text"),
        sa.Column("raw_payload", sa.JSON(), nullable=True),
        sa.Column("message_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_inbound_events_chat_id", "inbound_events", ["chat_id"])
    op.create_index("ix_inbound_events_message_id", "inbound_events", ["message_id"])
    op.create_index("ix_inbound_events_status", "inbound_events", ["status"])


def downgrade() -> None:
    op.drop_index("ix_inbound_events_status", table_name="inbound_events")
    op.drop_index("ix_inbound_events_message_id", table_name="inbound_events")
    op.drop_index("ix_inbound_events_chat_id", table_name="inbound_events")
    op.drop_table("inbound_events")
