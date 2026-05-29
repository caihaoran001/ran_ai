# BACKEND KNOWLEDGE BASE

**Stack:** FastAPI + SQLAlchemy 2.0 async + Alembic + OpenAI SDK

## STRUCTURE

```
backend/
├── src/app/
│   ├── api/routes/    # API 端点 (按功能模块)
│   ├── models/        # SQLAlchemy 数据模型 (支持软删除)
│   ├── schemas/       # Pydantic 请求/响应模型
│   ├── services/
│   │   ├── ai_service/      # OpenAI 兼容 AI 服务
│   │   ├── channels/        # 消息渠道抽象层
│   │   ├── ranren_ai/       # 渠道无关业务引擎
│   │   ├── qiwe_service/    # QiWe 消息聚合
│   │   ├── knowledge_base/  # 本地 Markdown FAQ fallback
│   │   ├── document_service # 交付文件生成
│   │   ├── checkin_service  # 打卡处理
│   │   ├── reminder_service # 定时提醒 + 知识进化调度
│   │   ├── intake_facts.py  # 入营事实确定性提取
│   │   ├── service_case_queries.py # 服务单查询辅助
│   │   └── knowledge_evolution.py  # 知识草稿自动生成
│   ├── workers/       # 后台任务 (定时提醒)
│   ├── db/            # 数据库会话管理
│   └── core/          # 配置和常量
├── alembic/           # 数据库迁移
├── tests/             # 自动化测试 (29 用例，Mock LLM)
│   ├── conftest.py       # Mock AIService + FastAPI test app
│   ├── test_01_intake.py # 入营全链路
│   ├── test_02_chat.py   # 聊天风控
│   ├── test_03_checkin.py# 打卡数据
│   ├── test_04_stage_reports.py # 阶段报告
│   ├── test_05_evolution.py # 知识进化
│   └── test_06_toggles.py # 系统开关
└── pyproject.toml     # 依赖和入口点
```

## WHERE TO LOOK

| Task | File | Notes |
|------|------|-------|
| API 入口 | `src/app/main.py` | FastAPI app 创建和生命周期 |
| 用户管理 | `src/app/api/routes/users.py` | 用户 CRUD + 软删除 |
| 服务单 | `src/app/api/routes/service_cases.py` | 服务单生命周期 + 软删除 |
| 问卷 | `src/app/api/routes/questionnaires.py` | H5 问卷提交 |
| 文档生成 | `src/app/api/routes/documents.py` | 交付文件管理 |
| 消息管理 | `src/app/api/routes/messages.py` | 列出/发送消息 |
| AI 服务 | `src/app/services/ai_service/client.py` | OpenAI 兼容调用 |
| 消息渠道抽象 | `src/app/services/channels/base.py` | `MessageChannel` 协议 |
| QiWe 渠道实现 | `src/app/services/channels/qiwe.py` | QiWe 消息发送 |
| 渠道注册表 | `src/app/services/channels/registry.py` | 全局渠道管理 |
| 燃人 AI 引擎 | `src/app/services/ranren_ai/engine.py` | 渠道无关业务逻辑 |
| QiWe 聚合 | `src/app/services/qiwe_service/aggregator.py` | 群消息聚合 |
| 数据库知识索引 | `src/app/services/knowledge_index.py` | `knowledge_chunks` 检索，支持 embedding/关键词 fallback |
| 知识自我进化 | `src/app/services/knowledge_evolution.py` | FAQ/案例草稿自动提取 |
| 本地知识库 fallback | `src/app/services/knowledge_base.py` | Markdown/LlamaIndex 降级检索 |
| 定时提醒 | `src/app/services/reminder_service.py` | 打卡提醒 + 阶段报告生成 + 知识进化调度 |
| 入营事实提取 | `src/app/services/intake_facts.py` | 问卷确定性事实抽取 (BMI/减重带) |
| 服务单查询 | `src/app/services/service_case_queries.py` | `get_active_service_case` / `get_service_case` |
| 自动化测试 | `tests/` | 29 用例，Mock LLM，见 `tests/README.md` |

## CONVENTIONS

- 所有数据库操作使用 async/await
- 路由函数返回 SQLAlchemy 模型，FastAPI 自动序列化
- 依赖注入使用 `Depends(get_session)`
- 配置通过 `pydantic-settings` 管理
- 后端管理 API 默认需要 `X-Admin-API-Key`，公开问卷和 `/health` 是例外
- 软删除使用 `deleted_at` 字段，查询时需过滤 `deleted_at.is_(None)`
- 消息发送使用 `channels` 抽象层，不直接调用具体通道 API

## ENTRY POINTS

```bash
# API 服务
uv run ran-ai-api

# 定时任务调度器
uv run ran-ai-scheduler

# 自动化测试
uv run pytest tests/ -v
```

## ANTI-PATTERNS

- 不要使用同步数据库调用 (`session.query()`)
- 不要直接修改已提交的迁移文件
- 不要在路由中直接调用 AI，使用 services 层
- 不要直接调用具体通道 API，使用 `channels` 抽象层
- 不要硬删除数据，使用软删除 (`deleted_at`)
- 不要把 `BACKEND_ADMIN_API_KEY` 暴露到 `NEXT_PUBLIC_*` 或浏览器端代码
