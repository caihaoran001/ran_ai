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

import pytest
from sqlalchemy import select

from app.models.service_case import ServiceCase, ServiceCaseStatus
from app.models.user_document import UserDocument
from app.services.intake_generation import INTAKE_DOCUMENT_MAX_ATTEMPTS

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
