"""add UNKNOWN outbound message status

Revision ID: 20260724_0057
Revises: 20260723_0056
Create Date: 2026-07-24
"""

from __future__ import annotations

from alembic import op


revision = "20260724_0057"
down_revision = "20260723_0056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLAlchemy Enum persists member names in this project, hence uppercase.
    op.execute("ALTER TYPE message_status ADD VALUE IF NOT EXISTS 'UNKNOWN'")


def downgrade() -> None:
    # PostgreSQL cannot remove one enum value in place. First collapse UNKNOWN
    # rows to the old conservative terminal state, then rebuild the enum.
    op.execute(
        """
        DO $$
        BEGIN
            UPDATE messages
            SET status = 'FAILED'
            WHERE status = 'UNKNOWN';

            IF EXISTS (
                SELECT 1
                FROM pg_type
                WHERE typname = 'message_status'
            ) THEN
                ALTER TYPE message_status RENAME TO message_status_old;
                CREATE TYPE message_status AS ENUM (
                    'RECEIVED',
                    'PENDING',
                    'SENDING',
                    'SENT',
                    'FAILED'
                );
                ALTER TABLE messages
                    ALTER COLUMN status TYPE message_status
                    USING status::text::message_status;
                DROP TYPE message_status_old;
            END IF;
        END
        $$;
        """
    )
