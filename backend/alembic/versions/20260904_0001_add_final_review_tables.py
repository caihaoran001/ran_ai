"""add day-28 final review script/answer tables and benefit grant ledger

Revision ID: 20260904_0001
Revises: 20260902_0003
Create Date: 2026-09-04
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260904_0001"
down_revision: str | Sequence[str] | None = "20260902_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "final_review_script_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("language", sa.String(length=8), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("nodes_json", sa.JSON(), nullable=False),
        sa.Column("changed_by_name", sa.String(length=128), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "language", "version_no", name="ux_final_review_script_language_no"
        ),
    )
    op.create_index(
        op.f("ix_final_review_script_versions_language"),
        "final_review_script_versions",
        ["language"],
    )

    op.create_table(
        "final_review_answers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("service_case_id", sa.Integer(), nullable=False),
        sa.Column("question_id", sa.String(length=16), nullable=False),
        sa.Column("answer_status", sa.String(length=16), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("normalized_value", sa.JSON(), nullable=True),
        sa.Column("evidence_message_id", sa.Integer(), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("script_version_no", sa.Integer(), nullable=True),
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
        sa.ForeignKeyConstraint(["evidence_message_id"], ["messages.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_final_review_answers_service_case_id"),
        "final_review_answers",
        ["service_case_id"],
    )
    op.create_index(
        op.f("ix_final_review_answers_question_id"),
        "final_review_answers",
        ["question_id"],
    )

    op.create_table(
        "day28_benefit_grants",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("service_case_id", sa.Integer(), nullable=False),
        sa.Column("benefit_code", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=True),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "service_case_id", "benefit_code", name="ux_day28_benefit_grant_case_code"
        ),
    )
    op.create_index(
        op.f("ix_day28_benefit_grants_service_case_id"),
        "day28_benefit_grants",
        ["service_case_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_day28_benefit_grants_service_case_id"),
        table_name="day28_benefit_grants",
    )
    op.drop_table("day28_benefit_grants")
    op.drop_index(
        op.f("ix_final_review_answers_question_id"), table_name="final_review_answers"
    )
    op.drop_index(
        op.f("ix_final_review_answers_service_case_id"),
        table_name="final_review_answers",
    )
    op.drop_table("final_review_answers")
    op.drop_index(
        op.f("ix_final_review_script_versions_language"),
        table_name="final_review_script_versions",
    )
    op.drop_table("final_review_script_versions")
