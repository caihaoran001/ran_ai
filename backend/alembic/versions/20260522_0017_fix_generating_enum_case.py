"""fix generating enum label case

Revision ID: 20260522_0017
Revises: 20260522_0016
Create Date: 2026-05-22
"""

from alembic import op


revision = "20260522_0017"
down_revision = "20260522_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM pg_enum e
                JOIN pg_type t ON e.enumtypid = t.oid
                WHERE t.typname = 'service_case_status' AND e.enumlabel = 'generating'
            ) THEN
                ALTER TYPE service_case_status RENAME VALUE 'generating' TO 'GENERATING';
            ELSIF NOT EXISTS (
                SELECT 1
                FROM pg_enum e
                JOIN pg_type t ON e.enumtypid = t.oid
                WHERE t.typname = 'service_case_status' AND e.enumlabel = 'GENERATING'
            ) THEN
                ALTER TYPE service_case_status ADD VALUE 'GENERATING';
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
                SELECT 1
                FROM pg_enum e
                JOIN pg_type t ON e.enumtypid = t.oid
                WHERE t.typname = 'service_case_status' AND e.enumlabel = 'GENERATING'
            ) THEN
                ALTER TYPE service_case_status RENAME VALUE 'GENERATING' TO 'generating';
            END IF;
        END
        $$;
        """
    )
