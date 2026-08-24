"""knowledge_faqs 新增 FAQ 确认状态两列 last_confirmed_version / last_confirmed_at

Revision ID: 20260824_0001
Revises: 20260821_0076
Create Date: 2026-08-24
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260824_0001"
down_revision = "20260821_0076"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "knowledge_faqs",
        sa.Column("last_confirmed_version", sa.Integer(), nullable=True),
    )
    op.add_column(
        "knowledge_faqs",
        sa.Column("last_confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("knowledge_faqs", "last_confirmed_at")
    op.drop_column("knowledge_faqs", "last_confirmed_version")
