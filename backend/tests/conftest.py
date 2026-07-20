import nest_asyncio
nest_asyncio.apply()

import json
import os
os.environ["DOCUMENT_OUTPUT_DIR"] = os.path.join(os.path.dirname(os.path.abspath(__file__)), "generated_documents")
import sys
import asyncio

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# Patch SQLAlchemy to use NullPool in tests to avoid asyncpg loop safety issues
from sqlalchemy.pool import NullPool
import sqlalchemy.ext.asyncio
orig_create_async_engine = sqlalchemy.ext.asyncio.create_async_engine
def mock_create_async_engine(*args, **kwargs):
    kwargs["poolclass"] = NullPool
    kwargs.pop("pool_pre_ping", None)
    return orig_create_async_engine(*args, **kwargs)
sqlalchemy.ext.asyncio.create_async_engine = mock_create_async_engine


from app.core.config import get_settings
from app.models.ai_usage import AIUsageLog
from app.models.checkin import Checkin
from app.models.message import Message, MessageDirection, MessageSenderType, MessageStatus

_SETTINGS = get_settings()

@pytest.fixture(scope="session")
def project_root():
    return Path(__file__).resolve().parents[1]






@pytest.fixture(autouse=True)
def mock_message_channel():
    from app.services.channels.registry import set_current_channel
    from app.services.channels.base import MessageChannel, ChannelSendResult

    class TestMessageChannel(MessageChannel):
        async def send_text(
            self, chat_id: str, content: str, *, reply_to: str | None = None, **kwargs
        ) -> ChannelSendResult:
            return ChannelSendResult(success=True, message_id="mock-text-id")

        async def send_document(
            self,
            chat_id: str,
            file_path: str,
            caption: str | None = None,
            *,
            reply_to: str | None = None,
            **kwargs,
        ) -> ChannelSendResult:
            return ChannelSendResult(success=True, message_id="mock-doc-id")

    channel = TestMessageChannel()
    set_current_channel(channel)
    yield channel
    set_current_channel(None)


_SPAWNED_TASKS = []

@pytest.fixture(autouse=True)
def patch_background_tasks():
    global _SPAWNED_TASKS
    _SPAWNED_TASKS.clear()
    
    from fastapi import BackgroundTasks
    orig_add_task = BackgroundTasks.add_task
    
    def mock_add_task(self, func, *args, **kwargs):
        _SPAWNED_TASKS.append((func, args, kwargs))
        
    BackgroundTasks.add_task = mock_add_task
    yield
    BackgroundTasks.add_task = orig_add_task


@pytest_asyncio.fixture(autouse=True)
async def wait_for_spawned_tasks(patch_background_tasks):
    yield
    while _SPAWNED_TASKS:
        tasks_to_run = list(_SPAWNED_TASKS)
        _SPAWNED_TASKS.clear()
        for func, args, kwargs in tasks_to_run:
            try:
                await func(*args, **kwargs)
            except Exception:
                pass


@pytest_asyncio.fixture
async def wait_background():
    async def _wait():
        while _SPAWNED_TASKS:
            tasks_to_run = list(_SPAWNED_TASKS)
            _SPAWNED_TASKS.clear()
            for func, args, kwargs in tasks_to_run:
                await func(*args, **kwargs)
    return _wait



