"""add group_type to service_cases

Revision ID: 20260813_0071
Revises: 20260812_0070
Create Date: 2026-08-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260813_0071"
down_revision = "20260812_0070"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "service_cases",
        sa.Column("group_type", sa.String(length=20), nullable=False, server_default="legacy"),
    )
    op.create_index("ix_service_cases_group_type", "service_cases", ["group_type"])
    op.execute(
        "UPDATE service_cases SET group_type = 'new_group' "
        "WHERE id IN (SELECT DISTINCT service_case_id FROM questionnaire_submissions "
        "WHERE template_key = 'intake')"
    )


def downgrade() -> None:
    op.drop_index("ix_service_cases_group_type", table_name="service_cases")
    op.drop_column("service_cases", "group_type")
