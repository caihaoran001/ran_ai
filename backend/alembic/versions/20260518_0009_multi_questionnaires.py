"""add multi questionnaire templates

Revision ID: 20260518_0009
Revises: 552fbc7439c8
Create Date: 2026-05-18
"""

from alembic import op
import sqlalchemy as sa

revision = "20260518_0009"
down_revision = "552fbc7439c8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("questionnaire_templates", sa.Column("template_key", sa.String(length=80), nullable=False, server_default="intake"))
    op.add_column("questionnaire_templates", sa.Column("title", sa.String(length=200), nullable=True))
    op.add_column("questionnaire_templates", sa.Column("description", sa.String(length=1000), nullable=True))
    op.create_index("ix_questionnaire_templates_template_key", "questionnaire_templates", ["template_key"])

    op.add_column("questionnaire_submissions", sa.Column("template_key", sa.String(length=80), nullable=False, server_default="intake"))
    op.create_index("ix_questionnaire_submissions_template_key", "questionnaire_submissions", ["template_key"])


def downgrade() -> None:
    op.drop_index("ix_questionnaire_submissions_template_key", table_name="questionnaire_submissions")
    op.drop_column("questionnaire_submissions", "template_key")
    op.drop_index("ix_questionnaire_templates_template_key", table_name="questionnaire_templates")
    op.drop_column("questionnaire_templates", "description")
    op.drop_column("questionnaire_templates", "title")
    op.drop_column("questionnaire_templates", "template_key")
