"""allow multiple checkins per day

Revision ID: 20260518_0010
Revises: 20260518_0009
Create Date: 2026-05-18
"""

from alembic import op

revision = "20260518_0010"
down_revision = "20260518_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_checkin_case_date", "checkins", type_="unique")


def downgrade() -> None:
    op.create_unique_constraint(
        "uq_checkin_case_date",
        "checkins",
        ["service_case_id", "checkin_date"],
    )
