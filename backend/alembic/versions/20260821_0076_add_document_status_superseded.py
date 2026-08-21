"""user_documents.document_status 枚举新增 superseded

Revision ID: 20260821_0076
Revises: 20260819_0075
Create Date: 2026-08-21

背景：重新生成交付文件时，旧版「草稿/待审核」文件应被系统自动替代，而不是继续在
待审队列里排队。为它们新增一个终态 superseded（被更新版本系统自动替代，非人工决定）。

PG15 允许在事务内 ALTER TYPE ... ADD VALUE（本迁移不使用该值，无后续使用限制）。
"""

from alembic import op


revision = "20260821_0076"
down_revision = "20260819_0075"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 注意：SQLAlchemy Enum 默认按成员名（大写）落库，如 'REVIEWING'/'SENT'，
    # API 序列化才是小写 value。这里必须加 'SUPERSEDED'，加小写会导致写入报错。
    op.execute(
        "ALTER TYPE document_status ADD VALUE IF NOT EXISTS 'SUPERSEDED'"
    )


def downgrade() -> None:
    # PostgreSQL 枚举值一旦被使用就不能直接删除；即使未被使用，也要先删列再重建类型，
    # 风险与收益不成比例。回滚时保留该枚举值，不做破坏性操作。
    pass
