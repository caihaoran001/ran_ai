"""user controlled check-in follow-up preferences

Revision ID: 20260714_0042
Revises: 20260713_0041
Create Date: 2026-07-14
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260714_0042"
down_revision = "20260713_0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "checkin_followup_preferences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("service_case_id", sa.Integer(), sa.ForeignKey("service_cases.id"), nullable=False),
        sa.Column("topic", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("source_message_id", sa.Integer(), sa.ForeignKey("messages.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("service_case_id", "topic", name="ux_checkin_followup_preference_case_topic"),
    )
    op.create_index(
        "ix_checkin_followup_preferences_service_case_id",
        "checkin_followup_preferences",
        ["service_case_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_checkin_followup_preferences_service_case_id", table_name="checkin_followup_preferences")
    op.drop_table("checkin_followup_preferences")
