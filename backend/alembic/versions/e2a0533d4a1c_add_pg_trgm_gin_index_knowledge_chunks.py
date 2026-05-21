"""add pg_trgm extension and GIN index for keyword fallback retrieval

Revision ID: e2a0533d4a1c
Revises: 20260519_0014
Create Date: 2026-05-19
"""

from alembic import op

revision = "e2a0533d4a1c"
down_revision = "20260519_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Enable pg_trgm (available in the pgvector/pgvector:pg15 image by default)
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    # GIN trigram index on content — allows ILIKE '%term%' queries to hit the index
    # instead of doing a full sequential scan.  Works well for CJK text: every 3+
    # consecutive characters form a trigram that the index can search.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_content_trgm "
        "ON knowledge_chunks USING GIN (content gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_content_trgm")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
