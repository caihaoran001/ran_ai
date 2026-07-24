"""link inbound inbox events to durable business messages

Revision ID: 20260724_0058
Revises: 20260724_0057
Create Date: 2026-07-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260724_0058"
down_revision = "20260724_0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "inbound_events",
        sa.Column("business_message_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_inbound_events_business_message_id_messages",
        "inbound_events",
        "messages",
        ["business_message_id"],
        ["id"],
    )
    op.create_index(
        "ix_inbound_events_business_message_id",
        "inbound_events",
        ["business_message_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_inbound_events_business_message_id",
        table_name="inbound_events",
    )
    op.drop_constraint(
        "fk_inbound_events_business_message_id_messages",
        "inbound_events",
        type_="foreignkey",
    )
    op.drop_column("inbound_events", "business_message_id")
