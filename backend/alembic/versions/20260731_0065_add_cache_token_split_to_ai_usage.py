"""add prompt cache hit/miss token split to ai_usage_logs

Revision ID: 20260731_0065
Revises: 20260731_0064
Create Date: 2026-07-31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260731_0065"
down_revision = "20260731_0064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_usage_logs",
        sa.Column("prompt_cache_hit_tokens", sa.Integer(), nullable=True),
    )
    op.add_column(
        "ai_usage_logs",
        sa.Column("prompt_cache_miss_tokens", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ai_usage_logs", "prompt_cache_miss_tokens")
    op.drop_column("ai_usage_logs", "prompt_cache_hit_tokens")
