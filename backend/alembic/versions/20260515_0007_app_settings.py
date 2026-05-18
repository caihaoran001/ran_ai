"""add app settings table

Revision ID: 20260515_0007
Revises: 20260515_0006
Create Date: 2026-05-15
"""

from alembic import op
import sqlalchemy as sa

revision = "20260515_0007"
down_revision = "20260515_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=128), primary_key=True),
        sa.Column("value_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
