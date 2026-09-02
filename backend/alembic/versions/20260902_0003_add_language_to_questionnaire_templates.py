"""add language to questionnaire templates

Revision ID: 20260902_0003
Revises: 20260902_0002
Create Date: 2026-09-02
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260902_0003"
down_revision: str | Sequence[str] | None = "20260902_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "questionnaire_templates",
        sa.Column(
            "language",
            sa.String(length=10),
            nullable=False,
            server_default="zh",
        ),
    )
    op.create_index(
        "ix_questionnaire_templates_template_key_language",
        "questionnaire_templates",
        ["template_key", "language"],
    )
    op.create_check_constraint(
        "ck_questionnaire_templates_language",
        "questionnaire_templates",
        "language IN ('zh', 'en')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_questionnaire_templates_language",
        "questionnaire_templates",
        type_="check",
    )
    op.drop_index(
        "ix_questionnaire_templates_template_key_language",
        table_name="questionnaire_templates",
    )
    op.drop_column("questionnaire_templates", "language")
