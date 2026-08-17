"""group_type 默认值改为 new_group（老群集合就地封存，不回填存量）

Revision ID: 20260817_0073
Revises: 20260814_0072
Create Date: 2026-08-17

新老群从「运行时识别」改为「静态标记」：新建服务单默认就是新客户，老群是一个
已经封存的历史集合，因此**只改默认值、绝不动存量行**。若这里顺手把 legacy 也刷成
new_group，179 张停在 CREATED 的老群空单子会立刻涌进看板统计，把新客户数、人均成效
和人均成本全部稀释。
"""

from __future__ import annotations

from alembic import op


revision = "20260817_0073"
down_revision = "20260814_0072"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE service_cases ALTER COLUMN group_type SET DEFAULT 'new_group'")


def downgrade() -> None:
    op.execute("ALTER TABLE service_cases ALTER COLUMN group_type SET DEFAULT 'legacy'")
