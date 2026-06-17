"""add service_plan_start_date to service_cases

Revision ID: ee271fedf8d0
Revises: 20260615_0002
Create Date: 2026-06-17 11:44:44.277222
"""

from alembic import op
import sqlalchemy as sa


revision = 'ee271fedf8d0'
down_revision = '20260615_0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 仅新增 service_plan_start_date 列；autogenerate 误检的 knowledge_* 索引漂移已手动剔除，不在本迁移内。
    op.add_column('service_cases', sa.Column('service_plan_start_date', sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column('service_cases', 'service_plan_start_date')
