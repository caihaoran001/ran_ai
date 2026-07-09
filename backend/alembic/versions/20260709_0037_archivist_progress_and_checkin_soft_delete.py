"""archivist_progress table + checkin soft-delete & per-day unique index

Revision ID: 20260709_0037
Revises: 20260708_0036
Create Date: 2026-07-09
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260709_0037"
down_revision = "20260708_0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) 档案官进度/水位线表（独立表，不给 service_cases 加列）
    op.create_table(
        "archivist_progress",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "service_case_id",
            sa.Integer(),
            sa.ForeignKey("service_cases.id"),
            nullable=False,
        ),
        sa.Column("last_archived_message_id", sa.Integer(), nullable=True),
        sa.Column("last_archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_archivist_progress_service_case_id",
        "archivist_progress",
        ["service_case_id"],
    )
    op.create_index(
        "ux_archivist_progress_service_case",
        "archivist_progress",
        ["service_case_id"],
        unique=True,
    )

    # 2) checkins 软删除 + 按天条件唯一索引（支撑档案官按天 UPSERT）
    op.add_column(
        "checkins",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    # 现有表单打卡是无脑 INSERT、同一天可能多行；建唯一索引前先把每个
    # (service_case_id, checkin_date) 的重复行软删除，只保留 id 最大（最新）的一条。
    # 用软删除而非物理删，历史数据可恢复（遵守绝不硬删铁律）。
    op.execute(
        sa.text(
            """
            UPDATE checkins c
            SET deleted_at = now()
            WHERE c.deleted_at IS NULL
              AND c.id < (
                SELECT MAX(c2.id) FROM checkins c2
                WHERE c2.service_case_id = c.service_case_id
                  AND c2.checkin_date = c.checkin_date
                  AND c2.deleted_at IS NULL
              )
            """
        )
    )
    op.create_index(
        "ux_checkins_case_date_active",
        "checkins",
        ["service_case_id", "checkin_date"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ux_checkins_case_date_active", table_name="checkins")
    op.drop_column("checkins", "deleted_at")

    op.drop_index("ux_archivist_progress_service_case", table_name="archivist_progress")
    op.drop_index("ix_archivist_progress_service_case_id", table_name="archivist_progress")
    op.drop_table("archivist_progress")
