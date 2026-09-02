"""add language column to service cases

Revision ID: 20260902_0001
Revises: 20260901_0001
Create Date: 2026-09-02
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260902_0001"
down_revision: str | Sequence[str] | None = "20260901_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "service_cases",
        sa.Column(
            "language",
            sa.String(length=10),
            nullable=False,
            server_default="zh",
        ),
    )


def downgrade() -> None:
    op.drop_column("service_cases", "language")
