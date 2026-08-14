"""add plateau status to service_cases

Revision ID: 20260814_0072
Revises: 20260813_0071
Create Date: 2026-08-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260814_0072"
down_revision = "20260813_0071"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "service_cases",
        sa.Column("plateau_status", sa.String(length=20), nullable=False, server_default="normal"),
    )
    op.add_column("service_cases", sa.Column("plateau_evidence", sa.JSON(), nullable=True))
    op.add_column(
        "service_cases",
        sa.Column("plateau_updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_service_cases_plateau_status", "service_cases", ["plateau_status"])


def downgrade() -> None:
    op.drop_index("ix_service_cases_plateau_status", table_name="service_cases")
    op.drop_column("service_cases", "plateau_updated_at")
    op.drop_column("service_cases", "plateau_evidence")
    op.drop_column("service_cases", "plateau_status")