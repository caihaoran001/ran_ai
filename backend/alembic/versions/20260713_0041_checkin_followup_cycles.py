"""durable check-in follow-up cycles

Revision ID: 20260713_0041
Revises: 20260710_0040
Create Date: 2026-07-13
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260713_0041"
down_revision = "20260710_0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "checkin_followup_cycles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("service_case_id", sa.Integer(), sa.ForeignKey("service_cases.id"), nullable=False),
        sa.Column("cycle_date", sa.Date(), nullable=False),
        sa.Column("sent_kinds", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("message_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("answered_item_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("service_case_id", "cycle_date", name="ux_checkin_followup_cycle_case_date"),
    )
    op.create_index("ix_checkin_followup_cycles_service_case_id", "checkin_followup_cycles", ["service_case_id"])
    op.create_index("ix_checkin_followup_cycles_cycle_date", "checkin_followup_cycles", ["cycle_date"])


def downgrade() -> None:
    op.drop_index("ix_checkin_followup_cycles_cycle_date", table_name="checkin_followup_cycles")
    op.drop_index("ix_checkin_followup_cycles_service_case_id", table_name="checkin_followup_cycles")
    op.drop_table("checkin_followup_cycles")
