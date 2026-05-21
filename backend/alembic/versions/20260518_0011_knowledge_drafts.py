"""add knowledge drafts

Revision ID: 20260518_0011
Revises: 20260518_0010
Create Date: 2026-05-18
"""

from alembic import op
import sqlalchemy as sa

revision = "20260518_0011"
down_revision = "20260518_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_drafts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("draft_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("sanitized_text", sa.Text(), nullable=True),
        sa.Column("source_message_ids", sa.JSON(), nullable=True),
        sa.Column("content_json", sa.JSON(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("published_to", sa.String(length=200), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("content_hash", name="uq_knowledge_drafts_content_hash"),
    )
    op.create_index("ix_knowledge_drafts_draft_type", "knowledge_drafts", ["draft_type"])
    op.create_index("ix_knowledge_drafts_status", "knowledge_drafts", ["status"])


def downgrade() -> None:
    op.drop_index("ix_knowledge_drafts_status", table_name="knowledge_drafts")
    op.drop_index("ix_knowledge_drafts_draft_type", table_name="knowledge_drafts")
    op.drop_table("knowledge_drafts")
