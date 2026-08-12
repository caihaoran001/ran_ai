"""add cumulative check-in reward and shipment ledgers

Revision ID: 20260812_0069
Revises: 20260803_0068
Create Date: 2026-08-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260812_0069"
down_revision = "20260803_0068"
branch_labels = None
depends_on = None


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
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
    )


def upgrade() -> None:
    op.create_table(
        "checkin_reward_programs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("service_case_id", sa.Integer(), nullable=False),
        sa.Column("intro_status", sa.String(length=24), nullable=False),
        sa.Column("intro_message_id", sa.Integer(), nullable=True),
        sa.Column("current_choice", sa.String(length=24), nullable=True),
        sa.Column("choice_tier_days", sa.Integer(), nullable=True),
        sa.Column("recipient_name", sa.String(length=100), nullable=True),
        sa.Column("recipient_phone", sa.String(length=32), nullable=True),
        sa.Column("shipping_address", sa.String(length=1000), nullable=True),
        sa.Column("choice_updated_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["intro_message_id"], ["messages.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["service_case_id"], ["service_cases.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("service_case_id"),
    )
    op.create_index(
        "ix_checkin_reward_programs_service_case_id",
        "checkin_reward_programs",
        ["service_case_id"],
        unique=True,
    )

    op.create_table(
        "checkin_reward_shipments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("service_case_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("recipient_name", sa.String(length=100), nullable=True),
        sa.Column("recipient_phone", sa.String(length=32), nullable=True),
        sa.Column("shipping_address", sa.String(length=1000), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("shipped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("marked_by", sa.String(length=100), nullable=True),
        sa.Column("notification_status", sa.String(length=24), nullable=False),
        sa.Column("notification_message_id", sa.Integer(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["notification_message_id"],
            ["messages.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["service_case_id"], ["service_cases.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_checkin_reward_shipments_service_case_id",
        "checkin_reward_shipments",
        ["service_case_id"],
        unique=False,
    )

    op.create_table(
        "checkin_reward_qualifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("service_case_id", sa.Integer(), nullable=False),
        sa.Column("tier_days", sa.Integer(), nullable=False),
        sa.Column("reward_code", sa.String(length=40), nullable=False),
        sa.Column("reward_name", sa.String(length=100), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("qualified_on", sa.Date(), nullable=False),
        sa.Column("notification_status", sa.String(length=24), nullable=False),
        sa.Column("notification_message_id", sa.Integer(), nullable=True),
        sa.Column("shipment_id", sa.Integer(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["notification_message_id"],
            ["messages.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["service_case_id"], ["service_cases.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["shipment_id"],
            ["checkin_reward_shipments.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "service_case_id",
            "tier_days",
            name="uq_checkin_reward_qualification_case_tier",
        ),
    )
    op.create_index(
        "ix_checkin_reward_qualifications_service_case_id",
        "checkin_reward_qualifications",
        ["service_case_id"],
        unique=False,
    )
    op.create_index(
        "ix_checkin_reward_qualifications_shipment_id",
        "checkin_reward_qualifications",
        ["shipment_id"],
        unique=False,
    )

    op.create_table(
        "checkin_reward_progress_notices",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("service_case_id", sa.Integer(), nullable=False),
        sa.Column("threshold_days", sa.Integer(), nullable=False),
        sa.Column("next_tier_days", sa.Integer(), nullable=False),
        sa.Column("notification_status", sa.String(length=24), nullable=False),
        sa.Column("notification_message_id", sa.Integer(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["notification_message_id"],
            ["messages.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["service_case_id"], ["service_cases.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "service_case_id",
            "threshold_days",
            name="uq_checkin_reward_progress_case_threshold",
        ),
    )
    op.create_index(
        "ix_checkin_reward_progress_notices_service_case_id",
        "checkin_reward_progress_notices",
        ["service_case_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_checkin_reward_progress_notices_service_case_id",
        table_name="checkin_reward_progress_notices",
    )
    op.drop_table("checkin_reward_progress_notices")
    op.drop_index(
        "ix_checkin_reward_qualifications_shipment_id",
        table_name="checkin_reward_qualifications",
    )
    op.drop_index(
        "ix_checkin_reward_qualifications_service_case_id",
        table_name="checkin_reward_qualifications",
    )
    op.drop_table("checkin_reward_qualifications")
    op.drop_index(
        "ix_checkin_reward_shipments_service_case_id",
        table_name="checkin_reward_shipments",
    )
    op.drop_table("checkin_reward_shipments")
    op.drop_index(
        "ix_checkin_reward_programs_service_case_id",
        table_name="checkin_reward_programs",
    )
    op.drop_table("checkin_reward_programs")