@pytest.fixture(scope="session")
def cai_haoran_answers():
    return {
        "SIMPLE1774103358253mn0fc08d": "蔡浩然",
        "SIMPLE1774103429810mn0fdjg2": "15642876967",
        "SIMPLE1774103480574mn0femm6": "NAR",
        "RADIO1774103553031mn0fg6ix": "男",
        "SIMPLE1774103589114mn0fgyd6": "30",
        "SIMPLE1774103860160mn0fmri8": "杭州",
        "RADIO1774103904301mn0fnpkf": "晚上20-22点之间",
        "SIMPLE1774104038286mn0fqky6": "175",
        "SIMPLE1774104087629mn0frn0t": "65",
        "SIMPLE1774104132462mn0fslm6": "暂未测量",
        "SIMPLE1774104236120mn0futlk": "暂未测量",
        "RADIO1774104297253mn0fw4rr": "基本稳定",
        "CHECKBOX1774104416332mn0fyoni": "情绪不稳、压力一大就容易乱",
        "CHECKBOX1774104667027mn0g4239": "每天节律更稳定、情绪更稳",
        "CHECKBOX1774104915668mn0g9dxy": "执行几天就疲了，做事情没毅力",
        "RADIO1774105107178mn0gdhpo": "固定白班",
        "RADIO1774105241706mn0ggdik": "23:30–00:30",
        "RADIO1774105303177mn0ghoy4": "8:00–9:00",
        "RADIO1774105374228mn0gj7rr": "6–7天",
        "RADIO1774105428032mn0gkdab": "19:00–20:00",
        "CHECKBOX1774105613109mn0goc3b": "公司食堂 / 学校食堂",
        "CHECKBOX1774105760075mn0grhhp": "晚餐后就不吃了",
        "RADIO1774105949628mn0gvjr2": "总体还算规律，三餐基本按时吃",
        "RADIO1774106134803mn0gzimv": "都差不多",
        "CHECKBOX1774106190188mn0h0pda": "饭后容易困",
        "RADIO1774106329171mn0h3olx": "基本每天吃",
        "RADIO1774106385794mn0h4was": "提神明显，但稍微容易心慌",
        "RADIO1774106601945mn0h9j2z": "状态一般，勉强够用",
        "CHECKBOX1774106739991mn0hchln": "一忙就上头，一停下来就跨",
        "RADIO1774106869788mn0hf9r2": "压力有点明显了，但还在应对能力范围内",
        "CHECKBOX1774107036716mn0hiujy": "急躁、没耐心、容易发脾气",
        "RADIO1774107228737mn0hmypv": "还可以",
        "CHECKBOX1774107333066mn0hp77x": "睡眠时间很不固定",
        "RADIO1774107460930mn0hrxvo": "基本不会夜醒",
        "RADIO1774107574262mn0hudbt": "可以很快清醒",
        "RADIO1774107759627mn0hycct": "1–2次",
        "CHECKBOX1774107823025mn0hzp9v": "散步/快走",
        "CHECKBOX1774107925408mn0i1w9u": "从能量和白天状态先改",
        "SIMPLE1774108071693mn0i515a": "无",
        "FILE1774108153106mn0i6ryq": "后续补充",
        "RADIO1774171736563mn1k1l9x": "通常占半盘左右/一整饭碗",
        "RADIO1774171867362mn1k4e78": "大多数正餐有蛋白质，但不一定够",
        "RADIO1774172055667mn1k8fhy": "会吃一点，但通常不够半盘",
        "RADIO1774172195394mn1kbfb8": "极少",
        "RADIO1774172399733mn1kfszi": "很少吃",
        "RADIO1774172399734mn1kfszr": "鸡蛋、肉、奶酪、坚果、无糖酸奶这类",
        "RADIO1774172399735mn1kft01": "我主食偏多，是典型高碳饮食",
        "RADIO1774173217404mn1kxbwh": "中等匀称，代谢快，容易饿",
        "RADIO1774173217404mn1kxbwn": "容易怕热，天热更不舒服",
        "RADIO1774173217405mn1kxbwt": "饿得快，不按时吃饭会明显难受",
        "RADIO1774173217405mn1kxbwz": "消化快，偶尔容易有烧心或胃里发热感",
        "RADIO1774173217405mn1kxbx5": "皮肤偏干，嘴唇也容易干",
        "RADIO1774173217405mn1kxbxb": "睡得还可以，但压力大时容易烦躁",
        "RADIO1774173217405mn1kxbxh": "精力来得快、掉得也快",
        "RADIO1774173217405mn1kxbxn": "稳、慢、有耐心，不急不躁",
        "RADIO1774173217406mn1kxbxt": "容易急、容易上火，也容易不耐烦",
        "RADIO1774173217406mn1kxbxz": "记得慢一些，但记住后较稳",
        "RADIO1774173217406mn1kxby5": "会烦躁、着急，想尽快解决",
        "RADIO1774173217406mn1kxbyb": "事情一多就容易过度用力",
        "SIMPLE1774699899282mnaahy8j": "线下",
        "RADIO1774108047588mn0i4ijq": "没有",
    }


