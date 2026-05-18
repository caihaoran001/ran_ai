"""add mvp loop fields

Revision ID: 20260515_0006
Revises: 20260515_0005
Create Date: 2026-05-15
"""

from alembic import op
import sqlalchemy as sa

revision = "20260515_0006"
down_revision = "20260515_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE document_type ADD VALUE IF NOT EXISTS 'STAGE_REPORT'")
    op.execute("ALTER TYPE document_type ADD VALUE IF NOT EXISTS 'GRADUATION_REPORT'")
    op.add_column("service_cases", sa.Column("risk_level", sa.String(length=32), nullable=False, server_default="normal"))
    op.add_column("service_cases", sa.Column("risk_flags", sa.JSON(), nullable=True))
    op.create_index("ix_service_cases_risk_level", "service_cases", ["risk_level"])


def downgrade() -> None:
    op.drop_index("ix_service_cases_risk_level", table_name="service_cases")
    op.drop_column("service_cases", "risk_flags")
    op.drop_column("service_cases", "risk_level")
