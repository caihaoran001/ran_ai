# 燃人AI 1.0

燃人AI 1.0 是内部运营系统：一个社群对应一个真实用户的 28 天服务空间。系统负责服务单、H5 问卷、LLM 生成用户画像和交付文件、人工审核、打卡记录、QiWe 实验通道消息收发和基础看板。

## 技术栈

- Frontend: Next.js + React + TypeScript + Tailwind CSS + shadcn/ui 风格组件
- Backend: FastAPI + Python + SQLAlchemy + Alembic
- Database: PostgreSQL
- AI: OpenAI SDK 兼容配置，默认可接 DeepSeek
- Messaging: QiWe 实验通道
- Runtime: Docker Compose

## 项目结构

```text
backend/      FastAPI、SQLAlchemy、Alembic、AI 服务、QiWe 消息通道
frontend/     Next.js 后台和 H5 问卷
docker-compose.yml
docker-compose.prod.yml
.env.example
.env.production.example
```

## 快速开始

准备配置：

```bash
cp .env.example .env
```

至少填写：

```bash
AI_API_KEY=your-ai-api-key
QIWEI_ENABLED=true
QIWEI_TOKEN=your-qiwei-token
QIWEI_GUID=your-online-device-guid
QIWEI_CALLBACK_SECRET=change-me-callback-secret
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

当前 `docker-compose.yml` 面向本地/局域网试验环境：后端容器默认使用 `BACKEND_ENVIRONMENT=docker`，不启用 Uvicorn reload，避免 SSE 长连接阻塞自动重载；修改后端代码后请重启 `ran-ai-backend`。前端仍使用 `next dev`。

公有云部署使用：

```bash
cp .env.production.example .env
docker compose -f docker-compose.prod.yml up -d --build
```

生产部署默认使用阿里云 RDS PostgreSQL 15，不在 ECS Compose 中启动数据库。请在 `.env` 中配置 RDS 连接串：

```bash
DATABASE_URL=postgresql+asyncpg://用户名:密码@RDS内网地址:5432/数据库名
```

生产部署说明见 [docs/deployment.md](docs/deployment.md)。

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
- `user_state_profiles`（动态状态画像，outcome events → JSONB 投影）
- `user_documents`
- `checkins`
- `messages`
- `outcome_events` / `outcome_snapshots` / `outcome_stage_states`（成效追踪）
- `knowledge_drafts`
- `knowledge_faqs`（结构化 FAQ）
- `knowledge_chunks`（检索索引）
- `reminder_events`

## AI 配置

业务代码只依赖 `backend/src/app/services/ai_service`，配置使用 OpenAI-compatible 形态：

```bash
AI_BASE_URL=https://api.deepseek.com
AI_API_KEY=your-ai-api-key
AI_MODEL=deepseek-v4-pro
# 交付文档/阶段报告专用模型，留空则回退 AI_MODEL。
AI_DOCUMENT_MODEL=deepseek-v4-pro
AI_MAX_TOKENS=8000
```

用户提交问卷后，系统会生成用户画像。服务单详情页可以再次手动生成画像，也可以点击生成两份交付文件：

- `个人能量重启处方单`
- `28天执行标准与打卡要求`

交付文件使用 `手动版提示词.md` 的提示词思路，让 LLM 先输出结构化 JSON 关键字段，再由后端模板渲染成后台可编辑正文。系统持久化保存 `content_json/content_md`；下载或发送时才用 `python-docx` 临时渲染 `.docx`，响应或上传完成后删除临时文件。后台页面支持查看、微调、下载和确认发送。

阶段报告也复用同一套模板化文档链路。服务单详情页可生成：

- `3天阶段报告`
- `7天阶段报告`
- `14天阶段报告`
- `21天阶段报告`
- `28天阶段报告`

阶段报告包含阶段总结、风险提醒、微调建议和后续执行建议。28 天是当前服务阶段的完结节点，系统不会额外生成“毕业报告”。

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

机器人回复检索数据库知识索引 `knowledge_chunks`，再把命中的知识块交给模型生成最终回复。参考问答使用向量与白名单关键词双路 RRF；优秀案例只使用向量检索。当前没有“数据库未命中后直接读取本地 Markdown”的生产 fallback。

默认本地知识素材目录在：

```text
backend/src/app/services/knowledge_base/
```

本地 `.md`/`.txt` 只有列入同目录 `knowledge_sources.json` 的 `active` 清单并执行索引同步后，才会成为有效 `markdown` chunk。`retained_legacy` 仅用于保留历史，不参与检索。也可以通过环境变量改目录：

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

审核控制台的“结构化 FAQ”区域可直接维护 FAQ；FAQ 启用时会同步到 `knowledge_chunks`，禁用后不再参与检索。审核控制台的“知识索引”区域只同步 `knowledge_sources.json.active` 明确启用的本地素材；未启用的旧 Markdown 会保留在仓库和数据库历史中，但同步后状态为 `stale`。

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

当前生产数据库使用阿里云 RDS PostgreSQL 15，并已启用 `vector` 插件。迁移会在可用时自动创建 `knowledge_chunks.embedding_vector vector(1024)` 和 HNSW cosine 索引；同时保留 `knowledge_chunks.embedding` JSON 字段作为应用层兼容路径。

长期知识自我进化已和数据库索引打通：草稿批准时先做格式归一化后的精确哈希检查，再用向量召回疑似候选；只有存在候选时才增加一次结构化 LLM 判定。普通重复会自动跳过或更新现有版本，业务口径冲突才交给审核员选择正确内容。FAQ 旧版本保存在 `knowledge_faq_versions`，案例被替代后旧 chunk 保留为 `superseded`，查重过程写入 `knowledge_dedupe_events`。

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

## QiWe 实验通道

QiWe 是当前主要消息通道。启用：

```bash
QIWEI_ENABLED=true
QIWEI_TOKEN=your-qiwei-token
QIWEI_GUID=your-online-device-guid
QIWEI_CALLBACK_SECRET=change-me-callback-secret
QIWEI_BATCH_WINDOW_SECONDS=8
```

通道职责：

- 接收 QiWe callback 推送的群消息
- callback URL 需要携带 `?secret=...`，或由上游请求头传入 `X-Qiwei-Callback-Secret`
- 同一群同一用户连续文本先聚合，再交给燃人AI处理
- 按 `group_id` 关联 `service_cases`
- 写入 `messages`
- 群内首条有效消息自动创建本群服务单，引荐前只留痕不回复
- 本群第一次由已识别员工 @ 机器人时，机器人只发送固定自我介绍；从后续消息起开放正常回复
- 引荐后的普通聊天会检索知识库后调用 DeepSeek 生成回复
- 群里发送“发送本群入营问卷”仅复用已有服务单并回复可选的本群专属问卷链接
- 已识别员工可在引荐后的消息中 @ 机器人设置服务开始日期；该日期只控制 28 天计时和按天任务，不影响群聊问答
- 后台人工发送、提醒发送、AI 回复统一通过 QiWe 服务完成

## 消息通道与燃人AI

消息接入在生产运行时拆成两个后端容器：

- `qiwe-ingress`：稳定常驻，只负责校验、解析 QiWe callback，并在返回 2xx 前写入 RDS `inbound_events`。
- `backend`：可以随日常代码部署重启，持续消费 `inbound_events`，完成消息聚合和后续业务处理。

业务处理内部继续按通道适配、消息聚合和通道无关引擎分层：

```text
app/qiwe_ingress.py             QiWe callback 轻量持久化入口
app/services/qiwe_service/      RDS inbox 消费与 QiWe 群消息聚合
app/services/channels/          通用消息通道接口、当前通道注册表、QiWe adapter
app/services/ranren_ai/         通道无关的业务处理引擎
```

完整链路是：QiWe callback → `qiwe-ingress` 持久化到 `inbound_events` → `backend` 领取事件并转成 `ChannelMessage` → 同群同用户消息按约 8 秒窗口聚合 → `RanrenAIEngine` 处理。这样即使业务后端正在部署或重启，已经收到的 callback 也不会只停留在进程内存中。`RanrenAIEngine` 只依赖通用 `MessageChannel`，负责群服务单建立、员工引荐闸门、可选问卷指令、知识库检索、LLM 回复和消息落库。

## 定时提醒

工作日中午 12 点，如果用户当天没有满足提醒条件，机器人会主动提醒：

```bash
REMINDER_ENABLED=true
AGENT_REMINDERS_ENABLED=true
REMINDER_AT=12:00
REMINDER_TIMEZONE=Asia/Shanghai
REMINDER_WEEKDAYS=1,2,3,4,5
```

提醒状态写入 `reminder_events`，同一天不会重复提醒。
Agent 群内一次性提醒使用独立任务表，默认复用 `REMINDER_CHECK_INTERVAL_SECONDS=30`；到点后会再跑一遍 Agent 生成最终提醒文案，并在 30 秒内发送。

## 成效追踪

成效引擎 (`backend/src/app/services/outcome_engine.py`) 会在每条群聊消息上做结构化提取：

- `outcome_events` 记录字段级的成效证据（来自用户原话），配合 `(service_case_id, message_id, field_key)` 唯一约束去重
- `outcome_stage_states` 按里程碑（day1/day3/day7/day14/day28/day90/day365）维护当前阶段目标、收集进度、最近追问
- `outcome_snapshots` 在每个里程碑触发时聚合事件 + 打卡 + 状态，生成阶段总结（`OUTCOME_SNAPSHOTS_ENABLED=true`）
- `user_state_profiles` 把 outcome events 投影成 JSONB 状态画像（`action_focus` / `need_analysis` / `body_feedback` / `risk_profile` / `outcome_changes` 等领域），AI 回复会引用当前 action focus 作为隐性背景

服务单详情页可查看阶段状态和最近事件。LLM 提取用 temperature=0.3，复用 AI service semaphore 控制并发。

## 系统诊断

`/api/diagnostics/*` 提供系统自检和 AI 连通探针：

- `GET /api/diagnostics/test-suites` — 列出可运行的测试套件
- `GET /api/diagnostics/summary` — 数据库连通 + AI 探针（模型/延迟/状态）
- `GET /api/diagnostics/run/{test_id}` — 流式运行指定测试套件（pytest 输出实时回传）
- `GET /api/diagnostics/history` / `POST /api/diagnostics/clear-history` — 跨设备共享的自测历史

对应前端页面 `/system-diagnostics`。页面的一键健康自测只运行使用 Mock 且回滚数据的
连续编号的安全健康检查 `01-08`；真实模型 E2E 显示为 `E2E-01` 并单独隔离，运行前必须确认。模拟客户业务数据会回滚，真实模型 token、费用与耗时作为诊断记录单独保留。

## 页面

- `/users` 用户列表，可进入用户详情查看服务单和最新用户画像
- `/cases` 服务单列表
- `/cases/[id]` 服务单详情（含成效追踪、行动焦点、最近追问）
- `/q/[token]` H5 问卷
- `/questionnaire-config` 问卷配置，后台直接编辑问卷字段 JSON，新的问卷链接会读取最新配置
- `/documents/[caseId]` 交付文件审核
- `/checkins` 打卡记录
- `/dashboard` 基础看板（含成效统计、行动焦点分布）
- `/system-diagnostics` 系统自测 + AI 探针面板