class MockAIService:
    def __init__(self, responses=None):
        self._responses = responses or {}
        self.settings = get_settings()

    def _model_for(self, scenario: str) -> str:
        # 与真实 AIService 接口对齐：按场景返回模型名（mock 不区分，回退默认）
        if scenario == "intervention_risk" and (getattr(self.settings, "ai_risk_model", "") or "").strip():
            return self.settings.ai_risk_model.strip()
        if scenario in {"intake_doc", "stage_report", "population_insight"} and (
            getattr(self.settings, "ai_document_model", "") or ""
        ).strip():
            return self.settings.ai_document_model.strip()
        return self.settings.ai_model

    def _match(self, system_prompt):
        for prefix, response in self._responses.items():
            if system_prompt.startswith(prefix):
                return response
        return None

    async def generate_text(self, *, system_prompt, user_prompt, **kwargs):
        matched = self._match(system_prompt)
        if matched is not None:
            return matched
        if "提炼出可用于标准知识检索的 QA 问答对" in system_prompt:
            return json.dumps(
                {
                    "candidates": [
                        {
                            "draft_type": "qa",
                            "title": "平台期情绪安抚",
                            "question": "体重暂时不变化时，应该怎么稳定情绪并继续执行？",
                            "answer": "先别急，短期数字不代表整段趋势。你已经在坚持了，今天先只守住一个最小动作：晚餐后不加餐。顺便回看下昨晚睡眠和今天排便怎么样。",
                            "category": "心理焦虑",
                            "tags": ["平台期", "情绪安抚", "继续执行"],
                            "reason": "适合沉淀为高频焦虑场景下的标准答复。",
                            "evidence": "怎么一点都不掉，我有点急了。",
                            "source_message_ids": [1, 2],
                        }
                    ]
                },
                ensure_ascii=False,
            )
        if "提炼出可作为优秀案例展示的用户故事片段" in system_prompt:
            return json.dumps(
                {
                    "candidates": [
                        {
                            "draft_type": "case",
                            "title": "焦虑后稳住执行",
                            "question": "用户在平台期焦虑，但没有放弃，继续守住节律。",
                            "answer": "这位用户在短期不掉秤时没有退出，而是继续按节律完成早C/午B/晚A，并逐步把晚间加餐收住，后续状态更稳。",
                            "category": "情绪转正",
                            "tags": ["平台期", "坚持执行", "节律稳定"],
                            "reason": "能用于安抚同类焦虑用户，强调连续性比单日数字更重要。",
                            "evidence": "怎么一点都不掉，我有点急了。",
                            "source_message_ids": [1, 2],
                        }
                    ]
                },
                ensure_ascii=False,
            )
        if "用户研究分析师" in system_prompt and "common_jtbd" in system_prompt:
            return json.dumps(
                {
                    "headline": "不同健康起点指向同一个长期管理任务",
                    "summary": "用户进入服务的表层原因不同，但都在寻找能够长期坚持的健康管理方式。",
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
                            "evidence_basis": "画像中的精力、节律与长期状态诉求",
                        },
                    ],
                    "shared_patterns": ["关注长期可坚持，而不只是短期体重数字"],
                    "product_implications": ["产品价值可能正向长期健康管理能力延伸，值得继续验证"],
                    "evidence_limits": ["结论仅基于当前系统内可用画像，不代表外部市场趋势"],
                },
                ensure_ascii=False,
            )
        return '{"summary":"mock","sections":[]}'

    async def extract_intervention_insight(self, *, source, content, context=None):
        text = (content or "").lower()
        if any(kw in text for kw in ("崩溃", "撑不住", "想死", "抑郁")):
            return _risk_insight("depression", "情绪危机", content[:80], source)
        if any(kw in text for kw in ("高血压", "糖尿病", "降压药", "胰岛素", "甲状腺", "怀孕", "哺乳期")):
            return _risk_insight("chronic_disease", "健康高风险", content[:80], source)
        if any(kw in text for kw in ("退款", "不想继续", "退钱", "效果不好想退")):
            return _intent_insight("refund", content[:80], source)
        if any(kw in text for kw in ("续一期", "还想再来", "想继续")):
            return _intent_insight("renewal", content[:80], source)
        return _normal_insight()

    async def extract_outcome_insight(self, *, content, context):
        text = content or ""
        missing = list((context or {}).get("missing_fields") or [])
        followup_context = (context or {}).get("followup_time_context")
        allowed_followup = None
        if isinstance(followup_context, dict) and isinstance(followup_context.get("allowed_field_keys"), list):
            allowed_followup = set(followup_context.get("allowed_field_keys") or [])
        events = []
        profile_signals = []
        if "漂流" in text or "户外" in text:
            profile_signals.append(
                {
                    "domain": "personal_preferences",
                    "signal_type": "activity_preference",
                    "value": "喜欢户外和漂流",
                    "evidence_text": text[:80],
                    "confidence": 0.9,
                }
            )
        if "长期不吃晚饭" in text or "平时基本不吃晚饭" in text:
            profile_signals.append(
                {
                    "domain": "lifestyle_habits",
                    "signal_type": "meal_pattern",
                    "value": "长期不吃晚饭",
                    "evidence_text": text[:80],
                    "confidence": 0.9,
                }
            )
        if "没那么困" in text or "精神" in text:
            events.append(
                {
                    "field_key": "energy_change" if "energy_change" in missing else (missing[0] if missing else "energy_change"),
                    "event_type": "improved",
                    "value": "用户反馈白天精神状态有改善",
                    "evidence_text": text[:80],
                    "confidence": 0.9,
                }
            )
        if "睡" in text:
            events.append(
                {
                    "field_key": "sleep_change" if "sleep_change" in missing else (missing[0] if missing else "sleep_change"),
                    "event_type": "feedback",
                    "value": "用户反馈睡眠变化",
                    "evidence_text": text[:80],
                    "confidence": 0.82,
                }
            )
        followup_candidates = [
            item
            for item in missing
            if item not in {event["field_key"] for event in events}
            and (allowed_followup is None or item in allowed_followup)
        ]
        followup_field = next(iter(followup_candidates), "")
        return {
            "outcome_events": events,
            "profile_signals": profile_signals,
            "followup_plan": {
                "should_ask": bool(followup_field),
                "field_key": followup_field,
                "question": "这两天睡眠有没有也跟着稳一点？" if followup_field else "",
                "reason": "当前阶段成效证据还缺一项自然反馈。",
            },
            "notes": "mock outcome",
        }

    async def close(self):
        pass


