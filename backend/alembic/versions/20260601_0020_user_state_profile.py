"""add user state profile table

Revision ID: 20260601_0020
Revises: 20260529_0019
Create Date: 2026-06-01
"""

from alembic import op
import sqlalchemy as sa


revision = "20260601_0020"
down_revision = "20260529_0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_state_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("service_case_id", sa.Integer(), nullable=False),
        sa.Column("state_json", sa.JSON(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["service_case_id"], ["service_cases.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("service_case_id", name="ux_user_state_profiles_service_case_id"),
    )
    op.create_index(
        op.f("ix_user_state_profiles_service_case_id"),
        "user_state_profiles",
        ["service_case_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_user_state_profiles_service_case_id"), table_name="user_state_profiles")
    op.drop_table("user_state_profiles")
