"""add agent reminder tasks

Revision ID: 20260703_0031
Revises: 20260702_0030
Create Date: 2026-07-03
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260703_0031"
down_revision = "20260702_0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    status_enum = postgresql.ENUM(
        "PENDING",
        "SENDING",
        "SENT",
        "FAILED",
        "CANCELLED",
        name="agent_reminder_task_status",
        create_type=False,
    )
    status_enum.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "agent_reminder_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("service_case_id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.String(length=128), nullable=False),
        sa.Column("trigger_message_id", sa.Integer(), nullable=True),
        sa.Column("remind_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", status_enum, nullable=False),
        sa.Column("sent_message_id", sa.Integer(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["service_case_id"], ["service_cases.id"]),
        sa.ForeignKeyConstraint(["trigger_message_id"], ["messages.id"]),
        sa.ForeignKeyConstraint(["sent_message_id"], ["messages.id"]),
    )
    op.create_index("ix_agent_reminder_tasks_group_id", "agent_reminder_tasks", ["group_id"])
    op.create_index("ix_agent_reminder_tasks_remind_at", "agent_reminder_tasks", ["remind_at"])
    op.create_index("ix_agent_reminder_tasks_sent_message_id", "agent_reminder_tasks", ["sent_message_id"])
    op.create_index("ix_agent_reminder_tasks_service_case_id", "agent_reminder_tasks", ["service_case_id"])
    op.create_index("ix_agent_reminder_tasks_status_remind_at", "agent_reminder_tasks", ["status", "remind_at"])
    op.create_index("ix_agent_reminder_tasks_trigger_message_id", "agent_reminder_tasks", ["trigger_message_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_reminder_tasks_trigger_message_id", table_name="agent_reminder_tasks")
    op.drop_index("ix_agent_reminder_tasks_status_remind_at", table_name="agent_reminder_tasks")
    op.drop_index("ix_agent_reminder_tasks_service_case_id", table_name="agent_reminder_tasks")
    op.drop_index("ix_agent_reminder_tasks_sent_message_id", table_name="agent_reminder_tasks")
    op.drop_index("ix_agent_reminder_tasks_remind_at", table_name="agent_reminder_tasks")
    op.drop_index("ix_agent_reminder_tasks_group_id", table_name="agent_reminder_tasks")
    op.drop_table("agent_reminder_tasks")
    sa.Enum(name="agent_reminder_task_status").drop(op.get_bind(), checkfirst=True)
