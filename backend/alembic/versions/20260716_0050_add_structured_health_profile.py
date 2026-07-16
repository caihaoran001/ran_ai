"""add structured health profile and backfill verified cases

Revision ID: 20260716_0050
Revises: 20260716_0049
Create Date: 2026-07-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260716_0050"
down_revision = "20260716_0049"
branch_labels = None
depends_on = None


def _condition(code: str, evidence: str, source: str, status: str = "confirmed") -> dict:
    return {"code": code, "status": status, "evidence": evidence, "source": source}


def _medication(
    category_code: str,
    name: str,
    evidence: str,
    source: str,
    *,
    long_term: bool = True,
    status: str = "active",
) -> dict:
    return {
        "category_code": category_code,
        "name": name,
        "long_term": long_term,
        "status": status,
        "evidence": evidence,
        "source": source,
    }


def _stage(code: str, evidence: str, source: str, status: str = "active") -> dict:
    return {"code": code, "status": status, "evidence": evidence, "source": source}


def _profile(
    *,
    conditions: list[dict] | None = None,
    medications: list[dict] | None = None,
    special_stages: list[dict] | None = None,
    allergies: list[dict] | None = None,
) -> dict:
    return {
        "conditions": conditions or [],
        "medications": medications or [],
        "special_stages": special_stages or [],
        "allergies": allergies or [],
    }


def upgrade() -> None:
    op.add_column("service_cases", sa.Column("health_profile", sa.JSON(), nullable=True))

    # 依据 2026-07-16 对生产高风险服务单问卷与聊天原话的逐单核验回填。
    # 待检查/可能性结论只记 pending，不进入确诊疾病统计。
    profiles = {
        "RAI-20260623-4997CB": _profile(
            conditions=[_condition("post_thyroidectomy", "甲状腺切除，每天需要吃优甲乐", "questionnaire")],
            medications=[_medication("thyroid_hormone", "优甲乐", "甲状腺切除，每天需要吃优甲乐", "questionnaire")],
        ),
        "RAI-20260613-85B0DC": _profile(
            conditions=[
                _condition("hypertension", "高血压需要服药", "questionnaire"),
                _condition("gallstone", "我有胆结石", "chat_message"),
            ],
            medications=[_medication("antihypertensive", "降压药", "高血压需要服药", "questionnaire")],
        ),
        "RAI-20260629-1204EF": _profile(
            conditions=[_condition("hyperthyroidism", "可能亚临床甲亢。", "chat_message", "pending")],
        ),
        "RAI-20260623-5E1BD1": _profile(
            conditions=[_condition("hypertension", "就是血压有点高", "chat_message")],
            medications=[_medication("antihypertensive", "苯磺酸氨氯地平片", "目前就服用降压药（苯磺酸氨氯地平片）", "questionnaire")],
        ),
        "RAI-20260629-521925": _profile(
            conditions=[_condition("hypothyroidism", "甲状腺功能减退，每天一片优甲乐", "questionnaire")],
            medications=[_medication("thyroid_hormone", "优甲乐", "甲状腺功能减退，每天一片优甲乐", "questionnaire")],
        ),
        "RAI-20260621-DF3B44": _profile(
            conditions=[_condition("hyperprolactinemia", "高泌乳素血症，服用溴隐亭", "questionnaire")],
            medications=[_medication("prolactin_lowering", "溴隐亭", "高泌乳素血症，服用溴隐亭", "questionnaire")],
        ),
        "RAI-20260621-075A8E": _profile(
            conditions=[_condition("fatty_liver", "脂肪肝风险等级Ⅱ级（中度风险）", "chat_message", "pending")],
        ),
        "RAI-20260615-6A294C": _profile(
            medications=[
                _medication("thyroid_hormone", "优甲乐", "优甲乐", "questionnaire"),
                _medication("endocrine_therapy", "来曲唑", "来曲唑", "questionnaire", long_term=False),
            ],
            special_stages=[_stage("postoperative_recovery", "手术刚做完，适当增加走路时间", "questionnaire")],
        ),
        "RAI-20260613-A00579": _profile(
            conditions=[
                _condition("hypothyroidism", "甲减 甲状腺结节 长期服用优甲乐", "questionnaire"),
                _condition("thyroid_nodule", "甲减 甲状腺结节 长期服用优甲乐", "questionnaire"),
            ],
            medications=[_medication("thyroid_hormone", "优甲乐", "甲减 甲状腺结节 长期服用优甲乐", "questionnaire")],
        ),
        "RAI-20260613-39B2B2": _profile(
            conditions=[
                _condition("hypertension", "优甲乐，降压药，降脂药", "questionnaire"),
                _condition("dyslipidemia", "睡前我要吃降血脂的药的", "chat_message"),
            ],
            medications=[
                _medication("thyroid_hormone", "优甲乐", "优甲乐，降压药，降脂药", "questionnaire"),
                _medication("antihypertensive", "降压药", "优甲乐，降压药，降脂药", "questionnaire"),
                _medication("lipid_lowering", "降脂药", "优甲乐，降压药，降脂药", "questionnaire"),
            ],
        ),
        "RAI-20260612-A59761": _profile(
            conditions=[_condition("hypertension", "降压药", "questionnaire")],
            medications=[_medication("antihypertensive", "降压药", "降压药", "questionnaire")],
            allergies=[{
                "category_code": "drug_allergy",
                "substance": "沙星类药物",
                "status": "confirmed",
                "evidence": "沙星类药物过敏",
                "source": "questionnaire",
            }],
        ),
        "RAI-20260628-9BA481": _profile(
            conditions=[_condition("pcos", "多囊卵巢医生开的", "chat_message")],
            medications=[_medication("glucose_lowering", "二甲双胍", "先吃二甲双胍，那个调料多囊卵巢医生开的", "chat_message")],
        ),
        "RAI-20260630-E187E8": _profile(
            conditions=[_condition("hypertension", "高血压药有在吃", "questionnaire")],
            medications=[_medication("antihypertensive", "高血压药", "高血压药有在吃", "questionnaire")],
        ),
        "RAI-20260613-3E5FA4": _profile(
            conditions=[_condition("thyroid_nodule", "甲状腺结节4A", "questionnaire")],
        ),
        "RAI-20260622-14607C": _profile(
            conditions=[
                _condition("cholecystitis", "胆囊炎，肝不太好", "questionnaire"),
                _condition("liver_condition_unspecified", "胆囊炎，肝不太好", "questionnaire", "pending"),
            ],
        ),
        "RAI-20260616-1678B1": _profile(
            conditions=[_condition("diabetes_unspecified", "有糖尿病", "chat_message")],
        ),
        "RAI-20260616-78433E": _profile(
            conditions=[_condition("type2_diabetes", "有二型糖尿病", "chat_message")],
        ),
        "RAI-20260612-A172DC": _profile(
            conditions=[_condition("hypertension", "高血压", "questionnaire")],
        ),
    }

    bind = op.get_bind()
    update = sa.text(
        "UPDATE service_cases SET health_profile = :profile WHERE case_no = :case_no"
    ).bindparams(sa.bindparam("profile", type_=sa.JSON()))
    for case_no, profile in profiles.items():
        bind.execute(update, {"case_no": case_no, "profile": profile})

    # 两张历史服务单只有“待确认”的检查线索，不应继续作为持续高风险。
    op.execute(
        """
        UPDATE service_cases
        SET risk_level = 'normal', risk_flags = '[]'::json, intervention_reasons = '[]'::json
        WHERE case_no IN ('RAI-20260629-1204EF', 'RAI-20260621-075A8E')
        """
    )


def downgrade() -> None:
    op.drop_column("service_cases", "health_profile")
