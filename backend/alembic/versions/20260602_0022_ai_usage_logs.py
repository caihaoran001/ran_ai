"""add ai usage logs

Revision ID: 20260602_0022
Revises: 20260602_0021
Create Date: 2026-06-02
"""

from alembic import op
import sqlalchemy as sa


revision = "20260602_0022"
down_revision = "20260602_0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_usage_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("service_case_id", sa.Integer(), nullable=True),
        sa.Column("message_id", sa.Integer(), nullable=True),
        sa.Column("scenario", sa.String(length=80), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("estimated_cost_yuan", sa.Float(), nullable=True),
        sa.Column("elapsed_ms", sa.Integer(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("finish_reason", sa.String(length=60), nullable=True),
        sa.Column("error_type", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"]),
        sa.ForeignKeyConstraint(["service_case_id"], ["service_cases.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_usage_logs_message_id", "ai_usage_logs", ["message_id"], unique=False)
    op.create_index("ix_ai_usage_logs_scenario", "ai_usage_logs", ["scenario"], unique=False)
    op.create_index("ix_ai_usage_logs_scenario_created", "ai_usage_logs", ["scenario", "created_at"], unique=False)
    op.create_index("ix_ai_usage_logs_service_case_created", "ai_usage_logs", ["service_case_id", "created_at"], unique=False)
    op.create_index("ix_ai_usage_logs_service_case_id", "ai_usage_logs", ["service_case_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_ai_usage_logs_service_case_id", table_name="ai_usage_logs")
    op.drop_index("ix_ai_usage_logs_service_case_created", table_name="ai_usage_logs")
    op.drop_index("ix_ai_usage_logs_scenario_created", table_name="ai_usage_logs")
    op.drop_index("ix_ai_usage_logs_scenario", table_name="ai_usage_logs")
    op.drop_index("ix_ai_usage_logs_message_id", table_name="ai_usage_logs")
    op.drop_table("ai_usage_logs")
