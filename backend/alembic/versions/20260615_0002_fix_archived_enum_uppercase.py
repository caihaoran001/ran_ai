"""fix: document_status 加大写 ARCHIVED（SQLAlchemy 以成员名存库）

Revision ID: 20260615_0002
Revises: 20260615_0001
Create Date: 2026-06-15
"""

from alembic import op


revision = '20260615_0002'
down_revision = '20260615_0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLAlchemy 的 Enum 默认以成员名(大写)存库，现有值为 DRAFT/REVIEWING/APPROVED/SENT。
    # 上一迁移误加了小写 'archived'，这里补上代码实际写入用的大写 'ARCHIVED'。
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE document_status ADD VALUE IF NOT EXISTS 'ARCHIVED'")


def downgrade() -> None:
    # PostgreSQL 不支持从 enum 移除值，降级为 no-op
    pass
