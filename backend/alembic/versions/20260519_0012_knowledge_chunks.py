"""add knowledge chunks

Revision ID: 20260519_0012
Revises: 20260518_0011
Create Date: 2026-05-19
"""

from alembic import op
import sqlalchemy as sa


revision = "20260519_0012"
down_revision = "20260518_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("chunk_uid", sa.String(length=120), nullable=False),
        sa.Column("source_type", sa.String(length=40), nullable=False),
        sa.Column("source_uri", sa.String(length=500), nullable=False),
        sa.Column("source_title", sa.String(length=200), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("embedding", sa.JSON(), nullable=True),
        sa.Column("embedding_model", sa.String(length=100), nullable=True),
        sa.Column("embedded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chunk_uid", name="uq_knowledge_chunks_chunk_uid"),
    )
    op.create_index("ix_knowledge_chunks_chunk_uid", "knowledge_chunks", ["chunk_uid"])
    op.create_index("ix_knowledge_chunks_source_type", "knowledge_chunks", ["source_type"])
    op.create_index("ix_knowledge_chunks_content_hash", "knowledge_chunks", ["content_hash"])
    op.create_index("ix_knowledge_chunks_status", "knowledge_chunks", ["status"])


def downgrade() -> None:
    op.drop_index("ix_knowledge_chunks_status", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_content_hash", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_source_type", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_chunk_uid", table_name="knowledge_chunks")
    op.drop_table("knowledge_chunks")
