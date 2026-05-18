"""add intervention and ai reply review fields

Revision ID: 20260515_0008
Revises: 20260515_0007
Create Date: 2026-05-15
"""

from alembic import op
import sqlalchemy as sa

revision = "20260515_0008"
down_revision = "20260515_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("analysis_json", sa.JSON(), nullable=True))
    op.add_column("service_cases", sa.Column("ai_auto_reply_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("service_cases", sa.Column("needs_human_intervention", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("service_cases", sa.Column("intervention_reasons", sa.JSON(), nullable=True))
    op.add_column("service_cases", sa.Column("renewal_intent_flags", sa.JSON(), nullable=True))
    op.add_column("service_cases", sa.Column("refund_intent_flags", sa.JSON(), nullable=True))
    op.create_index("ix_service_cases_needs_human_intervention", "service_cases", ["needs_human_intervention"])


def downgrade() -> None:
    op.drop_index("ix_service_cases_needs_human_intervention", table_name="service_cases")
    op.drop_column("service_cases", "refund_intent_flags")
    op.drop_column("service_cases", "renewal_intent_flags")
    op.drop_column("service_cases", "intervention_reasons")
    op.drop_column("service_cases", "needs_human_intervention")
    op.drop_column("service_cases", "ai_auto_reply_enabled")
    op.drop_column("messages", "analysis_json")
