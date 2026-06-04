"""separate diagnostic ai usage from production usage

Revision ID: 20260604_0023
Revises: 20260602_0022
Create Date: 2026-06-04
"""

from alembic import op
import sqlalchemy as sa


revision = "20260604_0023"
down_revision = "20260602_0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_usage_logs",
        sa.Column("is_diagnostic", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "ai_usage_logs",
        sa.Column("diagnostic_run_id", sa.String(length=100), nullable=True),
    )
    op.create_index("ix_ai_usage_logs_is_diagnostic", "ai_usage_logs", ["is_diagnostic"], unique=False)
    op.create_index("ix_ai_usage_logs_diagnostic_run_id", "ai_usage_logs", ["diagnostic_run_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_ai_usage_logs_diagnostic_run_id", table_name="ai_usage_logs")
    op.drop_index("ix_ai_usage_logs_is_diagnostic", table_name="ai_usage_logs")
    op.drop_column("ai_usage_logs", "diagnostic_run_id")
    op.drop_column("ai_usage_logs", "is_diagnostic")
