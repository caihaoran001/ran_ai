"""make service case users independent

Revision ID: 20260515_0004
Revises: 20260514_0003
Create Date: 2026-05-15
"""

from alembic import op
import sqlalchemy as sa

revision = "20260515_0004"
down_revision = "0726a6e10cd7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ux_users_phone_active", table_name="users")
    op.drop_index("ux_users_wecom_user_id_active", table_name="users")
    op.create_index("ix_users_phone", "users", ["phone"])
    op.create_index("ix_users_wecom_user_id", "users", ["wecom_user_id"])


def downgrade() -> None:
    op.drop_index("ix_users_wecom_user_id", table_name="users")
    op.drop_index("ix_users_phone", table_name="users")
    op.create_index(
        "ux_users_wecom_user_id_active",
        "users",
        ["wecom_user_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL AND wecom_user_id IS NOT NULL"),
    )
    op.create_index(
        "ux_users_phone_active",
        "users",
        ["phone"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL AND phone IS NOT NULL"),
    )
