"""add SENDING status and sent_at to messages (record-first outbound)

Revision ID: 20260702_0029
Revises: 20260702_0028
Create Date: 2026-07-02
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260702_0029"
down_revision = "20260702_0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # DB 存的是大写成员名（见 20260522_0017 的大小写修复），故 ADD VALUE 用 'SENDING'。
    op.execute("ALTER TYPE message_status ADD VALUE IF NOT EXISTS 'SENDING'")
    op.add_column(
        "messages",
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("messages", "sent_at")
    # 收敛残留的 SENDING 记录，再重建不含 SENDING 的枚举类型。
    op.execute(
        """
        DO $$
        BEGIN
            UPDATE messages
            SET status = 'FAILED'
            WHERE status = 'SENDING';

            IF EXISTS (
                SELECT 1
                FROM pg_type
                WHERE typname = 'message_status'
            ) THEN
                ALTER TYPE message_status RENAME TO message_status_old;
                CREATE TYPE message_status AS ENUM (
                    'RECEIVED',
                    'PENDING',
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
