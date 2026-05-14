"""questionnaire template and docx path

Revision ID: 20260513_0002
Revises: 20260513_0001
Create Date: 2026-05-13
"""

from alembic import op
import sqlalchemy as sa

revision = "20260513_0002"
down_revision = "20260513_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "questionnaire_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("fields_json", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.add_column("user_documents", sa.Column("file_path", sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column("user_documents", "file_path")
    op.drop_table("questionnaire_templates")