def _risk_insight(code, reason, evidence, source):
    return {
        "requires_human_intervention": True,
        "risk_flags": [{"code": code, "level": "high", "reason": reason, "evidence": evidence, "source": source}],
        "intervention_reasons": [],
        "renewal_intent": {"detected": False},
        "refund_intent": {"detected": False},
        "emotion_signal": {"detected": True, "level": "high", "reason": reason, "evidence": evidence},
        "confidence": 0.9,
    }


def _intent_insight(kind, evidence, source):
    return {
        "requires_human_intervention": False,
        "risk_flags": [],
        "intervention_reasons": [],
        "renewal_intent": {"detected": kind == "renewal", "reason": f"{kind}意图", "evidence": evidence, "source": source},
        "refund_intent": {"detected": kind == "refund", "reason": f"{kind}意图", "evidence": evidence, "source": source},
        "emotion_signal": {"detected": False, "level": "low"},
        "confidence": 0.85,
    }


def _normal_insight():
    return {
        "requires_human_intervention": False,
        "risk_flags": [],
        "intervention_reasons": [],
        "renewal_intent": {"detected": False},
        "refund_intent": {"detected": False},
        "emotion_signal": {"detected": False, "level": "low"},
        "confidence": 0.9,
    }


def _make_test_app() -> FastAPI:
    from app.api.router import api_router
    from app.core.config import Settings
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse
    import secrets

    s = get_settings()
    app = FastAPI(title=s.app_name)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_router, prefix=s.api_prefix)
    return app


