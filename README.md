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
ADMIN_USERNAME=admin
ADMIN_PASSWORD=change-me
BACKEND_ADMIN_API_KEY=change-me-internal-api-key
```

启动数据库、后端、前端：

```bash
docker compose up -d --build
```

访问：

```text
Frontend: http://localhost:3002
Backend:  http://localhost:8000
Docs:     http://localhost:8000/docs (需要后端内部 API Key)
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

如果用 Docker Compose，前端容器内部仍然监听 3000，但宿主机映射到 `FRONTEND_PORT`，`.env.example` 默认使用 `3002`。

当前 Docker Compose 面向本地/局域网试验环境：后端默认在 `ENVIRONMENT=development` 时开启 reload，前端使用 `next dev`。正式部署前应切换生产镜像、设置强后台密码和 `BACKEND_ADMIN_API_KEY`，并将 `ENVIRONMENT=production`。

后台页面通过前端同源代理访问后端 API。浏览器不会拿到 `BACKEND_ADMIN_API_KEY`；该密钥只在 Next 服务端和 FastAPI 后端之间使用。直接访问后端 `/api/*` 管理接口需要请求头 `X-Admin-API-Key`，公开问卷链接仍然通过 token 访问。

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
- `knowledge_drafts`
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

阶段报告也复用同一套模板化文档链路。服务单详情页可生成：

- `3天阶段报告`
- `7天阶段报告`
- `14天阶段报告`
- `21天阶段报告`
- `28天阶段报告`
- `毕业报告`

阶段报告包含阶段总结、风险提醒、微调建议和复购建议；毕业报告包含体重变化、节律变化、执行率、用户成长总结和下一阶段路线图。

## 打卡与风险控制

打卡主要通过 H5 每日行动/节点复盘问卷提交，系统会保存原始内容，并结构化记录核心字段：

- 早C午B晚A
- 饮水
- 睡眠
- 情绪
- 运动
- 体重

这些字段会用于服务单详情、打卡页、阶段报告和看板统计。群聊普通消息不再依赖 `#打卡` 正则解析，避免把业务绑死在关键词格式上。

MVP 风险识别通过 LLM 做意图识别与信息提取，判断是否需要人工介入、风险原因、续购意向和退款/退出意向。高风险用户不会自动发送 AI 生成内容，必须人工审核；高风险固定拦截话术属于预设安全话术，可以立即发送，用于安抚并提示联系班主任。

文档审核页提供内容发送策略：

- `manual_review`：AI 生成内容默认进入人工审核
- `auto_send`：低风险内容生成后自动发送，高风险内容仍进入人工审核

## 知识库回复与知识沉淀

机器人回复会优先检索数据库知识索引 `knowledge_chunks`，再把命中的知识块交给 DeepSeek 生成最终回复。数据库索引支持结构化 FAQ、审核通过的知识草稿、Markdown/TXT 知识块，以及可选 embedding 检索；若数据库没有命中，会降级到本地 Markdown/LlamaIndex 关键词检索。默认本地知识库目录在：

```text
backend/src/app/services/knowledge_base/
```

可以把常见问答写成 `.md` 或 `.txt` 文件放进去。也可以通过环境变量改目录：

```bash
KNOWLEDGE_BASE_DIR=/path/to/faq
KNOWLEDGE_BASE_TOP_K=3
```

客户 QA 表四可以导入为“干净检索正文 + 审计元数据”两份文件：

```bash
cd backend
uv run python scripts/import_qa_excel.py \
  --source ../参考文件/HumanBurningAI_营养师客服班主任_QA库框架_v1.xlsx
```

默认会生成：

- `backend/src/app/services/knowledge_base/ranren_ai_qa_from_excel.md`：机器人检索用，只包含用户常见问法、参考回复和回复结构。
- `backend/src/app/services/knowledge_base/ranren_ai_qa_from_excel.metadata.json`：审计用，保留表格里的分类、标签、风险、转人工等字段，但不参与检索。

客户 QA 表 `02_分类字典` 的“识别关键词”可导入为关键词检索白名单：

```bash
cd backend
uv run python scripts/import_keyword_whitelist.py \
  --source ../参考文件/HumanBurningAI_营养师客服班主任_QA库框架_v1.xlsx
```

默认会生成：

- `backend/src/app/services/knowledge_base/keyword_whitelist.json`

