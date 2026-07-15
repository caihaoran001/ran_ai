"""archivist_extract_traces table (extraction observability)

Revision ID: 20260715_0045
Revises: 20260714_0044
Create Date: 2026-07-15
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260715_0045"
down_revision = "20260714_0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 档案官抽取观测留痕:一次抽取(某服务单某自然日)记一行。
    # 记 LLM 原始 tool_calls(emitted)+ 过滤后存活(kept)+ 被丢弃及原因(dropped),
    # 用于事后精确复盘"上报了却没抽进结构化"的漏。纯观测产物。
    op.create_table(
        "archivist_extract_traces",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "service_case_id",
            sa.Integer(),
            sa.ForeignKey("service_cases.id"),
            nullable=False,
        ),
        sa.Column("target_date", sa.Date(), nullable=True),
        sa.Column("watermark_to", sa.Integer(), nullable=True),
        sa.Column("emitted_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("kept_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("dropped_json", sa.JSON(), nullable=False, server_default="[]"),
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
        "ix_archivist_extract_traces_service_case_id",
        "archivist_extract_traces",
        ["service_case_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_archivist_extract_traces_service_case_id",
        table_name="archivist_extract_traces",
    )
    op.drop_table("archivist_extract_traces")
