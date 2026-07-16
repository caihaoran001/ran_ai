"""remove stopped-period-only risk flags

Revision ID: 20260716_0049
Revises: 20260716_0048
Create Date: 2026-07-16
"""

from __future__ import annotations

from alembic import op


revision = "20260716_0049"
down_revision = "20260716_0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 这两张服务单的 pregnancy 风险仅来自“停经”陈述，既未确认怀孕也没有其他
    # 孕哺信息。删除该误判，并按剩余风险重新计算服务单等级。
    op.execute(
        """
        WITH cleaned AS (
            SELECT
                id,
                COALESCE(
                    (
                        SELECT jsonb_agg(flag)
                        FROM jsonb_array_elements(
                            CASE
                                WHEN jsonb_typeof(risk_flags::jsonb) = 'array' THEN risk_flags::jsonb
                                ELSE '[]'::jsonb
                            END
                        ) AS flag
                        WHERE flag->>'code' <> 'pregnancy'
                    ),
                    '[]'::jsonb
                ) AS risk_flags,
                COALESCE(
                    (
                        SELECT jsonb_agg(reason)
                        FROM jsonb_array_elements(
                            CASE
                                WHEN jsonb_typeof(intervention_reasons::jsonb) = 'array' THEN intervention_reasons::jsonb
                                ELSE '[]'::jsonb
                            END
                        ) AS reason
                        WHERE reason->>'code' <> 'pregnancy'
                    ),
                    '[]'::jsonb
                ) AS intervention_reasons
            FROM service_cases
            WHERE case_no IN ('RAI-20260620-35B97D', 'RAI-20260615-6D7267')
        ),
        relevel AS (
            SELECT
                id,
                risk_flags,
                intervention_reasons,
                CASE
                    WHEN EXISTS (
                        SELECT 1
                        FROM jsonb_array_elements(risk_flags) AS flag
                        WHERE flag->>'level' = 'high'
                          AND flag->>'code' IN (
                              'elderly_age', 'pregnancy', 'lactation', 'chronic_disease',
                              'long_term_medication', 'hormone_medication', 'liver_kidney',
                              'stone', 'severe_test_abnormality', 'depression'
                          )
                          AND COALESCE(NULLIF(BTRIM(flag->>'reason'), ''), '') <> ''
                          AND COALESCE(NULLIF(BTRIM(flag->>'evidence'), ''), '') <> ''
                    ) THEN 'high'
                    WHEN EXISTS (
                        SELECT 1
                        FROM jsonb_array_elements(risk_flags) AS flag
                        WHERE flag->>'level' = 'medium'
                    ) THEN 'medium'
                    ELSE 'normal'
                END AS risk_level
            FROM cleaned
        )
        UPDATE service_cases AS service_case
        SET
            risk_flags = relevel.risk_flags,
            intervention_reasons = relevel.intervention_reasons,
            risk_level = relevel.risk_level
        FROM relevel
        WHERE service_case.id = relevel.id
        """
    )


def downgrade() -> None:
    # 已删除的误判风险不应在回滚时恢复。
    pass
