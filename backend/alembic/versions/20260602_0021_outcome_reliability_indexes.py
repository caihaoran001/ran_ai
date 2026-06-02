"""tighten outcome reliability indexes

Revision ID: 20260602_0021
Revises: 20260601_0020
Create Date: 2026-06-02
"""

from alembic import op


revision = "20260602_0021"
down_revision = "20260601_0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM outcome_events newer
        USING outcome_events older
        WHERE newer.id > older.id
          AND newer.service_case_id = older.service_case_id
          AND newer.message_id = older.message_id
          AND newer.field_key = older.field_key
          AND newer.message_id IS NOT NULL
        """
    )
    op.create_unique_constraint(
        "ux_outcome_events_dedup",
        "outcome_events",
        ["service_case_id", "message_id", "field_key"],
    )
    op.execute("DROP INDEX IF EXISTS ix_outcome_events_field")
    op.execute("DROP INDEX IF EXISTS ix_outcome_events_service_case_id")
    op.execute("DROP INDEX IF EXISTS ix_outcome_stage_states_service_case_id")
    op.execute("DROP INDEX IF EXISTS ix_outcome_snapshots_service_case_id")
    op.execute("DROP INDEX IF EXISTS ix_user_state_profiles_service_case_id")


def downgrade() -> None:
    op.create_index("ix_user_state_profiles_service_case_id", "user_state_profiles", ["service_case_id"], unique=False)
    op.create_index("ix_outcome_snapshots_service_case_id", "outcome_snapshots", ["service_case_id"], unique=False)
    op.create_index("ix_outcome_stage_states_service_case_id", "outcome_stage_states", ["service_case_id"], unique=False)
    op.create_index("ix_outcome_events_service_case_id", "outcome_events", ["service_case_id"], unique=False)
    op.create_index("ix_outcome_events_field", "outcome_events", ["field_key"], unique=False)
    op.drop_constraint("ux_outcome_events_dedup", "outcome_events", type_="unique")
