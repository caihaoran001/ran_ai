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
│   │   ├── wecom_service/   # 企微机器人实现
│   │   ├── knowledge_base/  # 本地 Markdown FAQ fallback
│   │   ├── document_service # 交付文件生成
│   │   ├── checkin_service  # 打卡处理
│   │   └── reminder_service # 定时提醒
│   ├── workers/       # 后台任务 (企微机器人、定时提醒)
│   ├── db/            # 数据库会话管理
│   └── core/          # 配置和常量
├── alembic/           # 数据库迁移
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
| 企微渠道实现 | `src/app/services/channels/wecom.py` | 企微消息发送 |
| 渠道注册表 | `src/app/services/channels/registry.py` | 全局渠道管理 |
| 燃人 AI 引擎 | `src/app/services/ranren_ai/engine.py` | 渠道无关业务逻辑 |
| 企微机器人 | `src/app/services/wecom_service/worker.py` | WebSocket 消息接收 |
| 数据库知识索引 | `src/app/services/knowledge_index.py` | `knowledge_chunks` 检索，支持 embedding/关键词 fallback |
| 本地知识库 fallback | `src/app/services/knowledge_base.py` | Markdown/LlamaIndex 降级检索 |
| 定时提醒 | `src/app/services/reminder_service.py` | 打卡提醒 |

## CONVENTIONS

- 所有数据库操作使用 async/await
- 路由函数返回 SQLAlchemy 模型，FastAPI 自动序列化
- 依赖注入使用 `Depends(get_session)`
- 配置通过 `pydantic-settings` 管理
- 后端管理 API 默认需要 `X-Admin-API-Key`，公开问卷和 `/health` 是例外
- 软删除使用 `deleted_at` 字段，查询时需过滤 `deleted_at.is_(None)`
- 消息发送使用 `channels` 抽象层，不直接调用企微 API

## ENTRY POINTS

```bash
# API 服务
uv run ran-ai-api

# 企微机器人 (独立进程)
uv run ran-ai-wecom-worker

# 定时任务调度器
uv run ran-ai-scheduler
```

## ANTI-PATTERNS

- 不要使用同步数据库调用 (`session.query()`)
- 不要直接修改已提交的迁移文件
- 不要在路由中直接调用 AI，使用 services 层
- 不要直接调用企微 API，使用 `channels` 抽象层
- 不要硬删除数据，使用软删除 (`deleted_at`)
- 不要把 `BACKEND_ADMIN_API_KEY` 暴露到 `NEXT_PUBLIC_*` 或浏览器端代码
