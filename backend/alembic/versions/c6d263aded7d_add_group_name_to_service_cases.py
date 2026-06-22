"""add group_name to service_cases

Revision ID: c6d263aded7d
Revises: ee271fedf8d0
Create Date: 2026-06-22 12:02:13.166731
"""

from alembic import op
import sqlalchemy as sa


revision = 'c6d263aded7d'
down_revision = 'ee271fedf8d0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("service_cases", sa.Column("group_name", sa.String(length=128), nullable=True))


def downgrade() -> None:
    op.drop_column("service_cases", "group_name")
