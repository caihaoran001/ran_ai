"""add execution commitment state to service cases

Revision ID: 20260901_0001
Revises: 20260824_0001
Create Date: 2026-09-01
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260901_0001"
down_revision: str | Sequence[str] | None = "20260824_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "service_cases",
        sa.Column("execution_commitment", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("service_cases", "execution_commitment")
