import nest_asyncio
nest_asyncio.apply()

import json
import os
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
        async def send_text(self, chat_id: str, content: str) -> ChannelSendResult:
            return ChannelSendResult(success=True, message_id="mock-text-id")

        async def send_document(
            self, chat_id: str, file_path: str, caption: str | None = None
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

    def _match(self, system_prompt):
        for prefix, response in self._responses.items():
            if system_prompt.startswith(prefix):
                return response
        return None

    async def generate_text(self, *, system_prompt, user_prompt):
        matched = self._match(system_prompt)
        if matched is not None:
            return matched
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
    import app.services.ranren_ai.engine as engine_mod
    import app.services.intervention_service as intervention_mod

    orig_client = client_mod.AIService
    orig_doc = doc_mod.AIService
    orig_engine = engine_mod.AIService
    orig_intervention = intervention_mod.AIService

    client_mod.AIService = lambda *a, **kw: mock
    doc_mod.AIService = lambda *a, **kw: mock
    engine_mod.AIService = lambda *a, **kw: mock
    intervention_mod.AIService = lambda *a, **kw: mock

    yield mock

    client_mod.AIService = orig_client
    doc_mod.AIService = orig_doc
    engine_mod.AIService = orig_engine
    intervention_mod.AIService = orig_intervention


@pytest_asyncio.fixture
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
        "app.services.reminder_service",
    ]
    for mod_name in modules_to_patch:
        if mod_name in sys.modules:
            mod = sys.modules[mod_name]
            if hasattr(mod, "SessionLocal"):
                setattr(mod, "SessionLocal", mock_creator)

    yield

    app.db.session.SessionLocal = orig_session_local
    for mod_name in modules_to_patch:
        if mod_name in sys.modules:
            mod = sys.modules[mod_name]
            if hasattr(mod, "SessionLocal"):
                setattr(mod, "SessionLocal", orig_session_local)


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
async def public_client(mock_ai, db_session):
    app = _make_test_app()
    
    from app.db.session import get_session
    async def override_get_session():
        yield db_session

    app.dependency_overrides[get_session] = override_get_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test/api") as ac:
        yield ac


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _dispose_engine_after_test():
    yield
    from app.db.session import engine
    await engine.dispose()




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
