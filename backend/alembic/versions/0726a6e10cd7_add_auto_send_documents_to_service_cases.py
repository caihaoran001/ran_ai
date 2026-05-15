"""add auto_send_documents to service_cases

Revision ID: 0726a6e10cd7
Revises: 20260514_0003
Create Date: 2026-05-14 18:47:26.090918
"""

from alembic import op
import sqlalchemy as sa


revision = '0726a6e10cd7'
down_revision = '20260514_0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('service_cases', sa.Column('auto_send_documents', sa.Boolean(), nullable=False, server_default='false'))


def downgrade() -> None:
    op.drop_column('service_cases', 'auto_send_documents')
