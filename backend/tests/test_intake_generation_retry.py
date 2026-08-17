"""入营交付文件生成的重试契约。

背景（2026-08-17 生产事故，服务单 308）：第一次用主模型 pro 生成的 package 内容不完整被
校验拒绝，随后系统把剩余两次尝试永久切到备用模型 flash，两次都只吐思考、正文全空，最终
0 份文件。且服务单备注只留了最后一次的错误，前两次的拒绝原因随容器日志轮转永久丢失。

本文件锁定两条修复后的行为：
1. 内容不合格只在同一档模型上重试，绝不降级换模型；
2. 三次都失败时，最终错误必须带上每一次的拒绝原因。
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.ai_usage import AIUsageLog
from app.models.questionnaire import QuestionnaireSubmission
from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user_document import DocumentType, UserDocument
from app.services.intake_generation import INTAKE_DOCUMENT_MAX_ATTEMPTS
from app.services.reminder_service import (
    INTAKE_DOCUMENT_RETRY_WINDOW_DAYS,
    ReminderService,
)
from tests.test_01_intake import _make_intake_mock_response

INTAKE_SYSTEM_PROMPT_PREFIX = "你是加速燃「高燃人生28天训练营」的首席能量教练"


def _package_missing_documents() -> str:
    """能解析成 JSON、但一份交付文件都没有——复刻"模型想完了几乎没写正文"的产物。"""
    return json.dumps(
        {"intake_analysis_package": {"shared_analysis": {"weight_band": "B带｜轻盈启动"}}},
        ensure_ascii=False,
    )


@pytest.mark.asyncio
async def test_content_failure_retries_on_same_model_and_keeps_every_reason(
    admin_client,
    create_case,
    db_session,
    mock_ai,
    cai_haoran_answers,
    wait_background,
):
    mock_ai._responses = {
        INTAKE_SYSTEM_PROMPT_PREFIX: [_package_missing_documents()] * INTAKE_DOCUMENT_MAX_ATTEMPTS,
    }

    resp = await create_case(
        {
            "group_id": f"test-group-retry-{secrets.token_hex(6)}",
            "user": {"name": "蔡浩然"},
        }
    )
    assert resp.status_code == 201, resp.text
    case_id = resp.json()["id"]
    token = resp.json()["questionnaire_token"]

    submit_resp = await admin_client.post(
        f"/questionnaires/{token}/submit?template=intake",
        json={"answers": cai_haoran_answers},
    )
    assert submit_resp.status_code == 201, submit_resp.text
    await wait_background()

    intake_calls = [call for call in mock_ai.calls if call.get("scenario") == "intake_doc"]
    assert len(intake_calls) == INTAKE_DOCUMENT_MAX_ATTEMPTS, (
        f"内容不合格应在同档模型上重试满 {INTAKE_DOCUMENT_MAX_ATTEMPTS} 次"
    )

    # 没有任何一次尝试被切到备用模型：mock 不实现 activate_fallback_model，
    # 生产侧的同一条约束由 test_ai_service_document_generation_options.py 覆盖。
    assert not hasattr(mock_ai, "_document_fallback_scenarios")

    documents = (
        await db_session.scalars(
            select(UserDocument).where(UserDocument.service_case_id == case_id)
        )
    ).all()
    assert documents == [], "校验没过就不该落库任何交付文件"

    service_case = await db_session.scalar(
        select(ServiceCase).where(ServiceCase.id == case_id)
    )
    assert service_case is not None
    await db_session.refresh(service_case)
    notes = service_case.notes or ""
    for attempt in range(1, INTAKE_DOCUMENT_MAX_ATTEMPTS + 1):
        assert f"第{attempt}次" in notes, f"备注缺第 {attempt} 次尝试的拒绝原因：{notes}"
    assert "both delivery documents" in notes
    # 失败后回落到「已提交问卷」，交运营重新触发，不能停在「生成中」。
    assert service_case.status == ServiceCaseStatus.QUESTIONNAIRE_SUBMITTED


# --------------------------------------------------------------------------------------
# 兜底补跑的限次规则：区分「资料不全」(必然失败、永不重试) 与「模型内容失败」(值得重试)
# --------------------------------------------------------------------------------------


async def _case_missing_documents(
    admin_client,
    create_case,
    db_session,
    cai_haoran_answers,
    *,
    tag: str,
    submitted_days_ago: float,
) -> int:
    """造一个「交了入营问卷、但一份交付文件都没有」的活跃服务单。"""
    resp = await create_case(
        {"group_id": f"test-group-{tag}-{secrets.token_hex(6)}", "user": {"name": "缺文件用户"}}
    )
    assert resp.status_code == 201, resp.text
    case_id = resp.json()["id"]
    submit_resp = await admin_client.post(
        f"/questionnaires/{resp.json()['questionnaire_token']}/submit?template=intake",
        json={"answers": cai_haoran_answers},
    )
    assert submit_resp.status_code == 201, submit_resp.text

    service_case = await db_session.get(ServiceCase, case_id)
    service_case.intake_pending_generation_at = None
    service_case.status = ServiceCaseStatus.IN_SERVICE
    submission = await db_session.scalar(
        select(QuestionnaireSubmission).where(
            QuestionnaireSubmission.service_case_id == case_id,
            QuestionnaireSubmission.template_key == "intake",
        )
    )
    submission.created_at = datetime.now(timezone.utc) - timedelta(days=submitted_days_ago)
    await db_session.commit()
    return case_id


def _usage_log(case_id: int, scenario: str, *, success: bool = True) -> AIUsageLog:
    return AIUsageLog(
        service_case_id=case_id,
        scenario=scenario,
        model="deepseek-v4-pro",
        success=success,
        error_type=None if success else "RetryableIntakePackageError",
    )


async def _document_types(db_session, case_id: int) -> set[DocumentType]:
    documents = list(
        await db_session.scalars(
            select(UserDocument).where(UserDocument.service_case_id == case_id)
        )
    )
    return {doc.document_type for doc in documents}


@pytest.mark.asyncio
async def test_content_failure_is_retried_by_the_sweep_but_only_once_a_day(
    admin_client, create_case, db_session, mock_ai, cai_haoran_answers
):
    """文档模型真跑过、内容失败的单子要被补跑；但同一天只补跑一次，避免反复烧钱。"""
    mock_ai._responses = {INTAKE_SYSTEM_PROMPT_PREFIX: _make_intake_mock_response()}
    case_id = await _case_missing_documents(
        admin_client, create_case, db_session, cai_haoran_answers,
        tag="contentfail", submitted_days_ago=0.5,
    )
    # 有 intake_doc 记录 = 文档模型被调用过、栽在内容上。
    db_session.add(_usage_log(case_id, "intake_safety_factors"))
    db_session.add(_usage_log(case_id, "intake_doc", success=False))
    await db_session.commit()

    await ReminderService().check_stuck_document_generation_once()

    assert await _document_types(db_session, case_id) == {
        DocumentType.ENERGY_RESTART_PRESCRIPTION,
        DocumentType.DAY28_EXECUTION_STANDARD,
    }, "内容失败的单子必须能自动补跑出交付文件"

    # 天闸：同一天再扫也不该再调一次文档模型。
    for document in list(
        await db_session.scalars(
            select(UserDocument).where(UserDocument.service_case_id == case_id)
        )
    ):
        await db_session.delete(document)
    await db_session.commit()
    calls_before = len([c for c in mock_ai.calls if c.get("scenario") == "intake_doc"])

    await ReminderService().check_stuck_document_generation_once()

    calls_after = len([c for c in mock_ai.calls if c.get("scenario") == "intake_doc"])
    assert calls_after == calls_before, "同一天不能重复补跑"
    assert await _document_types(db_session, case_id) == set()


@pytest.mark.asyncio
async def test_deterministically_blocked_case_is_never_retried(
    admin_client, create_case, db_session, mock_ai, cai_haoran_answers
):
    """资料不全在调文档模型之前就被拦下(只有 intake_safety_factors 记录)，永不自动补跑。

    这类失败重试一万次结果一样，旧规则把它和内容失败混为一类正是问题所在——但放开重试
    时也不能把它一起放开，否则每轮都白烧一次模型调用。
    """
    mock_ai._responses = {INTAKE_SYSTEM_PROMPT_PREFIX: _make_intake_mock_response()}
    case_id = await _case_missing_documents(
        admin_client, create_case, db_session, cai_haoran_answers,
        tag="blocked", submitted_days_ago=0.5,
    )
    db_session.add(_usage_log(case_id, "intake_safety_factors"))
    await db_session.commit()

    await ReminderService().check_stuck_document_generation_once()

    assert await _document_types(db_session, case_id) == set()
    assert [c for c in mock_ai.calls if c.get("scenario") == "intake_doc"] == []


@pytest.mark.asyncio
async def test_content_failure_stops_being_retried_after_the_window(
    admin_client, create_case, db_session, mock_ai, cai_haoran_answers
):
    """超过重试窗口就交给运营，不再无限自动补跑。"""
    mock_ai._responses = {INTAKE_SYSTEM_PROMPT_PREFIX: _make_intake_mock_response()}
    case_id = await _case_missing_documents(
        admin_client, create_case, db_session, cai_haoran_answers,
        tag="stale", submitted_days_ago=INTAKE_DOCUMENT_RETRY_WINDOW_DAYS + 1,
    )
    db_session.add(_usage_log(case_id, "intake_doc", success=False))
    await db_session.commit()

    await ReminderService().check_stuck_document_generation_once()

    assert await _document_types(db_session, case_id) == set()
    assert [c for c in mock_ai.calls if c.get("scenario") == "intake_doc"] == []
