"""add structured document content

Revision ID: 20260515_0005
Revises: 20260515_0004
Create Date: 2026-05-15
"""

from alembic import op
import sqlalchemy as sa

revision = "20260515_0005"
down_revision = "20260515_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_documents", sa.Column("content_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("user_documents", "content_json")
