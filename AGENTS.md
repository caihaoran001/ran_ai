# PROJECT KNOWLEDGE BASE

**Generated:** 2026-05-14
**Project:** 燃AI 1.0 - 企业微信群内部运营系统

## OVERVIEW

企业微信群对应真实用户的 28 天服务空间。系统负责服务单、H5 问卷、LLM 生成用户画像和交付文件、人工审核、打卡记录、企微机器人消息收发和基础看板。支持软删除和多消息渠道扩展。

## STRUCTURE

```
ran_ai/
├── backend/          # FastAPI + SQLAlchemy + Alembic + AI 服务 + 企微机器人
│   └── src/app/
│       ├── api/routes/      # API 端点
│       ├── models/          # 数据库模型 (支持软删除)
│       ├── schemas/         # Pydantic 请求/响应模型
│       ├── services/
│       │   ├── ai_service/      # OpenAI 兼容 AI 服务
│       │   ├── channels/        # 消息渠道抽象层
│       │   ├── ranren_ai/       # 渠道无关业务引擎
│       │   ├── wecom_service/   # 企微机器人
│       │   ├── knowledge_base/  # FAQ 知识库
│       │   └── document_service # 交付文件生成
│       ├── workers/         # 后台任务
│       └── db/              # 数据库会话管理
├── frontend/         # Next.js 后台和 H5 问卷
├── docker-compose.yml
├── .env.example
├── 手动版提示词.md    # 交付文件生成提示词
└── 说明.md           # 项目说明文档
```

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| API 路由 | `backend/src/app/api/routes/` | 按功能模块组织 |
| 数据库模型 | `backend/src/app/models/` | SQLAlchemy 2.0 风格，支持软删除 |
| 业务逻辑 | `backend/src/app/services/` | AI、企微、文档等服务 |
| 消息渠道 | `backend/src/app/services/channels/` | 抽象层，支持多渠道扩展 |
| 燃人 AI 引擎 | `backend/src/app/services/ranren_ai/` | 渠道无关的业务引擎 |
| 前端页面 | `frontend/src/app/` | Next.js App Router |
| 数据库迁移 | `backend/alembic/versions/` | Alembic 管理 |
| 环境配置 | `.env.example` | 复制为 .env 使用 |

## CONVENTIONS

- **后端**: Python 3.11+, FastAPI, SQLAlchemy 2.0 async, Pydantic v2
- **前端**: Next.js 15, React 19, TypeScript, Tailwind CSS, shadcn/ui 风格
- **数据库**: PostgreSQL 15, asyncpg 驱动
- **AI**: OpenAI SDK 兼容 (默认 DeepSeek)
- **包管理**: 后端 uv, 前端 npm
- **容器化**: Docker Compose
- **软删除**: 用户/服务单使用 `deleted_at` 字段，条件唯一索引
- **消息渠道**: `MessageChannel` 协议，支持企微/钉钉/飞书等

## ARCHITECTURE

```
消息渠道 (WeCom/DingTalk/Feishu)
    ↓
消息渠道抽象层 (MessageChannel)
    ↓
燃人 AI 引擎 (RanrenAIEngine)
    ↓
业务逻辑 (服务单/问卷/AI/文档)
```

## ANTI-PATTERNS (THIS PROJECT)

- 不要使用同步数据库操作，全部使用 async
- 不要直接修改 alembic 运行中的迁移文件
- 不要在前端硬编码 API 地址，使用环境变量
- 不要在业务逻辑中直接调用企微 API，使用 channels 抽象层
- 不要硬删除数据，使用软删除 (`deleted_at`)

## COMMANDS

```bash
# 开发环境启动
docker compose up -d --build

# 后端本地开发
cd backend && uv sync && uv run alembic upgrade head && uv run ran-ai-api

# 前端本地开发
cd frontend && npm install && npm run dev

# 数据库迁移
cd backend && uv run alembic upgrade head
cd backend && uv run alembic revision --autogenerate -m "description"
```

## NOTES

- 前端容器内部监听 3000，宿主机映射到 FRONTEND_PORT (默认 3002)
- 后端默认端口 8000，API 前缀 /api
- 企微机器人需要配置 WECOM_BOT_ID 和 WECOM_SECRET
- AI 服务需要配置 AI_API_KEY (DeepSeek 或其他 OpenAI 兼容服务)
- 问卷页面 (`/q/*`) 已隔离，不显示后台管理侧边栏
- 支持局域网访问，配置 `FRONTEND_PUBLIC_BASE_URL` 为实际 IP
