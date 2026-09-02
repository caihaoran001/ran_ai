"""constrain service case language values

Revision ID: 20260902_0002
Revises: 20260902_0001
Create Date: 2026-09-02
"""

from collections.abc import Sequence

from alembic import op


revision: str = "20260902_0002"
down_revision: str | Sequence[str] | None = "20260902_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 历史列是自由字符串；无效遗留值回退默认中文后再加库级约束。
    op.execute("UPDATE service_cases SET language = 'zh' WHERE language NOT IN ('zh', 'en')")
    op.create_check_constraint(
        "ck_service_cases_language",
        "service_cases",
        "language IN ('zh', 'en')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_service_cases_language", "service_cases", type_="check")
