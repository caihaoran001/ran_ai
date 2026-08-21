from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.models.population_insight import PopulationInsightSnapshot
from app.models.service_case import ServiceCase, ServiceCaseGroupType, ServiceCaseStatus
from app.models.user import User
from app.models.user_profile import UserProfile
from app.schemas.dashboard import PopulationInsightContent
from app.services.ai_service.client import AIService
from app.services.population_insight import (
    POPULATION_INSIGHT_PROMPT_VERSION,
    get_population_insight,
    population_insight_refresh_reason,
    refresh_population_insight,
)
from app.services.population_scope import select_population_service_cases
from app.services.reminder_service import ReminderService


INSIGHT_CONTENT = {
    "headline": "不同健康起点指向同一个长期管理任务",
    "summary": "用户的表层原因不同，但都希望建立可持续的健康管理方式。",
    "common_jtbd": {
        "when": "当我意识到当前状态可能影响未来健康时",
        "want_to": "我希望建立能够长期坚持的健康管理方式",
        "so_i_can": "从而维持稳定状态并降低未来健康风险",
    },
    "segments": [
        {
            "name": "健康风险管理型",
            "entry_point": "已有健康风险或身体负担",
            "deeper_goal": "降低长期风险并稳定状态",
            "evidence_basis": "画像中的健康风险与长期目标",
        },
        {
            "name": "主动健康投资型",
            "entry_point": "主动改善精力和生活状态",
            "deeper_goal": "维持更高的长期生活质量",
            "evidence_basis": "画像中的精力与长期状态诉求",
        },
    ],
    "shared_patterns": ["关注长期可坚持，而不只是短期体重数字"],
    "product_implications": ["产品价值可能向长期健康管理能力延伸，值得继续验证"],
    "evidence_limits": ["结论仅基于当前系统内可用画像，不代表外部市场趋势"],
}


class CapturingInsightAI:
    def __init__(self):
        self.calls: list[dict] = []

    def _model_for(self, scenario: str) -> str:
        assert scenario == "population_insight"
        return "document-model-test"

    async def generate_text(self, **kwargs):
        self.calls.append(kwargs)
        return json.dumps(INSIGHT_CONTENT, ensure_ascii=False)


async def _add_profiled_case(db_session, *, name: str, phone: str, customer_type: str):
    tag = uuid4().hex[:8]
    user = User(name=name, phone=phone)
    db_session.add(user)
    await db_session.flush()
    service_case = ServiceCase(
        case_no=f"INSIGHT-{tag}",
        user_id=user.id,
        group_id=f"insight-group-{tag}",
        questionnaire_token=f"insight-token-{tag}",
        status=ServiceCaseStatus.IN_SERVICE,
        group_type=ServiceCaseGroupType.NEW_GROUP.value,
    )
    db_session.add(service_case)
    await db_session.flush()
    db_session.add(
        UserProfile(
            service_case_id=service_case.id,
            profile_json={
                "customer_type": customer_type,
                "main_problem": f"{name}想改善长期状态",
                "day28_goal": "先把精力和饮食节律稳定下来",
                "need_analysis": {
                    "surface_needs": ["减重", "改善精力"],
                    "deep_needs": ["建立可长期坚持的健康方式"],
                    "priority_need": f"稳定状态，联系号码 {phone}",
                },
                "risks": ["代谢风险待持续关注"],
            },
            summary=f"{name}的入营画像",
            generated_by_model="profile-model-test",
        )
    )
    await db_session.flush()
    return service_case


@pytest.mark.asyncio
async def test_population_insight_refresh_uses_one_anonymized_model_call_and_persists_snapshot(
    db_session,
):
    await _add_profiled_case(
        db_session,
        name="洞察测试甲",
        phone="13912345678",
        customer_type="疾病管理型",
    )
    await _add_profiled_case(
        db_session,
        name="洞察测试乙",
        phone="13887654321",
        customer_type="健康投资型",
    )
    ai = CapturingInsightAI()

    result = await refresh_population_insight(db_session, force=True, ai_service=ai)

    assert result.ran is True
    assert result.reason == "manual"
    assert len(ai.calls) == 1
    assert ai.calls[0]["scenario"] == "population_insight"
    prompt = ai.calls[0]["user_prompt"]
    assert "洞察测试甲" not in prompt
    assert "洞察测试乙" not in prompt
    assert "13912345678" not in prompt
    assert "13887654321" not in prompt
    assert "P001" in prompt

    response = await get_population_insight(db_session)
    assert response.status == "ready"
    assert response.content is not None
    assert response.content.common_jtbd.want_to.startswith("我希望")
    assert response.generated_by_model == "document-model-test"
    assert response.profiled_users >= 2
    assert response.model_input_users >= 2


@pytest.mark.asyncio
async def test_population_insight_excludes_refunded_cases(db_session):
    """共同洞察分析的是「当前该服务谁」，已退款的单必须排除（与画像/看板的含退款口径区分开）。"""

    in_service_a = await _add_profiled_case(
        db_session,
        name="在服甲",
        phone="13700000005",
        customer_type="疾病管理型",
    )
    in_service_b = await _add_profiled_case(
        db_session,
        name="在服乙",
        phone="13700000006",
        customer_type="健康投资型",
    )
    # 分型串刻意取得独一无二：姓名/手机号本来就会被匿名化抹掉，拿它们断言等于没断言，
    # 只有这种会原样进提示词的字段才能证明退款单真的没被喂给模型。
    refunded = await _add_profiled_case(
        db_session,
        name="退款丙",
        phone="13700000007",
        customer_type="仅退款样本分型",
    )
    refunded.status = ServiceCaseStatus.REFUNDED
    await db_session.flush()

    insight_cases = await select_population_service_cases(db_session, exclude_refunded=True)
    insight_case_ids = {c.id for c in insight_cases}
    assert refunded.id not in insight_case_ids
    assert in_service_a.id in insight_case_ids
    assert in_service_b.id in insight_case_ids

    ai = CapturingInsightAI()
    result = await refresh_population_insight(db_session, force=True, ai_service=ai)

    assert result.ran is True
    prompt = ai.calls[0]["user_prompt"]
    payload = json.loads(prompt.split("\n", 1)[1])
    assert payload["sample"]["included_users"] == len(insight_case_ids)
    assert "仅退款样本分型" not in prompt
    assert "疾病管理型" in prompt
    assert "健康投资型" in prompt


