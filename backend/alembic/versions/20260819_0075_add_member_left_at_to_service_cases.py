"""服务单加「客户已退群」标记，并让一个群可以先后服务多位客户

Revision ID: 20260819_0075
Revises: 20260818_0074
Create Date: 2026-08-19

背景：运营会复用老群（改个群名、把新客户拉进来），但原来「一个群只能挂一张服务单」的
唯一索引逼着他们先去后台把旧单删掉——历史被标成已删除，忘了删则新客户的话会记进旧客户档案。
改成「一个群只能有一张客户在场的服务单」：客户本人退群即打上退群时间并让位，
新客户进来自动开新一期，旧单原样保留。
"""

from alembic import op
import sqlalchemy as sa


revision = "20260819_0075"
down_revision = "20260818_0074"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "service_cases",
        sa.Column("member_left_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.drop_index("ux_service_cases_group_id_active", table_name="service_cases")
    op.create_index(
        "ux_service_cases_group_id_active",
        "service_cases",
        ["group_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL AND member_left_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ux_service_cases_group_id_active", table_name="service_cases")
    op.create_index(
        "ux_service_cases_group_id_active",
        "service_cases",
        ["group_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_column("service_cases", "member_left_at")
