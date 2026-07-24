"""archivist durable post-commit tasks and quarantine replay

Revision ID: 20260724_0059
Revises: 20260724_0058
Create Date: 2026-07-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260724_0059"
down_revision = "20260724_0058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "archivist_progress",
        sa.Column("last_failure_kind", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "archivist_progress",
        sa.Column("last_failure_fingerprint", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "archivist_progress",
        sa.Column(
            "quarantine_candidate_failures",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "archivist_progress",
        sa.Column(
            "quarantine_candidate_fingerprint",
            sa.String(length=64),
            nullable=True,
        ),
    )

    op.create_table(
        "archivist_post_commit_tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("service_case_id", sa.Integer(), nullable=False),
        sa.Column("watermark_to", sa.Integer(), nullable=False),
        sa.Column("task_type", sa.String(length=32), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["service_case_id"], ["service_cases.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "service_case_id",
            "watermark_to",
            "task_type",
            name="ux_archivist_post_task_case_watermark_type",
        ),
    )
    op.create_index(
        "ix_archivist_post_commit_tasks_service_case_id",
        "archivist_post_commit_tasks",
        ["service_case_id"],
        unique=False,
    )
    op.create_index(
        "ix_archivist_post_commit_tasks_status",
        "archivist_post_commit_tasks",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_archivist_post_tasks_status_next_attempt",
        "archivist_post_commit_tasks",
        ["status", "next_attempt_at"],
        unique=False,
    )

    op.create_table(
        "archivist_quarantines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("service_case_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("failure_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=False),
        sa.Column("quarantined_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("replay_attempts", sa.Integer(), nullable=False),
        sa.Column("replayed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_replay_error", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"]),
        sa.ForeignKeyConstraint(["service_case_id"], ["service_cases.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_archivist_quarantines_message_id",
        "archivist_quarantines",
        ["message_id"],
        unique=False,
    )
    op.create_index(
        "ix_archivist_quarantines_service_case_id",
        "archivist_quarantines",
        ["service_case_id"],
        unique=False,
    )
    op.create_index(
        "ix_archivist_quarantines_status",
        "archivist_quarantines",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_archivist_quarantines_status_quarantined_at",
        "archivist_quarantines",
        ["status", "quarantined_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_archivist_quarantines_status_quarantined_at",
        table_name="archivist_quarantines",
    )
    op.drop_index("ix_archivist_quarantines_status", table_name="archivist_quarantines")
    op.drop_index(
        "ix_archivist_quarantines_service_case_id",
        table_name="archivist_quarantines",
    )
    op.drop_index(
        "ix_archivist_quarantines_message_id",
        table_name="archivist_quarantines",
    )
    op.drop_table("archivist_quarantines")

    op.drop_index(
        "ix_archivist_post_tasks_status_next_attempt",
        table_name="archivist_post_commit_tasks",
    )
    op.drop_index(
        "ix_archivist_post_commit_tasks_status",
        table_name="archivist_post_commit_tasks",
    )
    op.drop_index(
        "ix_archivist_post_commit_tasks_service_case_id",
        table_name="archivist_post_commit_tasks",
    )
    op.drop_table("archivist_post_commit_tasks")

    op.drop_column("archivist_progress", "quarantine_candidate_fingerprint")
    op.drop_column("archivist_progress", "quarantine_candidate_failures")
    op.drop_column("archivist_progress", "last_failure_fingerprint")
    op.drop_column("archivist_progress", "last_failure_kind")
