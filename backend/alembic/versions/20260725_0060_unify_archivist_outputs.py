"""unify archivist outputs in the main transaction

Revision ID: 20260725_0060
Revises: 20260724_0059
Create Date: 2026-07-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260725_0060"
down_revision = "20260724_0059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "archivist_case_memory",
        sa.Column("case_material_summary", sa.Text(), nullable=True),
    )
    op.drop_table("archivist_post_commit_tasks")


def downgrade() -> None:
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
    op.drop_column("archivist_case_memory", "case_material_summary")
