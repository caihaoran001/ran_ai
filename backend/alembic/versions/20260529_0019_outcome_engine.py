"""add outcome engine tables

Revision ID: 20260529_0019
Revises: 20260522_0018
Create Date: 2026-05-29
"""

from alembic import op
import sqlalchemy as sa


revision = "20260529_0019"
down_revision = "20260522_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "outcome_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("service_case_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=True),
        sa.Column("milestone", sa.String(length=32), nullable=False),
        sa.Column("field_key", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("value_text", sa.Text(), nullable=False),
        sa.Column("evidence_text", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"]),
        sa.ForeignKeyConstraint(["service_case_id"], ["service_cases.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_outcome_events_case_milestone", "outcome_events", ["service_case_id", "milestone"], unique=False)
    op.create_index("ix_outcome_events_field", "outcome_events", ["field_key"], unique=False)
    op.create_index(op.f("ix_outcome_events_field_key"), "outcome_events", ["field_key"], unique=False)
    op.create_index(op.f("ix_outcome_events_message_id"), "outcome_events", ["message_id"], unique=False)
    op.create_index(op.f("ix_outcome_events_milestone"), "outcome_events", ["milestone"], unique=False)
    op.create_index(op.f("ix_outcome_events_service_case_id"), "outcome_events", ["service_case_id"], unique=False)

    op.create_table(
        "outcome_stage_states",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("service_case_id", sa.Integer(), nullable=False),
        sa.Column("milestone", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("collected_fields", sa.JSON(), nullable=True),
        sa.Column("missing_fields", sa.JSON(), nullable=True),
        sa.Column("last_followup_field", sa.String(length=64), nullable=True),
        sa.Column("last_followup_question", sa.Text(), nullable=True),
        sa.Column("last_followup_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("followup_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["service_case_id"], ["service_cases.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("service_case_id", "milestone", name="ux_outcome_stage_state_case_milestone"),
    )
    op.create_index(op.f("ix_outcome_stage_states_milestone"), "outcome_stage_states", ["milestone"], unique=False)
    op.create_index(op.f("ix_outcome_stage_states_service_case_id"), "outcome_stage_states", ["service_case_id"], unique=False)

    op.create_table(
        "outcome_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("service_case_id", sa.Integer(), nullable=False),
        sa.Column("milestone", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("metrics_json", sa.JSON(), nullable=True),
        sa.Column("evidence_json", sa.JSON(), nullable=True),
        sa.Column("ai_summary", sa.Text(), nullable=True),
        sa.Column("action_items", sa.JSON(), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["service_case_id"], ["service_cases.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("service_case_id", "milestone", name="ux_outcome_snapshot_case_milestone"),
    )
    op.create_index(op.f("ix_outcome_snapshots_milestone"), "outcome_snapshots", ["milestone"], unique=False)
    op.create_index(op.f("ix_outcome_snapshots_service_case_id"), "outcome_snapshots", ["service_case_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_outcome_snapshots_service_case_id"), table_name="outcome_snapshots")
    op.drop_index(op.f("ix_outcome_snapshots_milestone"), table_name="outcome_snapshots")
    op.drop_table("outcome_snapshots")

    op.drop_index(op.f("ix_outcome_stage_states_service_case_id"), table_name="outcome_stage_states")
    op.drop_index(op.f("ix_outcome_stage_states_milestone"), table_name="outcome_stage_states")
    op.drop_table("outcome_stage_states")

    op.drop_index(op.f("ix_outcome_events_service_case_id"), table_name="outcome_events")
    op.drop_index(op.f("ix_outcome_events_milestone"), table_name="outcome_events")
    op.drop_index(op.f("ix_outcome_events_message_id"), table_name="outcome_events")
    op.drop_index(op.f("ix_outcome_events_field_key"), table_name="outcome_events")
    op.drop_index("ix_outcome_events_field", table_name="outcome_events")
    op.drop_index("ix_outcome_events_case_milestone", table_name="outcome_events")
    op.drop_table("outcome_events")
