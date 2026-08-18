"""add plateau_followed_up_at to service_cases

审核控制台的「体重状态跟进」待办用它清零：运营标记跟进后写入当前时间，
只要判定结论之后又变过一次（plateau_updated_at 比它新），该服务单重新回到待办。

Revision ID: 20260818_0074
Revises: 20260817_0073
Create Date: 2026-08-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260818_0074"
down_revision = "20260817_0073"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "service_cases",
        sa.Column("plateau_followed_up_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("service_cases", "plateau_followed_up_at")
