"""add refunded status to service_case_status enum

Revision ID: 20260701_0025
Revises: 20260629_0024
Create Date: 2026-07-01
"""

from alembic import op


revision = "20260701_0025"
down_revision = "20260629_0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # DB 存的是大写成员名（见 20260522_0017 的大小写修复），故 ADD VALUE 用 'REFUNDED'。
    op.execute("ALTER TYPE service_case_status ADD VALUE IF NOT EXISTS 'REFUNDED'")


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            UPDATE service_cases
            SET status = 'IN_SERVICE'
            WHERE status = 'REFUNDED';

            IF EXISTS (
                SELECT 1
                FROM pg_type
                WHERE typname = 'service_case_status'
            ) THEN
                ALTER TYPE service_case_status RENAME TO service_case_status_old;
                CREATE TYPE service_case_status AS ENUM (
                    'CREATED',
                    'QUESTIONNAIRE_SUBMITTED',
                    'GENERATING',
                    'DOCUMENTS_GENERATED',
                    'REVIEWING',
                    'APPROVED',
                    'IN_SERVICE',
                    'COMPLETED'
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