关键词 fallback 检索只使用这份白名单命中的业务词；`jieba` 分词只作为辅助，不再把所有切词结果都拿去查库。向量语义检索不受这份白名单限制。

Embedding 服务密钥不要写入仓库，可以放在本地 `.env`：

```bash
EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
EMBEDDING_API_KEY=
EMBEDDING_MODEL=text-embedding-v4
```

中期知识库已经拆成两层：

- `knowledge_faqs`：结构化 FAQ 业务表，支持后台新增、编辑、禁用和版本管理。
- `knowledge_chunks`：检索索引表，承接 Markdown、FAQ 和审核通过的知识草稿，用于机器人 RAG 检索。

审核控制台的“结构化 FAQ”区域可直接维护 FAQ；FAQ 启用时会同步到 `knowledge_chunks`，禁用后不再参与检索。审核控制台的“知识索引”区域可以把 `knowledge_base` 目录下的 Markdown/TXT 同步到 `knowledge_chunks` 表。机器人回复时会优先查询数据库知识块；若数据库没有命中，再降级使用原 Markdown/LlamaIndex 检索。

同步接口：

```bash
curl -X POST http://localhost:8000/api/knowledge-base/index/sync \
  -H "x-admin-api-key: ${BACKEND_ADMIN_API_KEY}" \
  -H "content-type: application/json" \
  -d '{"embed": false}'
```

配置好 `EMBEDDING_API_KEY` 后可以生成向量：

```bash
curl -X POST http://localhost:8000/api/knowledge-base/index/sync \
  -H "x-admin-api-key: ${BACKEND_ADMIN_API_KEY}" \
  -H "content-type: application/json" \
  -d '{"embed": true}'
```

当前向量先存在 `knowledge_chunks.embedding` JSON 字段，并在应用层做余弦相似度检索，便于在不替换现有 Postgres 镜像的情况下先跑通中期 RAG。长期 pgvector 迁移已经预留：如果数据库镜像提供 `vector` 扩展，迁移会自动创建 `knowledge_chunks.embedding_vector vector(1024)` 和 HNSW cosine 索引。

当前默认 `postgres:15-alpine` 镜像不包含 pgvector。要启用 pgvector，可先备份数据，再在 `.env` 中切换：

```bash
POSTGRES_IMAGE=pgvector/pgvector:pg15
```

长期知识自我进化已和数据库索引打通：FAQ 草稿批准后会创建结构化 FAQ，并同步到 `knowledge_chunks`，如果已配置 embedding 会同步向量化。

审核控制台支持“知识草稿”：

- FAQ 草稿：按自然日聊天记录沉淀，建议每天 0 点跑前一日聊天。
- 优秀案例草稿：按服务阶段 3/7/14/21/28 天沉淀，每次只读取上一阶段之后的新聊天窗口，避免重复使用早期素材。
- 输入只使用已落库聊天记录，系统做基础脱敏后由 LLM 根据燃人AI服务思想生成待审核草稿。
- 草稿必须人工批准后才会进入结构化 FAQ 表或 `knowledge_chunks` 案例索引，不再追加写入本地文本文件。
- 审核控制台可视化配置知识自我进化的启用开关、扫描间隔、FAQ 扫描时间、阶段案例天数和读取上限；数据库配置优先于 `.env` 默认值。

可选定时扫描默认关闭：

```bash
KNOWLEDGE_EVOLUTION_ENABLED=false
KNOWLEDGE_EVOLUTION_QA_AT=00:00
KNOWLEDGE_EVOLUTION_CASE_DAYS=3,7,14,21,28
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

`RanrenAIEngine` 只依赖通用 `MessageChannel`，负责入营问卷指令、知识库检索、LLM 回复和消息落库。当前企微 worker 只把 `WeComMessage` 转成 `ChannelMessage` 后交给引擎处理。后续如果换成其他通道，只需要实现新的 `MessageChannel` adapter，并把入站消息转成 `ChannelMessage`。

测试阶段可以临时放开入站策略：

```bash
WECOM_DM_POLICY=open
WECOM_GROUP_POLICY=open
```

## 定时提醒

工作日中午 12 点，如果用户当天没有满足提醒条件，机器人会主动提醒：

```bash
REMINDER_ENABLED=true
REMINDER_AT=12:00
REMINDER_TIMEZONE=Asia/Shanghai
REMINDER_WEEKDAYS=1,2,3,4,5
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
