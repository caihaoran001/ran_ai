# 燃AI 1.0

燃AI 1.0 是内部运营系统：一个企业微信群对应一个真实用户的 28 天服务空间。系统负责服务单、H5 问卷、LLM 生成用户画像和交付文件、人工审核、打卡记录、企微机器人消息收发和基础看板。

## 技术栈

- Frontend: Next.js + React + TypeScript + Tailwind CSS + shadcn/ui 风格组件
- Backend: FastAPI + Python + SQLAlchemy + Alembic
- Database: PostgreSQL
- AI: OpenAI SDK 兼容配置，默认可接 DeepSeek
- Messaging: 企业微信 AI Bot WebSocket bridge
- Runtime: Docker Compose

## 项目结构

```text
backend/      FastAPI、SQLAlchemy、Alembic、AI 服务、企微机器人服务
frontend/     Next.js 后台和 H5 问卷
docker-compose.yml
.env.example
```

## 快速开始

准备配置：

```bash
cp .env.example .env
```

至少填写：

```bash
AI_API_KEY=your-ai-api-key
WECOM_BOT_ID=your-bot-id
WECOM_SECRET=your-secret
```

启动数据库、后端、前端：

```bash
docker compose up -d --build
```

访问：

```text
Frontend: http://localhost:3002
Backend:  http://localhost:8000
Docs:     http://localhost:8000/docs
```

## 本地开发

后端：

```bash
cd backend
uv sync
uv run alembic upgrade head
uv run ran-ai-api
```

前端：

```bash
cd frontend
npm install
npm run dev
```

如果用 Docker Compose，前端容器内部仍然监听 3000，但宿主机映射到 `FRONTEND_PORT`，当前 `.env` 使用的是 `3002`。

## 数据库

默认本地连接：

```bash
DATABASE_URL=postgresql+asyncpg://ran_ai:ran_ai_password@localhost:54322/ran_ai
```

迁移：

```bash
cd backend
uv run alembic upgrade head
```

首版核心表：

- `users`
- `service_cases`
- `questionnaire_submissions`
- `user_profiles`
- `user_documents`
- `checkins`
- `messages`
- `reminder_events`

## AI 配置

业务代码只依赖 `backend/src/app/services/ai_service`，配置使用 OpenAI-compatible 形态：

```bash
AI_BASE_URL=https://api.deepseek.com
AI_API_KEY=your-ai-api-key
AI_MODEL=deepseek-chat
AI_MAX_TOKENS=4000
```

用户提交问卷后，系统会生成用户画像。服务单详情页可以再次手动生成画像，也可以点击生成两份交付文件：

- `个人能量重启处方单`
- `28天执行标准与打卡要求`

交付文件使用 `手动版提示词.md` 的提示词思路，让 LLM 先输出结构化 JSON 关键字段，再由后端模板渲染成后台可编辑正文，并用 `python-docx` 落盘为 `.docx`。文件默认写入 `generated_documents/`，后台页面支持查看、微调、下载和确认发送。

## 知识库回复

机器人回复会先用 LlamaIndex 检索 FAQ 知识库，再把命中的内容交给 DeepSeek 生成最终回复。当前第一版用的是 LlamaIndex `SimpleKeywordTableIndex`，不需要 embedding 模型和向量数据库；后续知识库变大后，可以替换为 embedding + pgvector/Qdrant/Milvus 等向量检索。默认知识库在：

```text
backend/src/app/services/knowledge_base/
```

可以把常见问答写成 `.md` 或 `.txt` 文件放进去。也可以通过环境变量改目录：

```bash
KNOWLEDGE_BASE_DIR=/path/to/faq
KNOWLEDGE_BASE_TOP_K=3
```

## 企业微信机器人

企微机器人是 backend 的核心组成部分。启用：

```bash
WECOM_WORKER_ENABLED=true
WECOM_BOT_ID=your-bot-id
WECOM_SECRET=your-secret
```

机器人职责：

- 接收企业微信群消息
- 按 `group_id` 关联 `service_cases`
- 写入 `messages`
- 识别 `#打卡` 并写入 `checkins`
- 普通聊天会检索知识库后调用 DeepSeek 生成回复
- 群里发送“发送本群入营问卷”会自动创建或复用本群服务单，并回复本群专属问卷链接
- 后台人工发送、提醒发送、AI 回复统一通过企微服务完成

## 消息通道与燃人AI

消息接入已经拆成两层：

```text
app/services/channels/        通用消息通道接口、当前通道注册表、企微 adapter
app/services/ranren_ai/       通道无关的业务处理引擎
app/services/wecom_service/   当前企微 AI Bot 接入 worker
```

`RanrenAIEngine` 只依赖通用 `MessageChannel`，负责入营问卷指令、打卡识别、知识库检索、LLM 回复和消息落库。当前企微 worker 只把 `WeComMessage` 转成 `ChannelMessage` 后交给引擎处理。后续如果换成其他通道，只需要实现新的 `MessageChannel` adapter，并把入站消息转成 `ChannelMessage`。

测试阶段可以临时放开入站策略：

```bash
WECOM_DM_POLICY=open
WECOM_GROUP_POLICY=open
```

## 定时提醒

工作日中午 12 点，如果用户当天没有发送符合 `^#打卡` 的消息，机器人会主动提醒：

```bash
REMINDER_ENABLED=true
REMINDER_AT=12:00
REMINDER_TIMEZONE=Asia/Shanghai
REMINDER_WEEKDAYS=1,2,3,4,5
REMINDER_REQUIRED_PATTERN=^#打卡
```

提醒状态写入 `reminder_events`，同一天不会重复提醒。

## 页面

- `/users` 用户列表，可进入用户详情查看服务单和最新用户画像
- `/cases` 服务单列表
- `/cases/[id]` 服务单详情
- `/q/[token]` H5 问卷
- `/questionnaire-config` 问卷配置，后台直接编辑问卷字段 JSON，新的问卷链接会读取最新配置
- `/documents/[caseId]` 交付文件审核
- `/checkins` 打卡记录
- `/dashboard` 基础看板