@pytest.mark.asyncio
async def test_population_insight_dashboard_endpoints_read_cache_and_refresh(
    admin_client,
    db_session,
):
    await _add_profiled_case(
        db_session,
        name="端点测试甲",
        phone="13700000001",
        customer_type="疾病管理型",
    )
    await _add_profiled_case(
        db_session,
        name="端点测试乙",
        phone="13700000002",
        customer_type="健康投资型",
    )

    before = await admin_client.get("/dashboard/population-insight")
    assert before.status_code == 200
    assert before.json()["status"] == "empty"

    refreshed = await admin_client.post("/dashboard/population-insight/refresh", json={})
    assert refreshed.status_code == 200
    assert refreshed.json()["status"] == "ready"
    assert refreshed.json()["content"]["common_jtbd"]["want_to"].startswith("我希望")

    cached = await admin_client.get("/dashboard/population-insight")
    assert cached.status_code == 200
    assert cached.json()["generated_at"] == refreshed.json()["generated_at"]


def test_population_insight_auto_refresh_ignores_daily_profile_churn_but_respects_week_and_new_users():
    now = datetime(2026, 7, 20, 3, tzinfo=timezone.utc)
    latest = PopulationInsightSnapshot(
        scope_key="all_non_deleted_non_refunded",
        status="ready",
        content_json=INSIGHT_CONTENT,
        source_case_ids=list(range(1, 101)),
        included_users=100,
        profiled_users=90,
        model_input_users=90,
        prompt_version=POPULATION_INSIGHT_PROMPT_VERSION,
        generated_by_model="doc-model",
        refresh_reason="weekly",
        started_at=now - timedelta(days=6),
        generated_at=now - timedelta(days=6),
    )

    assert population_insight_refresh_reason(
        latest_ready=latest,
        current_case_ids=list(range(1, 101)),
        now=now,
    ) is None
    assert population_insight_refresh_reason(
        latest_ready=latest,
        current_case_ids=list(range(1, 106)),
        now=now,
    ) == "new_users"
    assert population_insight_refresh_reason(
        latest_ready=latest,
        current_case_ids=list(range(1, 101)),
        now=now + timedelta(days=1),
    ) == "weekly"


@pytest.mark.asyncio
async def test_population_insight_failed_run_keeps_previous_ready_content(db_session):
    now = datetime.now(timezone.utc)
    db_session.add_all(
        [
            PopulationInsightSnapshot(
                scope_key="all_non_deleted_non_refunded",
                status="ready",
                content_json=PopulationInsightContent.model_validate(INSIGHT_CONTENT).model_dump(mode="json"),
                source_case_ids=[1, 2],
                included_users=2,
                profiled_users=2,
                model_input_users=2,
                prompt_version=POPULATION_INSIGHT_PROMPT_VERSION,
                generated_by_model="doc-model",
                refresh_reason="initial",
                started_at=now - timedelta(days=1),
                generated_at=now - timedelta(days=1),
            ),
            PopulationInsightSnapshot(
                scope_key="all_non_deleted_non_refunded",
                status="failed",
                content_json=None,
                source_case_ids=[1, 2],
                included_users=2,
                profiled_users=2,
                model_input_users=2,
                prompt_version=POPULATION_INSIGHT_PROMPT_VERSION,
                generated_by_model=None,
                refresh_reason="manual",
                started_at=now,
                generated_at=None,
                failure_reason="RuntimeError: upstream unavailable",
            ),
        ]
    )
    await db_session.flush()

    response = await get_population_insight(db_session)

    assert response.status == "failed"
    assert response.last_refresh_failed is True
    assert response.content is not None
    assert response.content.headline == INSIGHT_CONTENT["headline"]


def test_population_insight_uses_document_model_route():
    settings = Settings(
        AI_API_KEY="test",
        AI_MODEL="chat-model",
        AI_DOCUMENT_MODEL="document-model",
    )
    service = object.__new__(AIService)
    service.settings = settings
    assert service._model_for("population_insight") == "document-model"


@pytest.mark.asyncio
async def test_population_insight_scheduler_checks_once_after_configured_hour(monkeypatch):
    calls: list[datetime] = []

    async def fake_refresh(_session, *, now):
        calls.append(now)
        return SimpleNamespace(ran=False, reason="not_due", snapshot_id=None)

    monkeypatch.setattr("app.services.population_insight.refresh_population_insight", fake_refresh)
    service = ReminderService(
        Settings(
            POPULATION_INSIGHT_ENABLED=True,
            POPULATION_INSIGHT_AUTO_HOUR=3,
            REMINDER_TIMEZONE="Asia/Shanghai",
        )
    )

    await service.check_population_insight_once(
        datetime(2026, 7, 20, 2, 59, tzinfo=timezone(timedelta(hours=8)))
    )
    await service.check_population_insight_once(
        datetime(2026, 7, 20, 3, 1, tzinfo=timezone(timedelta(hours=8)))
    )
    await service.check_population_insight_once(
        datetime(2026, 7, 20, 10, 0, tzinfo=timezone(timedelta(hours=8)))
    )

    assert len(calls) == 1
