"""add versioned five-item check-in checklist

Revision ID: 20260720_0054
Revises: 20260720_0053
Create Date: 2026-07-20
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260720_0054"
down_revision = "20260720_0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "checkin_checklist_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("service_case_id", sa.Integer(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("items_json", sa.JSON(), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_message_id", sa.Integer(), nullable=True),
        sa.Column("source_document_id", sa.Integer(), nullable=True),
        sa.Column("changed_by_id", sa.String(length=128), nullable=True),
        sa.Column("changed_by_name", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["service_case_id"], ["service_cases.id"]),
        sa.ForeignKeyConstraint(["source_message_id"], ["messages.id"]),
        sa.ForeignKeyConstraint(["source_document_id"], ["user_documents.id"]),
        sa.UniqueConstraint(
            "service_case_id",
            "version_no",
            name="ux_checkin_checklist_version_case_no",
        ),
        sa.UniqueConstraint(
            "source_message_id",
            name="ux_checkin_checklist_version_source_message",
        ),
    )
    op.create_index(
        "ix_checkin_checklist_versions_service_case_id",
        "checkin_checklist_versions",
        ["service_case_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_checkin_checklist_versions_service_case_id",
        table_name="checkin_checklist_versions",
    )
    op.drop_table("checkin_checklist_versions")