@pytest_asyncio.fixture
async def mock_ai():
    mock = MockAIService()
    import app.services.ai_service.client as client_mod
    import app.services.document_service as doc_mod
    import app.services.knowledge_evolution as evolution_mod
    import app.services.ranren_ai.engine as engine_mod
    import app.services.intervention_service as intervention_mod
    import app.services.population_insight as population_insight_mod

    orig_client = client_mod.AIService
    orig_doc = doc_mod.AIService
    orig_evolution = evolution_mod.AIService
    orig_engine = engine_mod.AIService
    orig_intervention = intervention_mod.AIService
    orig_population_insight = population_insight_mod.AIService

    client_mod.AIService = lambda *a, **kw: mock
    doc_mod.AIService = lambda *a, **kw: mock
    evolution_mod.AIService = lambda *a, **kw: mock
    engine_mod.AIService = lambda *a, **kw: mock
    intervention_mod.AIService = lambda *a, **kw: mock
    population_insight_mod.AIService = lambda *a, **kw: mock

    yield mock

    client_mod.AIService = orig_client
    doc_mod.AIService = orig_doc
    evolution_mod.AIService = orig_evolution
    engine_mod.AIService = orig_engine
    intervention_mod.AIService = orig_intervention
    population_insight_mod.AIService = orig_population_insight


@pytest_asyncio.fixture(loop_scope="session")
async def db_session():
    from app.db.session import SessionLocal

    async with SessionLocal() as session:
        trans = await session.begin()
        
        orig_commit = session.commit
        async def mock_commit():
            await session.flush()
        session.commit = mock_commit

        orig_rollback = session.rollback
        async def mock_rollback():
            pass
        session.rollback = mock_rollback

        session.info["test_trans"] = trans
        session.info["orig_commit"] = orig_commit
        session.info["orig_rollback"] = orig_rollback

        yield session
        
        session.commit = orig_commit
        session.rollback = orig_rollback
        try:
            await trans.rollback()
        except Exception:
            pass


@pytest_asyncio.fixture(autouse=True)
async def mock_session_local(db_session):
    import sys
    import importlib
    import app.db.session

    orig_session_local = app.db.session.SessionLocal

    class MockSessionContext:
        def __init__(self, s):
            self.s = s

        async def __aenter__(self):
            return self.s

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            if exc_type is None:
                await self.s.flush()
            return False

        def __getattr__(self, name):
            return getattr(self.s, name)

    mock_creator = lambda *a, **kw: MockSessionContext(db_session)
    app.db.session.SessionLocal = mock_creator

    modules_to_patch = [
        "app.api.routes.questionnaires",
        "app.api.routes.documents",
        "app.services.ranren_ai.engine",
        "app.services.qiwe_service.inbox",
        "app.services.agent.tools.reminder_tools",
        "app.services.agent.tools.service_case_tools",
        "app.services.agent.tools.followup_preference_tools",
        "app.services.agent.tools.service_lifecycle_tools",
        "app.services.agent.tools.knowledge_tools",
        "app.services.reminder_service",
        "app.services.archivist.case_memory_service",
        "app.services.archivist.case_synthesis",
        "app.services.archivist.trigger",
    ]
    for mod_name in modules_to_patch:
        try:
            mod = importlib.import_module(mod_name)
            if hasattr(mod, "SessionLocal"):
                setattr(mod, "SessionLocal", mock_creator)
        except Exception:
            pass

    yield

    app.db.session.SessionLocal = orig_session_local
    for mod_name in modules_to_patch:
        try:
            mod = importlib.import_module(mod_name)
            if hasattr(mod, "SessionLocal"):
                setattr(mod, "SessionLocal", orig_session_local)
        except Exception:
            pass


@pytest_asyncio.fixture
async def admin_client(mock_ai, db_session):
    app = _make_test_app()
    
    from app.db.session import get_session
    async def override_get_session():
        yield db_session

    app.dependency_overrides[get_session] = override_get_session

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test/api",
        headers={"x-admin-api-key": "change-me-internal-api-key"},
    ) as ac:
        yield ac


