"""add SKIPPED status to agent_reminder_task_status (过点即弃)

Revision ID: 20260715_0046
Revises: 20260715_0045
Create Date: 2026-07-15
"""

from __future__ import annotations

from alembic import op


revision = "20260715_0046"
down_revision = "20260715_0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # DB 存的是大写成员名（见 20260522_0017 的大小写修复），故 ADD VALUE 用 'SKIPPED'。
    op.execute("ALTER TYPE agent_reminder_task_status ADD VALUE IF NOT EXISTS 'SKIPPED'")


def downgrade() -> None:
    # Postgres 不支持从枚举里删值：先把残留的 SKIPPED 收敛为 CANCELLED，再重建不含 SKIPPED 的枚举。
    op.execute(
        """
        DO $$
        BEGIN
            UPDATE agent_reminder_tasks
            SET status = 'CANCELLED'
            WHERE status = 'SKIPPED';

            IF EXISTS (
                SELECT 1
                FROM pg_type
                WHERE typname = 'agent_reminder_task_status'
            ) THEN
                ALTER TYPE agent_reminder_task_status RENAME TO agent_reminder_task_status_old;
                CREATE TYPE agent_reminder_task_status AS ENUM (
                    'PENDING',
                    'SENDING',
                    'SENT',
                    'FAILED',
                    'CANCELLED'
                );
                ALTER TABLE agent_reminder_tasks
                    ALTER COLUMN status TYPE agent_reminder_task_status
                    USING status::text::agent_reminder_task_status;
                DROP TYPE agent_reminder_task_status_old;
            END IF;
        END
        $$;
        """
    )
