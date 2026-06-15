"""add archived to document_status

Revision ID: 20260615_0001
Revises: 20260604_0023
Create Date: 2026-06-15
"""

from alembic import op


revision = '20260615_0001'
down_revision = '20260604_0023'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ADD VALUE 在旧版 PostgreSQL 不能在事务块内执行，用 autocommit_block 兼容所有版本
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE document_status ADD VALUE IF NOT EXISTS 'archived'")


def downgrade() -> None:
    # PostgreSQL 不支持从 enum 中移除值，降级为 no-op
    pass
