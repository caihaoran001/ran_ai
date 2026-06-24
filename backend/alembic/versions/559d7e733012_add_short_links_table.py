"""add short_links table

Revision ID: 559d7e733012
Revises: c6d263aded7d
Create Date: 2026-06-24 15:35:59.543546
"""

from alembic import op
import sqlalchemy as sa


revision = '559d7e733012'
down_revision = 'c6d263aded7d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "short_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("service_case_id", sa.Integer(), sa.ForeignKey("service_cases.id"), nullable=True),
        sa.Column("template_key", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("target_path", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_short_links_code", "short_links", ["code"], unique=True)
    op.create_index("ix_short_links_service_case_id", "short_links", ["service_case_id"])
    op.create_index("ix_short_links_case_template", "short_links", ["service_case_id", "template_key"])


def downgrade() -> None:
    op.drop_index("ix_short_links_case_template", table_name="short_links")
    op.drop_index("ix_short_links_service_case_id", table_name="short_links")
    op.drop_index("ix_short_links_code", table_name="short_links")
    op.drop_table("short_links")
