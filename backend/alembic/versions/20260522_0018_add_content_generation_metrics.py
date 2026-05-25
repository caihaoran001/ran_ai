"""add content generation metrics to service_cases

Revision ID: 20260522_0018
Revises: 20260522_0017
Create Date: 2026-05-22
"""

from alembic import op
import sqlalchemy as sa


revision = "20260522_0018"
down_revision = "20260522_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("service_cases", sa.Column("content_generated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("service_cases", sa.Column("questionnaire_to_llm_output_ms", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("service_cases", "questionnaire_to_llm_output_ms")
    op.drop_column("service_cases", "content_generated_at")
