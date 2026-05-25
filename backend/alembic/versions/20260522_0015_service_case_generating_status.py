"""add generating status to service_case_status enum

Revision ID: 20260522_0015
Revises: 20260519_0014
Create Date: 2026-05-22
"""

from alembic import op


revision = "20260522_0015"
down_revision = "20260519_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE service_case_status ADD VALUE IF NOT EXISTS 'generating'")


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            UPDATE service_cases
            SET status = 'questionnaire_submitted'
            WHERE status = 'generating';

            IF EXISTS (
                SELECT 1
                FROM pg_type
                WHERE typname = 'service_case_status'
            ) THEN
                ALTER TYPE service_case_status RENAME TO service_case_status_old;
                CREATE TYPE service_case_status AS ENUM (
                    'created',
                    'questionnaire_submitted',
                    'documents_generated',
                    'reviewing',
                    'approved',
                    'in_service',
                    'completed'
                );
                ALTER TABLE service_cases
                    ALTER COLUMN status TYPE service_case_status
                    USING status::text::service_case_status;
                DROP TYPE service_case_status_old;
            END IF;
        END
        $$;
        """
    )
