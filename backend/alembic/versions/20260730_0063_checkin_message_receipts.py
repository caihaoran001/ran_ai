"""add idempotent check-in message receipts

Revision ID: 20260730_0063
Revises: 20260727_0062
Create Date: 2026-07-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260730_0063"
down_revision = "20260727_0062"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "checkin_message_receipts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("service_case_id", sa.Integer(), nullable=False),
        sa.Column("source_message_id", sa.Integer(), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=False),
        sa.Column("checkin_id", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=True),
        sa.Column("checklist_source_ref", sa.String(length=128), nullable=False),
        sa.Column("request_json", sa.JSON(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["service_case_id"],
            ["service_cases.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_message_id"],
            ["messages.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["checkin_id"],
            ["checkins.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "service_case_id",
            "source_message_id",
            "target_date",
            name="ux_checkin_message_receipt_source_date",
        ),
    )
    op.create_index(
        "ix_checkin_message_receipts_service_case_id",
        "checkin_message_receipts",
        ["service_case_id"],
    )
    op.create_index(
        "ix_checkin_message_receipts_source_message_id",
        "checkin_message_receipts",
        ["source_message_id"],
    )
    op.create_index(
        "ix_checkin_message_receipts_target_date",
        "checkin_message_receipts",
        ["target_date"],
    )
    op.create_index(
        "ix_checkin_message_receipts_checkin_id",
        "checkin_message_receipts",
        ["checkin_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_checkin_message_receipts_checkin_id",
        table_name="checkin_message_receipts",
    )
    op.drop_index(
        "ix_checkin_message_receipts_target_date",
        table_name="checkin_message_receipts",
    )
    op.drop_index(
        "ix_checkin_message_receipts_source_message_id",
        table_name="checkin_message_receipts",
    )
    op.drop_index(
        "ix_checkin_message_receipts_service_case_id",
        table_name="checkin_message_receipts",
    )
    op.drop_table("checkin_message_receipts")
