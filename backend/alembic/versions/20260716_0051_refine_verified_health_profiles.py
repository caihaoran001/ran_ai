"""refine verified health profile backfill

Revision ID: 20260716_0051
Revises: 20260716_0050
Create Date: 2026-07-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260716_0051"
down_revision = "20260716_0050"
branch_labels = None
depends_on = None


def _condition(code: str, evidence: str, source: str) -> dict:
    return {"code": code, "status": "confirmed", "evidence": evidence, "source": source}


def _medication(category_code: str, name: str, evidence: str, source: str) -> dict:
    return {
        "category_code": category_code,
        "name": name,
        "long_term": True,
        "status": "active",
        "evidence": evidence,
        "source": source,
    }


def _profile(
    *,
    conditions: list[dict] | None = None,
    medications: list[dict] | None = None,
    allergies: list[dict] | None = None,
) -> dict:
    return {
        "conditions": conditions or [],
        "medications": medications or [],
        "special_stages": [],
        "allergies": allergies or [],
    }


def _allergy() -> dict:
    return {
        "category_code": "drug_allergy",
        "substance": "沙星类药物",
        "status": "confirmed",
        "evidence": "沙星类药物过敏",
        "source": "questionnaire",
    }


def _write_profiles(profiles: dict[str, dict]) -> None:
    bind = op.get_bind()
    update = sa.text(
        "UPDATE service_cases SET health_profile = :profile WHERE case_no = :case_no"
    ).bindparams(sa.bindparam("profile", type_=sa.JSON()))
    for case_no, profile in profiles.items():
        bind.execute(update, {"case_no": case_no, "profile": profile})


def upgrade() -> None:
    # 药名/药类只能形成用药标签，不能反推疾病诊断；“血压有点高”保留为原义健康情况。
    _write_profiles({
        "RAI-20260623-5E1BD1": _profile(
            conditions=[_condition(
                "elevated_blood_pressure", "就是血压有点高", "chat_message"
            )],
            medications=[_medication(
                "antihypertensive",
                "苯磺酸氨氯地平片",
                "目前就服用降压药（苯磺酸氨氯地平片）",
                "questionnaire",
            )],
        ),
        "RAI-20260613-39B2B2": _profile(
            medications=[
                _medication(
                    "thyroid_hormone", "优甲乐", "优甲乐，降压药，降脂药", "questionnaire"
                ),
                _medication(
                    "antihypertensive", "降压药", "优甲乐，降压药，降脂药", "questionnaire"
                ),
                _medication(
                    "lipid_lowering", "降脂药", "优甲乐，降压药，降脂药", "questionnaire"
                ),
            ],
        ),
        "RAI-20260612-A59761": _profile(
            medications=[_medication(
                "antihypertensive", "降压药", "降压药", "questionnaire"
            )],
            allergies=[_allergy()],
        ),
        "RAI-20260630-E187E8": _profile(
            medications=[_medication(
                "antihypertensive", "高血压药", "高血压药有在吃", "questionnaire"
            )],
        ),
    })


def downgrade() -> None:
    _write_profiles({
        "RAI-20260623-5E1BD1": _profile(
            conditions=[_condition("hypertension", "就是血压有点高", "chat_message")],
            medications=[_medication(
                "antihypertensive",
                "苯磺酸氨氯地平片",
                "目前就服用降压药（苯磺酸氨氯地平片）",
                "questionnaire",
            )],
        ),
        "RAI-20260613-39B2B2": _profile(
            conditions=[
                _condition("hypertension", "优甲乐，降压药，降脂药", "questionnaire"),
                _condition("dyslipidemia", "睡前我要吃降血脂的药的", "chat_message"),
            ],
            medications=[
                _medication(
                    "thyroid_hormone", "优甲乐", "优甲乐，降压药，降脂药", "questionnaire"
                ),
                _medication(
                    "antihypertensive", "降压药", "优甲乐，降压药，降脂药", "questionnaire"
                ),
                _medication(
                    "lipid_lowering", "降脂药", "优甲乐，降压药，降脂药", "questionnaire"
                ),
            ],
        ),
        "RAI-20260612-A59761": _profile(
            conditions=[_condition("hypertension", "降压药", "questionnaire")],
            medications=[_medication(
                "antihypertensive", "降压药", "降压药", "questionnaire"
            )],
            allergies=[_allergy()],
        ),
        "RAI-20260630-E187E8": _profile(
            conditions=[_condition("hypertension", "高血压药有在吃", "questionnaire")],
            medications=[_medication(
                "antihypertensive", "高血压药", "高血压药有在吃", "questionnaire"
            )],
        ),
    })
