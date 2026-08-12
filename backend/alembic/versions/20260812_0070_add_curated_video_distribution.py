"""add curated video queue and delivery ledgers

Revision ID: 20260812_0070
Revises: 20260812_0069
Create Date: 2026-08-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260812_0070"
down_revision = "20260812_0069"
branch_labels = None
depends_on = None


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def upgrade() -> None:
    op.create_table(
        "curated_video_source_cursors",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_group_id", sa.String(length=128), nullable=False),
        sa.Column("last_inbound_event_id", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_group_id"),
    )
    op.create_table(
        "curated_video_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_group_id", sa.String(length=128), nullable=False),
        sa.Column("source_inbound_event_id", sa.Integer(), nullable=False),
        sa.Column("source_message_id", sa.String(length=128), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("feed_payload", sa.JSON(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["source_inbound_event_id"], ["inbound_events.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_inbound_event_id"),
        sa.UniqueConstraint("source_message_id"),
    )
    op.create_index("ix_curated_video_items_source_group_id", "curated_video_items", ["source_group_id"])
    op.create_index("ix_curated_video_items_status", "curated_video_items", ["status"])
    op.create_table(
        "curated_video_daily_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_date", sa.Date(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("target_count", sa.Integer(), nullable=False),
        sa.Column("sent_count", sa.Integer(), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["item_id"], ["curated_video_items.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("item_id"),
        sa.UniqueConstraint("run_date", name="uq_curated_video_daily_run_date"),
    )
    op.create_table(
        "curated_video_deliveries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("daily_run_id", sa.Integer(), nullable=False),
        sa.Column("service_case_id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("outbound_message_id", sa.Integer(), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("channel_message_id", sa.String(length=128), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["daily_run_id"], ["curated_video_daily_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["outbound_message_id"], ["messages.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["service_case_id"], ["service_cases.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("daily_run_id", "group_id", name="uq_curated_video_delivery_run_group"),
    )
    op.create_index("ix_curated_video_deliveries_daily_run_id", "curated_video_deliveries", ["daily_run_id"])
    op.create_index("ix_curated_video_deliveries_service_case_id", "curated_video_deliveries", ["service_case_id"])
    op.create_index("ix_curated_video_deliveries_status", "curated_video_deliveries", ["status"])
    op.create_index("ix_curated_video_deliveries_outbound_message_id", "curated_video_deliveries", ["outbound_message_id"])


def downgrade() -> None:
    op.drop_index("ix_curated_video_deliveries_outbound_message_id", table_name="curated_video_deliveries")
    op.drop_index("ix_curated_video_deliveries_status", table_name="curated_video_deliveries")
    op.drop_index("ix_curated_video_deliveries_service_case_id", table_name="curated_video_deliveries")
    op.drop_index("ix_curated_video_deliveries_daily_run_id", table_name="curated_video_deliveries")
    op.drop_table("curated_video_deliveries")
    op.drop_table("curated_video_daily_runs")
    op.drop_index("ix_curated_video_items_status", table_name="curated_video_items")
    op.drop_index("ix_curated_video_items_source_group_id", table_name="curated_video_items")
    op.drop_table("curated_video_items")
    op.drop_table("curated_video_source_cursors")