@pytest_asyncio.fixture
async def create_case(db_session):
    """测试造数据用：直接建 User+ServiceCase 入库。

    生产已移除手动新建服务单的 HTTP 端点（服务单只由群内入营问卷指令自动创建），
    此 fixture 复刻原端点的默认值行为，供测试快速铺设服务单，返回带 .status_code /
    .json() 的轻量响应对象以贴合原有断言写法。
    """
    import secrets as _secrets
    from datetime import datetime as _dt

    from app.models.service_case import ServiceCase
    from app.models.user import User

    class _FakeResp:
        def __init__(self, data: dict):
            self._data = data
            self.status_code = 201
            self.text = ""

        def json(self) -> dict:
            return self._data

    async def _create(payload: dict) -> _FakeResp:
        user_data = dict(payload.get("user") or {})
        user_data["name"] = (user_data.get("name") or "").strip() or "待填写问卷用户"
        user = User(**user_data)
        db_session.add(user)
        await db_session.flush()

        case_no = payload.get("case_no") or (
            f"RAI-{_dt.now().strftime('%Y%m%d')}-{_secrets.token_hex(3).upper()}"
        )
        service_case = ServiceCase(
            case_no=case_no,
            user_id=user.id,
            group_id=payload["group_id"],
            questionnaire_token=_secrets.token_urlsafe(24),
            notes=payload.get("notes"),
        )
        db_session.add(service_case)
        await db_session.flush()
        return _FakeResp(
            {
                "id": service_case.id,
                "case_no": service_case.case_no,
                "user_id": service_case.user_id,
                "group_id": service_case.group_id,
                "questionnaire_token": service_case.questionnaire_token,
                "status": service_case.status.value,
                "ai_auto_reply_enabled": service_case.ai_auto_reply_enabled,
                "auto_send_documents": service_case.auto_send_documents,
            }
        )

    return _create


@pytest_asyncio.fixture
async def public_client(mock_ai, db_session):
    app = _make_test_app()
    
    from app.db.session import get_session
    async def override_get_session():
        yield db_session

    app.dependency_overrides[get_session] = override_get_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test/api") as ac:
        yield ac


@pytest_asyncio.fixture(autouse=True)
async def _dispose_engine_after_test():
    yield
    # 已改用 NullPool，无需且严禁在 function 级 teardown 重复销毁全局单例 engine 以防止事件循环冲突
    pass




async def seed_checkins(session, service_case_id, group_id, user_id, days=28):
    from tests.fixtures.chat_scenarios import CHECKIN_SEQUENCE

    for entry in CHECKIN_SEQUENCE:
        day = entry["day"]
        if day > days:
            break
        checkin_date = date.today() - timedelta(days=29 - day)
        parsed = {
            "weight": entry["weight"],
            "water": entry["water"],
            "sleep": entry["sleep"],
            "mood": entry["mood"],
            "exercise": entry["exercise"],
        }
        session.add(
            Checkin(
                service_case_id=service_case_id,
                group_id=group_id,
                user_id=user_id,
                checkin_date=checkin_date,
                content=f"打卡D{day}: wt{entry['weight']}",
                parsed_json=parsed,
            )
        )
    await session.flush()


async def seed_outcome_event(
    session,
    service_case_id,
    *,
    field_key="weight_change",
    event_type="improved",
    milestone="day7",
    value_text="瘦了2斤",
):
    from app.models.outcome import OutcomeEvent

    event = OutcomeEvent(
        service_case_id=service_case_id,
        milestone=milestone,
        field_key=field_key,
        event_type=event_type,
        value_text=value_text,
        evidence_text="这周瘦了2斤",
        confidence=0.9,
        source="archivist",
    )
    session.add(event)
    await session.flush()
    return event


async def seed_chat_messages(session, service_case_id, group_id):
    from tests.fixtures.chat_scenarios import ALL_CHAT_MESSAGES

    for msg in ALL_CHAT_MESSAGES:
        created_at = datetime.now(timezone.utc) - timedelta(days=29 - msg.day, hours=6)
        session.add(
            Message(
                service_case_id=service_case_id,
                group_id=group_id,
                direction=MessageDirection.INBOUND,
                sender_type=MessageSenderType.USER,
                message_type="text",
                content=msg.content,
                status=MessageStatus.SENT,
                created_at=created_at,
            )
        )
    await session.flush()
