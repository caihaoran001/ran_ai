"""archivist_progress failure/retry/stats fields

Revision ID: 20260710_0039
Revises: 20260709_0038
Create Date: 2026-07-10
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260710_0039"
down_revision = "20260709_0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 可靠性：归档失败时不推进水位线，改由这些字段留痕（连续失败/累计失败/最后失败原因），
    # 支持重试与运营可见。server_default 让存量行平滑补 0。
    op.add_column(
        "archivist_progress",
        sa.Column(
            "consecutive_failures",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "archivist_progress",
        sa.Column(
            "failure_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "archivist_progress",
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "archivist_progress",
        sa.Column("last_failure_reason", sa.String(length=500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("archivist_progress", "last_failure_reason")
    op.drop_column("archivist_progress", "last_failure_at")
    op.drop_column("archivist_progress", "failure_count")
    op.drop_column("archivist_progress", "consecutive_failures")
