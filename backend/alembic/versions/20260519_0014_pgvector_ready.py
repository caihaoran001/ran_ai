"""pgvector ready optional columns

Revision ID: 20260519_0014
Revises: 20260519_0013
Create Date: 2026-05-19
"""

from alembic import op


revision = "20260519_0014"
down_revision = "20260519_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'vector') THEN
                CREATE EXTENSION IF NOT EXISTS vector;
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'knowledge_chunks' AND column_name = 'embedding_vector'
                ) THEN
                    ALTER TABLE knowledge_chunks ADD COLUMN embedding_vector vector(1024);
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM pg_indexes WHERE indexname = 'ix_knowledge_chunks_embedding_vector_hnsw'
                ) THEN
                    CREATE INDEX ix_knowledge_chunks_embedding_vector_hnsw
                    ON knowledge_chunks USING hnsw (embedding_vector vector_cosine_ops);
                END IF;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'knowledge_chunks' AND column_name = 'embedding_vector'
            ) THEN
                DROP INDEX IF EXISTS ix_knowledge_chunks_embedding_vector_hnsw;
                ALTER TABLE knowledge_chunks DROP COLUMN embedding_vector;
            END IF;
        END
        $$;
        """
    )
