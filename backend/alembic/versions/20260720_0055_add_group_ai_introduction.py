"""add durable group AI introduction state

Revision ID: 20260720_0055
Revises: 20260720_0054
Create Date: 2026-07-20
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260720_0055"
down_revision = "20260720_0054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "service_cases",
        sa.Column("ai_introduced_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "service_cases",
        sa.Column(
            "ai_introduced_by_wecom_user_id",
            sa.String(length=128),
            nullable=True,
        ),
    )
    op.add_column(
        "service_cases",
        sa.Column("ai_introduction_trigger_message_id", sa.Integer(), nullable=True),
    )

    # 存量群的机器人已在正常说话，上线时不得突然把它们全部关回
    # “待引荐”。新规则只作用于迁移之后由群消息自动创建的服务单。
    op.execute(
        """
        UPDATE service_cases
        SET ai_introduced_at = COALESCE(created_at, CURRENT_TIMESTAMP),
            ai_introduced_by_wecom_user_id = 'migration:existing-case'
        WHERE deleted_at IS NULL
          AND ai_introduced_at IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column("service_cases", "ai_introduction_trigger_message_id")
    op.drop_column("service_cases", "ai_introduced_by_wecom_user_id")
    op.drop_column("service_cases", "ai_introduced_at")
