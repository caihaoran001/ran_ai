"""add knowledge dedupe audit events

Revision ID: 20260727_0062
Revises: 20260725_0061
Create Date: 2026-07-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260727_0062"
down_revision = "20260725_0061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_dedupe_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("draft_id", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("target_chunk_id", sa.Integer(), nullable=True),
        sa.Column("relation", sa.String(length=40), nullable=False),
        sa.Column("answer_relation", sa.String(length=40), nullable=True),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("resolution", sa.String(length=40), nullable=False),
        sa.Column("resolved_by", sa.String(length=24), nullable=False),
        sa.Column("distance", sa.Float(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("details_json", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["draft_id"],
            ["knowledge_drafts.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["target_chunk_id"],
            ["knowledge_chunks.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_knowledge_dedupe_events_draft_id",
        "knowledge_dedupe_events",
        ["draft_id"],
    )
    op.create_index(
        "ix_knowledge_dedupe_events_content_hash",
        "knowledge_dedupe_events",
        ["content_hash"],
    )
    op.create_index(
        "ix_knowledge_dedupe_events_target_chunk_id",
        "knowledge_dedupe_events",
        ["target_chunk_id"],
    )
    op.create_index(
        "ix_knowledge_dedupe_events_decision",
        "knowledge_dedupe_events",
        ["decision"],
    )
    op.create_index(
        "ix_knowledge_dedupe_events_resolution",
        "knowledge_dedupe_events",
        ["resolution"],
    )
    op.create_index(
        "ix_knowledge_dedupe_events_draft_created",
        "knowledge_dedupe_events",
        ["draft_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_knowledge_dedupe_events_draft_created",
        table_name="knowledge_dedupe_events",
    )
    op.drop_index(
        "ix_knowledge_dedupe_events_resolution",
        table_name="knowledge_dedupe_events",
    )
    op.drop_index(
        "ix_knowledge_dedupe_events_decision",
        table_name="knowledge_dedupe_events",
    )
    op.drop_index(
        "ix_knowledge_dedupe_events_target_chunk_id",
        table_name="knowledge_dedupe_events",
    )
    op.drop_index(
        "ix_knowledge_dedupe_events_content_hash",
        table_name="knowledge_dedupe_events",
    )
    op.drop_index(
        "ix_knowledge_dedupe_events_draft_id",
        table_name="knowledge_dedupe_events",
    )
    op.drop_table("knowledge_dedupe_events")
