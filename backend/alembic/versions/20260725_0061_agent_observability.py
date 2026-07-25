"""add unified agent observability ledger

Revision ID: 20260725_0061
Revises: 20260725_0060
Create Date: 2026-07-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260725_0061"
down_revision = "20260725_0060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_key", sa.String(length=180), nullable=False),
        sa.Column("agent_type", sa.String(length=32), nullable=False),
        sa.Column("run_kind", sa.String(length=40), nullable=False),
        sa.Column("service_case_id", sa.Integer(), nullable=True),
        sa.Column("trigger_message_id", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=24),
            server_default="running",
            nullable=False,
        ),
        sa.Column("outcome", sa.String(length=64), nullable=True),
        sa.Column("trigger_reason", sa.String(length=80), nullable=True),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("model_calls", sa.Integer(), server_default="0", nullable=False),
        sa.Column("tool_calls", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "successful_tool_calls",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("prompt_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "completion_tokens", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("total_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "estimated_cost_yuan",
            sa.Float(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("input_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("output_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("dropped_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error_type", sa.String(length=120), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column(
            "is_diagnostic",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("diagnostic_run_id", sa.String(length=100), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["service_case_id"], ["service_cases.id"]),
        sa.ForeignKeyConstraint(["trigger_message_id"], ["messages.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_key"),
    )
    for name, columns in (
        ("ix_agent_runs_run_key", ["run_key"]),
        ("ix_agent_runs_agent_type", ["agent_type"]),
        ("ix_agent_runs_run_kind", ["run_kind"]),
        ("ix_agent_runs_service_case_id", ["service_case_id"]),
        ("ix_agent_runs_trigger_message_id", ["trigger_message_id"]),
        ("ix_agent_runs_status", ["status"]),
        ("ix_agent_runs_outcome", ["outcome"]),
        ("ix_agent_runs_is_diagnostic", ["is_diagnostic"]),
        ("ix_agent_runs_diagnostic_run_id", ["diagnostic_run_id"]),
        ("ix_agent_runs_type_created", ["agent_type", "created_at"]),
        ("ix_agent_runs_kind_created", ["run_kind", "created_at"]),
        ("ix_agent_runs_status_created", ["status", "created_at"]),
        ("ix_agent_runs_outcome_created", ["outcome", "created_at"]),
    ):
        op.create_index(name, "agent_runs", columns, unique=False)

    op.create_table(
        "agent_tool_calls",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("agent_run_id", sa.Integer(), nullable=False),
        sa.Column("tool_call_id", sa.String(length=160), nullable=False),
        sa.Column("tool_name", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("elapsed_ms", sa.Integer(), nullable=True),
        sa.Column("error_type", sa.String(length=120), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "agent_run_id",
            "tool_call_id",
            name="ux_agent_tool_calls_run_call",
        ),
    )
    for name, columns in (
        ("ix_agent_tool_calls_agent_run_id", ["agent_run_id"]),
        ("ix_agent_tool_calls_tool_name", ["tool_name"]),
        ("ix_agent_tool_calls_status", ["status"]),
        ("ix_agent_tool_calls_name_created", ["tool_name", "created_at"]),
        ("ix_agent_tool_calls_status_created", ["status", "created_at"]),
    ):
        op.create_index(name, "agent_tool_calls", columns, unique=False)

    op.add_column(
        "ai_usage_logs",
        sa.Column("agent_run_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_ai_usage_logs_agent_run_id",
        "ai_usage_logs",
        "agent_runs",
        ["agent_run_id"],
        ["id"],
    )
    op.create_index(
        "ix_ai_usage_logs_agent_run_id",
        "ai_usage_logs",
        ["agent_run_id"],
        unique=False,
    )

    op.add_column(
        "archivist_extract_traces",
        sa.Column("agent_run_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_archivist_extract_traces_agent_run_id",
        "archivist_extract_traces",
        "agent_runs",
        ["agent_run_id"],
        ["id"],
    )
    op.create_index(
        "ix_archivist_extract_traces_agent_run_id",
        "archivist_extract_traces",
        ["agent_run_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_archivist_extract_traces_agent_run_id",
        table_name="archivist_extract_traces",
    )
    op.drop_constraint(
        "fk_archivist_extract_traces_agent_run_id",
        "archivist_extract_traces",
        type_="foreignkey",
    )
    op.drop_column("archivist_extract_traces", "agent_run_id")

    op.drop_index("ix_ai_usage_logs_agent_run_id", table_name="ai_usage_logs")
    op.drop_constraint(
        "fk_ai_usage_logs_agent_run_id",
        "ai_usage_logs",
        type_="foreignkey",
    )
    op.drop_column("ai_usage_logs", "agent_run_id")

    op.drop_table("agent_tool_calls")
    op.drop_table("agent_runs")
