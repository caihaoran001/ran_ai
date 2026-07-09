"""archivist_shadow_outcomes table (P0.5 outcome/profile shadow reconciliation)

Revision ID: 20260709_0038
Revises: 20260709_0037
Create Date: 2026-07-09
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260709_0038"
down_revision = "20260709_0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 影子对账审计表：档案官批量抽的成效/画像 vs 热路径已写 outcome_events 的差异留痕。
    op.create_table(
        "archivist_shadow_outcomes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "service_case_id",
            sa.Integer(),
            sa.ForeignKey("service_cases.id"),
            nullable=False,
        ),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("watermark_from", sa.Integer(), nullable=True),
        sa.Column("watermark_to", sa.Integer(), nullable=True),
        sa.Column("shadow_events", sa.JSON(), nullable=True),
        sa.Column("shadow_signals", sa.JSON(), nullable=True),
        sa.Column("hot_events", sa.JSON(), nullable=True),
        sa.Column("diff", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_archivist_shadow_outcomes_service_case_id",
        "archivist_shadow_outcomes",
        ["service_case_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_archivist_shadow_outcomes_service_case_id",
        table_name="archivist_shadow_outcomes",
    )
    op.drop_table("archivist_shadow_outcomes")
