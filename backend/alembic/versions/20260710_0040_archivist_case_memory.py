"""archivist_case_memory table (rolling summary + recent topics)

Revision ID: 20260710_0040
Revises: 20260710_0039
Create Date: 2026-07-10
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260710_0040"
down_revision = "20260710_0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 档案官逐服务单的自由文本记忆：滚动小结(LLM 浓缩)+ 近期话题(确定性维护)。
    # 与 user_state_profiles 分开——这是记忆产物、可再生，非权威事实源。
    op.create_table(
        "archivist_case_memory",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "service_case_id",
            sa.Integer(),
            sa.ForeignKey("service_cases.id"),
            nullable=False,
        ),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("recent_topics", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("summary_watermark_message_id", sa.Integer(), nullable=True),
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
        "ix_archivist_case_memory_service_case_id",
        "archivist_case_memory",
        ["service_case_id"],
    )
    op.create_unique_constraint(
        "ux_archivist_case_memory_service_case",
        "archivist_case_memory",
        ["service_case_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "ux_archivist_case_memory_service_case",
        "archivist_case_memory",
        type_="unique",
    )
    op.drop_index(
        "ix_archivist_case_memory_service_case_id",
        table_name="archivist_case_memory",
    )
    op.drop_table("archivist_case_memory")
