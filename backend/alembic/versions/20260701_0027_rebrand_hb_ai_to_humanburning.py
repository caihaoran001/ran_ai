"""rebrand HB.AI -> 燃人HumanBurning.AI in persisted questionnaire templates

Revision ID: 20260701_0027
Revises: 20260701_0026
Create Date: 2026-07-01

HB.AI 恰好是他人拥有的域名，消费者会误当网址跳转。品牌统一改为
"燃人HumanBurning.AI"。代码常量已同步修改，此迁移负责把线上运营已持久化到
questionnaire_templates 表的旧品牌串（title 与 fields_json 内各段落 intro）
一并替换。fields_json 列类型为 json（见 20260513_0002），故按 ::json 处理。

幂等：新串不含子串 "HB.AI"，且 WHERE LIKE 过滤，重复执行无副作用。
"""

from alembic import op


revision = "20260701_0027"
down_revision = "20260701_0026"
branch_labels = None
depends_on = None

OLD = "HB.AI"
NEW = "燃人HumanBurning.AI"


def upgrade() -> None:
    op.execute(
        f"UPDATE questionnaire_templates "
        f"SET title = replace(title, '{OLD}', '{NEW}') "
        f"WHERE title LIKE '%{OLD}%'"
    )
    op.execute(
        f"UPDATE questionnaire_templates "
        f"SET fields_json = replace(fields_json::text, '{OLD}', '{NEW}')::json "
        f"WHERE fields_json::text LIKE '%{OLD}%'"
    )


def downgrade() -> None:
    op.execute(
        f"UPDATE questionnaire_templates "
        f"SET title = replace(title, '{NEW}', '{OLD}') "
        f"WHERE title LIKE '%{NEW}%'"
    )
    op.execute(
        f"UPDATE questionnaire_templates "
        f"SET fields_json = replace(fields_json::text, '{NEW}', '{OLD}')::json "
        f"WHERE fields_json::text LIKE '%{NEW}%'"
    )
