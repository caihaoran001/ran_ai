"""add structured knowledge faqs

Revision ID: 20260519_0013
Revises: 20260519_0012
Create Date: 2026-05-19
"""

from alembic import op
import sqlalchemy as sa


revision = "20260519_0013"
down_revision = "20260519_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_faqs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("faq_uid", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("source_type", sa.String(length=40), nullable=True),
        sa.Column("source_id", sa.String(length=80), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("faq_uid", name="uq_knowledge_faqs_faq_uid"),
    )
    op.create_index("ix_knowledge_faqs_faq_uid", "knowledge_faqs", ["faq_uid"])
    op.create_index("ix_knowledge_faqs_status", "knowledge_faqs", ["status"])
    op.create_table(
        "knowledge_faq_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("faq_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("change_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["faq_id"], ["knowledge_faqs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("faq_id", "version", name="uq_knowledge_faq_versions_faq_version"),
    )
    op.create_index("ix_knowledge_faq_versions_faq_id", "knowledge_faq_versions", ["faq_id"])


def downgrade() -> None:
    op.drop_index("ix_knowledge_faq_versions_faq_id", table_name="knowledge_faq_versions")
    op.drop_table("knowledge_faq_versions")
    op.drop_index("ix_knowledge_faqs_status", table_name="knowledge_faqs")
    op.drop_index("ix_knowledge_faqs_faq_uid", table_name="knowledge_faqs")
    op.drop_table("knowledge_faqs")
