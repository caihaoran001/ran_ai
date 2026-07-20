"""population insight generation snapshots

Revision ID: 20260720_0053
Revises: 20260718_0052
Create Date: 2026-07-20
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260720_0053"
down_revision = "20260718_0052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "population_insight_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scope_key", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("content_json", sa.JSON(), nullable=True),
        sa.Column("source_case_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("included_users", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("profiled_users", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("model_input_users", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("prompt_version", sa.String(length=40), nullable=False),
        sa.Column("generated_by_model", sa.String(length=128), nullable=True),
        sa.Column("refresh_reason", sa.String(length=40), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
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
    op.create_index(
        "ix_population_insight_snapshots_status",
        "population_insight_snapshots",
        ["status"],
    )
    op.create_index(
        "ix_population_insight_snapshots_scope_status_generated",
        "population_insight_snapshots",
        ["scope_key", "status", "generated_at"],
    )
    op.create_index(
        "ux_population_insight_snapshots_scope_generating",
        "population_insight_snapshots",
        ["scope_key"],
        unique=True,
        postgresql_where=sa.text("status = 'generating'"),
    )


def downgrade() -> None:
    op.drop_index(
        "ux_population_insight_snapshots_scope_generating",
        table_name="population_insight_snapshots",
    )
    op.drop_index(
        "ix_population_insight_snapshots_scope_status_generated",
        table_name="population_insight_snapshots",
    )
    op.drop_index(
        "ix_population_insight_snapshots_status",
        table_name="population_insight_snapshots",
    )
    op.drop_table("population_insight_snapshots")
