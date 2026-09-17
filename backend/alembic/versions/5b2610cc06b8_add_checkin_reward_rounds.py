"""add checkin reward rounds

Revision ID: 5b2610cc06b8
Revises: 20260904_0001
Create Date: 2026-09-17 09:51:15.147589
"""

from alembic import op
import sqlalchemy as sa


revision = '5b2610cc06b8'
down_revision = '20260904_0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 只新增奖励轮次表；autogenerate 误检的 checkpoint/knowledge_* 等既有结构
    # 漂移已手动剔除，不在本迁移内。
    op.create_table(
        'checkin_reward_rounds',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('started_on', sa.Date(), nullable=False),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_checkin_reward_rounds_started_on'),
        'checkin_reward_rounds',
        ['started_on'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f('ix_checkin_reward_rounds_started_on'),
        table_name='checkin_reward_rounds',
    )
    op.drop_table('checkin_reward_rounds')
