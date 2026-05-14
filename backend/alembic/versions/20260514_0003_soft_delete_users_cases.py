"""soft delete users and service cases

Revision ID: 20260514_0003
Revises: 20260513_0002
Create Date: 2026-05-14
"""

from alembic import op
import sqlalchemy as sa

revision = "20260514_0003"
down_revision = "20260513_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("service_cases", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))

    op.drop_constraint("users_phone_key", "users", type_="unique")
    op.drop_constraint("users_wecom_user_id_key", "users", type_="unique")
    op.drop_constraint("service_cases_group_id_key", "service_cases", type_="unique")

    op.create_index(
        "ux_users_phone_active",
        "users",
        ["phone"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL AND phone IS NOT NULL"),
    )
    op.create_index(
        "ux_users_wecom_user_id_active",
        "users",
        ["wecom_user_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL AND wecom_user_id IS NOT NULL"),
    )
    op.create_index(
        "ux_service_cases_group_id_active",
        "service_cases",
        ["group_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ux_service_cases_group_id_active", table_name="service_cases")
    op.drop_index("ux_users_wecom_user_id_active", table_name="users")
    op.drop_index("ux_users_phone_active", table_name="users")

    op.create_unique_constraint("service_cases_group_id_key", "service_cases", ["group_id"])
    op.create_unique_constraint("users_wecom_user_id_key", "users", ["wecom_user_id"])
    op.create_unique_constraint("users_phone_key", "users", ["phone"])

    op.drop_column("service_cases", "deleted_at")
    op.drop_column("users", "deleted_at")
