from __future__ import annotations

from datetime import date
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user import User
from app.services.intake_facts import build_intake_fact_summary
from app.services.questionnaire_template import (
    get_active_questionnaire_fields,
    get_questionnaire_template,
    save_active_questionnaire_fields,
)


def test_templates_have_identical_keys_and_structure():
    zh = get_questionnaire_template("zh")
    en = get_questionnaire_template("en")

    zh_fields = zh.get("fields", [])
    en_fields = en.get("fields", [])

    assert len(zh_fields) == 82
    assert len(en_fields) == 82

    for i, (zf, ef) in enumerate(zip(zh_fields, en_fields)):
        assert zf["id"] == ef["id"], f"id mismatch at index {i}"
        assert zf["key"] == ef["key"], f"key mismatch at index {i}: {zf['key']} vs {ef['key']}"
        assert zf["type"] == ef["type"], f"type mismatch at index {i}"
        assert zf.get("required", False) == ef.get("required", False), f"required mismatch at index {i}"
        zopts = zf.get("options") or []
        eopts = ef.get("options") or []
        assert len(zopts) == len(eopts), f"options count mismatch at index {i}"
        assert ef["title"] and ef["label"], f"missing English title/label at index {i}"


@pytest.mark.asyncio
async def test_get_questionnaire_bilingual_by_case_language(
    db_session: AsyncSession,
    public_client: AsyncClient,
    create_case,
):
    # 1. Create English case
    resp_en = await create_case({
        "group_id": "group_en_001",
        "user": {"name": "Alice Smith"},
    })
    token_en = resp_en.json()["questionnaire_token"]
    case_en = await db_session.get(ServiceCase, resp_en.json()["id"])
    case_en.language = "en"
    await db_session.flush()

    # 2. Create Chinese case
    resp_zh = await create_case({
        "group_id": "group_zh_001",
        "user": {"name": "张三"},
    })
    token_zh = resp_zh.json()["questionnaire_token"]
    case_zh = await db_session.get(ServiceCase, resp_zh.json()["id"])
    case_zh.language = "zh"
    await db_session.flush()

    # Request English questionnaire
    res_en = await public_client.get(f"/questionnaires/{token_en}")
    assert res_en.status_code == 200
    data_en = res_en.json()
    assert data_en["language"] == "en"
    assert data_en["title"] == "HumanBurning.AI 28-Day Camp | User Profile & Baseline Assessment"
    assert len(data_en["fields"]) == 82
    titles_en = [f.get("title") or f.get("label") for f in data_en["fields"]]
    assert "Full Legal Name" in titles_en
    assert "Your Height (cm)" in titles_en
    assert "Your Current Weight (kg or lbs)" in titles_en

    # Request Chinese questionnaire
    res_zh = await public_client.get(f"/questionnaires/{token_zh}")
    assert res_zh.status_code == 200
    data_zh = res_zh.json()
    assert data_zh["language"] == "zh"
    assert "问卷" in data_zh["title"] or "训练营" in data_zh["title"]
    assert len(data_zh["fields"]) > 0
    titles_zh = [f.get("title") or f.get("label") for f in data_zh["fields"]]
    assert any("姓名" in t for t in titles_zh)
    assert any("身高" in t for t in titles_zh)
    assert any("体重" in t for t in titles_zh)


@pytest.mark.asyncio
async def test_submit_en_questionnaire_with_imperial_units(
    db_session: AsyncSession,
    public_client: AsyncClient,
    create_case,
):
    resp = await create_case({
        "group_id": "group_en_002",
        "user": {"name": "Old Name"},
    })
    token = resp.json()["questionnaire_token"]
    case_id = resp.json()["id"]
    case = await db_session.get(ServiceCase, case_id)
    case.language = "en"
    await db_session.flush()

    en_fields = await get_active_questionnaire_fields(db_session, "intake", language="en")

    answers = {
        "SIMPLE1774103358253mn0fc08d": "Carol Danvers",
        "SIMPLE1774103429810mn0fdjg2": "+1-555-0199",
        "RADIO1774103553031mn0fg6ix": "Female",
        "SIMPLE1774103589114mn0fgyd6": "32",
        "SIMPLE1774103860160mn0fmri8": "New York",
        "SIMPLE1774104038286mn0fqky6": "168",
        "SIMPLE1774104087629mn0frn0t": "150 lbs",
        "SIMPLE_ADULT_STABLE_COMFORT_WEIGHT_KG": "130 lbs",
        "SIMPLE1774104236120mn0futlk": "28 inches",
        "DATEPLANSTART0001": "2026-09-10",
        "CHECKBOX1774104667027mn0g4239": "Satisfying change in weight/waist",
    }

    # Submit with payload
    res = await public_client.post(
        f"/questionnaires/{token}/submit",
        json={"answers": answers, "language": "zh"},  # Client cannot maliciously switch case language to zh
    )
    assert res.status_code == 201

    await db_session.refresh(case)
    user = await db_session.get(User, case.user_id)

    # User name synced
    assert user.name == "Carol Danvers"
    assert user.phone == "+1-555-0199"

    # Start date parsed
    assert case.service_plan_start_date == date(2026, 9, 10)
    assert case.language == "en"  # language remained en!
    assert case.status == ServiceCaseStatus.GENERATING
    assert case.intake_pending_generation_at is not None

    # Fact summary unit conversions
    facts = build_intake_fact_summary(en_fields, answers)
    assert facts["legal_name"] == "Carol Danvers"
    assert facts["gender"] == "Female"
    assert float(facts["height_cm"]) == 168.0
    # 150 lbs = 68.0 kg (rounded)
    assert float(facts["weight_kg"]) == pytest.approx(68.0, abs=0.1)
    # 28 inches = 71.1 cm (rounded)
    assert float(facts["waist_cm"]) == pytest.approx(71.1, abs=0.2)


@pytest.mark.asyncio
async def test_admin_saving_chinese_template_does_not_deactivate_english_template(db_session: AsyncSession):
    # Save a custom Chinese template
    await save_active_questionnaire_fields(
        db_session,
        [{"key": "test_zh_key", "type": "SIMPLE", "title": "自定义中文题目", "label": "自定义中文题目"}],
        name="Custom Chinese Template",
        language="zh",
    )

    # Save a custom English template
    await save_active_questionnaire_fields(
        db_session,
        [{"key": "test_en_key", "type": "SIMPLE", "title": "Custom English Field", "label": "Custom English Field"}],
        name="Custom English Template",
        language="en",
    )

    # Verify both are active for their respective languages
    zh_fields = await get_active_questionnaire_fields(db_session, "intake", language="zh")
    en_fields = await get_active_questionnaire_fields(db_session, "intake", language="en")

    assert zh_fields[0]["key"] == "test_zh_key"
    assert en_fields[0]["key"] == "test_en_key"

    # Save a second Chinese template; English template must remain untouched
    await save_active_questionnaire_fields(
        db_session,
        [{"key": "test_zh_key_2", "type": "SIMPLE", "title": "第二次中文题目", "label": "第二次中文题目"}],
        name="Custom Chinese Template 2",
        language="zh",
    )

    zh_fields_new = await get_active_questionnaire_fields(db_session, "intake", language="zh")
    en_fields_new = await get_active_questionnaire_fields(db_session, "intake", language="en")

    assert zh_fields_new[0]["key"] == "test_zh_key_2"
    assert en_fields_new[0]["key"] == "test_en_key"
