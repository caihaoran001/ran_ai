"""preserve immutable original delivery-document snapshots

Revision ID: 20260803_0068
Revises: 20260731_0067
Create Date: 2026-08-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260803_0068"
down_revision = "20260731_0067"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_document_originals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("content_md", sa.Text(), nullable=False),
        sa.Column("content_json", sa.JSON(), nullable=True),
        sa.Column("generated_by_model", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["user_documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id"),
    )
    op.create_index(
        "ix_user_document_originals_document_id",
        "user_document_originals",
        ["document_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_user_document_originals_document_id", table_name="user_document_originals")
    op.drop_table("user_document_originals")
