"""initial schema

Revision ID: 20260513_0001
Revises:
Create Date: 2026-05-13
"""

from alembic import op
import sqlalchemy as sa

revision = "20260513_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    service_case_status = sa.Enum(
        "CREATED",
        "QUESTIONNAIRE_SUBMITTED",
        "DOCUMENTS_GENERATED",
        "REVIEWING",
        "APPROVED",
        "IN_SERVICE",
        "COMPLETED",
        name="service_case_status",
    )
    document_type = sa.Enum(
        "ENERGY_RESTART_PRESCRIPTION",
        "DAY28_EXECUTION_STANDARD",
        name="document_type",
    )
    document_status = sa.Enum(
        "DRAFT",
        "REVIEWING",
        "APPROVED",
        "SENT",
        name="document_status",
    )
    message_direction = sa.Enum("INBOUND", "OUTBOUND", name="message_direction")
    message_sender_type = sa.Enum("USER", "STAFF", "BOT", "SYSTEM", name="message_sender_type")
    message_purpose = sa.Enum(
        "CHAT",
        "CHECKIN",
        "REMINDER",
        "DOCUMENT_DELIVERY",
        "MANUAL",
        name="message_purpose",
    )
    message_status = sa.Enum("RECEIVED", "PENDING", "SENT", "FAILED", name="message_status")
    reminder_event_type = sa.Enum("SEEN", "REMINDED", name="reminder_event_type")

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("wecom_user_id", sa.String(length=128), nullable=True),
        sa.Column("notes", sa.String(length=1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("phone"),
        sa.UniqueConstraint("wecom_user_id"),
    )

    op.create_table(
        "service_cases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_no", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("group_id", sa.String(length=128), nullable=False),
        sa.Column("questionnaire_token", sa.String(length=128), nullable=False),
        sa.Column("status", service_case_status, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.String(length=2000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("case_no"),
        sa.UniqueConstraint("group_id"),
        sa.UniqueConstraint("questionnaire_token"),
    )

    op.create_table(
        "questionnaire_submissions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("service_case_id", sa.Integer(), sa.ForeignKey("service_cases.id"), nullable=False),
        sa.Column("answers_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_questionnaire_submissions_service_case_id", "questionnaire_submissions", ["service_case_id"])

    op.create_table(
        "user_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("service_case_id", sa.Integer(), sa.ForeignKey("service_cases.id"), nullable=False),
        sa.Column("profile_json", sa.JSON(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("generated_by_model", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_user_profiles_service_case_id", "user_profiles", ["service_case_id"])

    op.create_table(
        "user_documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("service_case_id", sa.Integer(), sa.ForeignKey("service_cases.id"), nullable=False),
        sa.Column("document_type", document_type, nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("content_md", sa.Text(), nullable=False),
        sa.Column("status", document_status, nullable=False),
        sa.Column("generated_by_model", sa.String(length=128), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_user_documents_service_case_id", "user_documents", ["service_case_id"])

    op.create_table(
        "checkins",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("service_case_id", sa.Integer(), sa.ForeignKey("service_cases.id"), nullable=False),
        sa.Column("group_id", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("checkin_date", sa.Date(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("raw_message_id", sa.String(length=128), nullable=True),
        sa.Column("parsed_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("service_case_id", "checkin_date", name="uq_checkin_case_date"),
    )
    op.create_index("ix_checkins_service_case_id", "checkins", ["service_case_id"])
    op.create_index("ix_checkins_group_id", "checkins", ["group_id"])

    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("service_case_id", sa.Integer(), sa.ForeignKey("service_cases.id"), nullable=True),
        sa.Column("group_id", sa.String(length=128), nullable=False),
        sa.Column("wecom_message_id", sa.String(length=128), nullable=True),
        sa.Column("direction", message_direction, nullable=False),
        sa.Column("sender_type", message_sender_type, nullable=False),
        sa.Column("message_type", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("purpose", message_purpose, nullable=False),
        sa.Column("status", message_status, nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_messages_service_case_id", "messages", ["service_case_id"])
    op.create_index("ix_messages_group_id", "messages", ["group_id"])
    op.create_index("ix_messages_wecom_message_id", "messages", ["wecom_message_id"])

    op.create_table(
        "reminder_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("service_case_id", sa.Integer(), sa.ForeignKey("service_cases.id"), nullable=False),
        sa.Column("rule_id", sa.String(length=100), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("event_type", reminder_event_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "service_case_id",
            "rule_id",
            "event_date",
            "event_type",
            name="uq_reminder_case_rule_date_type",
        ),
    )
    op.create_index("ix_reminder_events_service_case_id", "reminder_events", ["service_case_id"])


def downgrade() -> None:
    for table in [
        "reminder_events",
        "messages",
        "checkins",
        "user_documents",
        "user_profiles",
        "questionnaire_submissions",
        "service_cases",
        "users",
    ]:
        op.drop_table(table)
    for enum_name in [
        "reminder_event_type",
        "message_status",
        "message_purpose",
        "message_sender_type",
        "message_direction",
        "document_status",
        "document_type",
        "service_case_status",
    ]:
        sa.Enum(name=enum_name).drop(op.get_bind(), checkfirst=True)
