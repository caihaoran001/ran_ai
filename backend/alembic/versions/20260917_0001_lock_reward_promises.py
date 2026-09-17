"""lock reward promises per service case

Revision ID: 20260917_0001
Revises: 5b2610cc06b8
Create Date: 2026-09-17
"""

from alembic import op
import sqlalchemy as sa


revision = "20260917_0001"
down_revision = "5b2610cc06b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "checkin_reward_programs",
        sa.Column("reward_config_snapshot", sa.JSON(), nullable=True),
    )
    op.add_column(
        "checkin_reward_programs",
        sa.Column(
            "reward_terms_locked_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("checkin_reward_programs", "reward_terms_locked_at")
    op.drop_column("checkin_reward_programs", "reward_config_snapshot")
