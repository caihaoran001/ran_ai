"""add stable comfortable adult weight question to intake templates

Revision ID: 20260731_0067
Revises: 20260731_0066
Create Date: 2026-07-31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260731_0067"
down_revision = "20260731_0066"
branch_labels = None
depends_on = None


QUESTION_KEY = "SIMPLE_ADULT_STABLE_COMFORT_WEIGHT_KG"
CURRENT_WEIGHT_KEY = "SIMPLE1774104087629mn0frn0t"
QUESTION = {
    "id": QUESTION_KEY,
    "key": QUESTION_KEY,
    "type": "SIMPLE",
    "title": (
        "回想您成年后，有没有过一个体重：让您觉得身体比较轻盈，精力较为旺盛，"
        "正常吃饭生活，并且这个体重数字稳定过1年以上的时间？如果有，当时是多少kg？"
    ),
    "label": (
        "回想您成年后，有没有过一个体重：让您觉得身体比较轻盈，精力较为旺盛，"
        "正常吃饭生活，并且这个体重数字稳定过1年以上的时间？如果有，当时是多少kg？"
    ),
    "subtitle": "如没有或不确定，可填写“没有”或“不确定”",
    "required": True,
    "options": [],
}


templates = sa.table(
    "questionnaire_templates",
    sa.column("id", sa.Integer),
    sa.column("template_key", sa.String),
    sa.column("fields_json", sa.JSON),
)


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.select(templates.c.id, templates.c.fields_json).where(
            templates.c.template_key == "intake"
        )
    ).mappings()
    for row in rows:
        fields = list(row["fields_json"] or [])
        if any(
            str(field.get("key") or field.get("id") or "") == QUESTION_KEY
            for field in fields
        ):
            continue
        insert_at = next(
            (
                index + 1
                for index, field in enumerate(fields)
                if str(field.get("key") or field.get("id") or "") == CURRENT_WEIGHT_KEY
            ),
            len(fields),
        )
        fields.insert(insert_at, dict(QUESTION))
        # 同步去掉历史模板对当前体重的空说明，明确 kg/斤均可填写。
        for field in fields:
            if str(field.get("key") or field.get("id") or "") == CURRENT_WEIGHT_KEY:
                field["subtitle"] = "可填写 kg/公斤或斤，系统会自动换算"
                break
        bind.execute(
            sa.update(templates)
            .where(templates.c.id == row["id"])
            .values(fields_json=fields)
        )


def downgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.select(templates.c.id, templates.c.fields_json).where(
            templates.c.template_key == "intake"
        )
    ).mappings()
    for row in rows:
        fields = [
            field
            for field in list(row["fields_json"] or [])
            if str(field.get("key") or field.get("id") or "") != QUESTION_KEY
        ]
        for field in fields:
            if str(field.get("key") or field.get("id") or "") == CURRENT_WEIGHT_KEY:
                field["subtitle"] = ""
                break
        bind.execute(
            sa.update(templates)
            .where(templates.c.id == row["id"])
            .values(fields_json=fields)
        )
