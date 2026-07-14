"""drop outcome_stage_states followup columns

成效引擎重构：reply-time 成效追问机制已整体移除（追问由 archivist/followup.py
独立负责），outcome_stage_states 的 last_followup_field/last_followup_question/
last_followup_at/followup_count 四列不再被写入或读取，随列一并移除。
collected_fields/missing_fields 保留（快照过期判断与 metrics 仍在用）。

Revision ID: 20260714_0044
Revises: 20260714_0043
Create Date: 2026-07-14
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260714_0044"
down_revision = "20260714_0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("outcome_stage_states", "last_followup_field")
    op.drop_column("outcome_stage_states", "last_followup_question")
    op.drop_column("outcome_stage_states", "last_followup_at")
    op.drop_column("outcome_stage_states", "followup_count")


def downgrade() -> None:
    op.add_column(
        "outcome_stage_states",
        sa.Column("last_followup_field", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "outcome_stage_states",
        sa.Column("last_followup_question", sa.Text(), nullable=True),
    )
    op.add_column(
        "outcome_stage_states",
        sa.Column("last_followup_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "outcome_stage_states",
        sa.Column("followup_count", sa.Integer(), nullable=False, server_default="0"),
    )
