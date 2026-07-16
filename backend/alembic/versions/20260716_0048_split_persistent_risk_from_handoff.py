"""split persistent health risk from one-off handoff events

Revision ID: 20260716_0048
Revises: 20260715_0047
Create Date: 2026-07-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260716_0048"
down_revision = "20260715_0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 历史上任何一条 level=high 都会把 service_case 挂成高风险，包括 other_risk、
    # 急性体感和医疗边界提问。新口径只保留有明确分类与原话证据的持续健康背景。
    op.execute(
        """
        WITH normalized AS (
            SELECT
                id,
                CASE
                    WHEN EXISTS (
                        SELECT 1
                        FROM jsonb_array_elements(
                            CASE
                                WHEN jsonb_typeof(risk_flags::jsonb) = 'array' THEN risk_flags::jsonb
                                ELSE '[]'::jsonb
                            END
                        ) AS flag
                        WHERE flag->>'level' = 'high'
                          AND flag->>'code' IN (
                              'pregnancy', 'lactation', 'chronic_disease',
                              'long_term_medication', 'hormone_medication',
                              'liver_kidney', 'stone', 'severe_test_abnormality', 'depression'
                          )
                          AND COALESCE(NULLIF(BTRIM(flag->>'reason'), ''), '') <> ''
                          AND COALESCE(NULLIF(BTRIM(flag->>'evidence'), ''), '') <> ''
                    ) THEN 'high'
                    WHEN EXISTS (
                        SELECT 1
                        FROM jsonb_array_elements(
                            CASE
                                WHEN jsonb_typeof(risk_flags::jsonb) = 'array' THEN risk_flags::jsonb
                                ELSE '[]'::jsonb
                            END
                        ) AS flag
                        WHERE flag->>'level' = 'medium'
                    ) THEN 'medium'
                    ELSE 'normal'
                END AS risk_level
            FROM service_cases
        )
        UPDATE service_cases AS service_case
        SET risk_level = normalized.risk_level
        FROM normalized
        WHERE service_case.id = normalized.id
          AND service_case.risk_level = 'high'
          AND normalized.risk_level <> 'high'
        """
    )

    # AI 待审核已废弃，转人工直接 @班主任；删除其服务单级残留状态。
    op.drop_column("service_cases", "needs_human_intervention")


def downgrade() -> None:
    op.add_column(
        "service_cases",
        sa.Column(
            "needs_human_intervention",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_index(
        "ix_service_cases_needs_human_intervention",
        "service_cases",
        ["needs_human_intervention"],
    )
    op.alter_column("service_cases", "needs_human_intervention", server_default=None)
