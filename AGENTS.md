# PROJECT KNOWLEDGE BASE

燃人AI 1.0 — an internal community-operations system. Each WeChat-style group maps to one real user's 28-day服务 (service) window. The system handles service cases, an H5 questionnaire, LLM-generated user profiles and delivery documents, human review, check-in records, the **QiWe** experimental messaging channel, and a basic dashboard.

Stack: **FastAPI + SQLAlchemy 2.0 (async) + Alembic + PostgreSQL** (backend), **Next.js 15 + React 19 + TypeScript + Tailwind** (frontend), **Docker Compose** runtime, OpenAI-compatible AI (default DeepSeek).

## Companion docs — read these too

This file is the primary knowledge base. Three detailed companion docs (in Chinese) hold additional context; read the relevant one before changing code:

- `backend/AGENTS.md` — backend structure + a "WHERE TO LOOK" table mapping tasks to files.
- `frontend/AGENTS.md` — frontend structure, API endpoints, conventions.
- `docs/testing/README.md` + `docs/testing/TEST_CASES.md` — **the only entry point** when the user says "测试 / test". See Testing below.

## CONVENTIONS
- **后端**: Python 3.11+, FastAPI, SQLAlchemy 2.0 async, Pydantic v2, uv 包管理
- **前端**: Next.js 15, React 19, TypeScript, Tailwind CSS, npm 包管理
- **数据库**: PostgreSQL 15, asyncpg 驱动, 软删除 (`deleted_at` 字段)
- **AI**: OpenAI SDK 兼容 (默认 DeepSeek)
- **消息渠道**: `MessageChannel` 协议抽象，支持多渠道扩展

## Commands

### Run everything (Docker — primary local/LAN workflow)
```bash
cp .env.example .env          # then set AI_API_KEY, QIWEI_*, ADMIN_*, BACKEND_ADMIN_API_KEY
docker compose up -d --build
# Frontend http://localhost:3002  ·  Backend http://localhost:8000  ·  Postgres :54322
```
The backend container runs **without uvicorn reload** (SSE long-connections would block it). **After editing any file under `backend/src/`, you must `docker restart ran-ai-backend`** or changes won't take effect — this is the most commonly forgotten step.

### Backend (local, in `backend/`, uses `uv`)
```bash
uv sync
uv run alembic upgrade head
uv run ran-ai-api               # API server
uv run ran-ai-scheduler         # reminder/knowledge-evolution/outcome scheduler
uv run ran-ai-wecom-worker      # legacy WeCom worker (new channel is QiWe)
uv run pytest tests/ -v         # full test suite (Mock LLM; real-LLM E2E is isolated)
uv run pytest tests/test_02_chat.py -v          # single file
uv run pytest tests/test_02_chat.py::test_name  # single test
```

### Frontend (in `frontend/`, uses `npm`)
```bash
npm install
npm run dev          # next dev on 0.0.0.0
npm run build        # MUST build locally before deploy — never compile on ECS
npm run lint
npm run test:e2e     # Playwright
```

## Architecture — the big picture

### Messaging is layered (channel-agnostic core)
Message handling is split into three layers so business logic never touches a specific channel's API:
```
app/services/channels/     MessageChannel protocol, channel registry, QiWe adapter
app/services/ranren_ai/    RanrenAIEngine — channel-agnostic business engine
app/services/qiwe_service/ QiWe group-message aggregation
```
A QiWe callback turns inbound text into a `ChannelMessage`, aggregates consecutive messages from the same user/group over an ~8s window, then hands it to `RanrenAIEngine` (engine.py). The engine handles the intake-questionnaire command, knowledge retrieval, LLM reply, and message persistence. **To send messages, always go through the `channels` abstraction — never call a channel API directly.**

### Knowledge retrieval / RAG (two-tier with fallback)
Bot replies first query the DB index `knowledge_chunks` (via `knowledge_index.py`), then pass hits to the LLM. The index ingests structured FAQs (`knowledge_faqs`), approved knowledge drafts, and Markdown/TXT chunks, with optional embedding (cosine in app layer; pgvector migration is pre-staged but the default `postgres:15-alpine` image lacks it). If the DB index misses, it **falls back** to local Markdown/LlamaIndex keyword search under `backend/src/app/services/knowledge_base/`. Keyword fallback is limited to the business whitelist `keyword_whitelist.json` — do not query the DB with every jieba token.

### Documents & profiles
Questionnaire submission → LLM generates a user profile. Service-case detail can generate two delivery docs (处方单, 28天执行单) and stage reports (3/7/14/21/28-day + graduation). Flow: LLM emits **structured JSON** for key fields → backend templates (`document_templates.py`) render editable Markdown → persisted as `content_json`/`content_md` → only at download/send time does `docx_service.py` render a temporary `.docx` (deleted after). **The authoritative system prompts live in `backend/src/app/services/document_service.py`; `手动版提示词.md` is reference only and must not be used at runtime.**

### Outcome tracking
`outcome_engine.py` extracts structured outcome evidence per chat message into `outcome_events` (deduped by `(service_case_id, message_id, field_key)`), maintains per-milestone `outcome_stage_states`, aggregates `outcome_snapshots` at milestones, and `user_state_profile.py` projects events into a JSONB `user_state_profiles` (action_focus / need_analysis / body_feedback / risk_profile). AI replies cite the current action focus as implicit background.

### Risk control
MVP risk is identified by **LLM intent/info extraction**, not hardcoded keywords. High-risk users never auto-receive AI content (forced human review); only the preset high-risk safety script may send immediately. Content policy (`content_policy.py`): `manual_review` (default — AI content goes to human review) vs `auto_send` (low-risk auto-sends, high-risk still reviewed).

### Auth model
Backend admin `/api/*` requires header `X-Admin-API-Key` (`BACKEND_ADMIN_API_KEY`); `/health` and public questionnaire (token-based) are exceptions. The admin key lives **only** between the Next server and FastAPI — never expose it to `NEXT_PUBLIC_*` or browser code. The frontend proxies backend API same-origin.

## ANTI-PATTERNS (CRITICAL RULES)
- **禁用同步DB操作**：严禁在后端执行同步数据库操作，全部使用 async，保证高并发性能。
- **保护 Alembic**：严禁直接手动修改已经生成并运行的 Alembic 迁移文件。
- **前端变量**：严禁在前端硬编码 API 路由地址，必须统一使用环境变量。
- **渠道隔离**：严禁在核心业务逻辑中直接调用消息通道 API，必须通过 `channels` 抽象层进行代理。
- **绝不物理硬删除**：严禁硬删除数据，必须使用软删除（设置 `deleted_at` 字段并结合条件唯一索引）。
- **杜绝关键词硬编码规则**：参考文件应作为大模型输入与知识库素材，业务逻辑判断必须优先使用大模型结构化输出，而非硬编码关键词判断。
- **QA 表四入库限制**：检索正文只放"问法/参考回复/回复结构"；分类、风险、转人工字段必须置于 metadata 中，防止污染检索文本。
- **关键词白名单局限**：Fallback 检索仅限使用 `keyword_whitelist.json` 的业务白名单。
- **FAQ 与 Chunk 同步**：审核通过/人工维护的 FAQ 必须保存到 `knowledge_faqs` 并同步至 `knowledge_chunks`，严禁只追加到 Markdown 文件。
- **打卡解析原则**：打卡结构化完全以问卷/表单提交的结构化字段为主，不再新增 `#打卡` 文本正则解析。
- **提示词来源**：系统提示词完全以 `backend/src/app/services/document_service.py` 内部定义的为准，原 `手动版提示词.md` 仅为参考，不得参与运行时 LLM 呼叫。

## DEBUGGING & DIAGNOSTICS (调试与排查指南)
- **查看实时运行日志**：在排查消息收发、大模型调用、企微回调等问题时，**必须**在项目根目录运行 `docker logs -f --tail 100 ran-ai-backend` 查看最新日志。
- **查真实数据/配置一律以线上为准，禁止拿本地推断**：排查任何真实业务数据（聊天记录、服务单、消息 payload、画像等）或线上配置/运行状态时，**必须查生产**，严禁用本地 `.env`、本地 DB、`.env.example` 或代码默认值推断。原因：本地库是种子/测试数据，且**阿里云 RDS 只在 ECS 的 VPC 内可达，本地连不到 RDS**。
  - **查数据（RDS）**：SSH 进 ECS（`43.98.170.61`，见 `scratch/*remote*.py` 的 paramiko 写法或 `docs/qiwe_debugging_guide.md`）后 `docker exec -i ran_ai-backend-1 uv run python -` 跑查询脚本——只有容器内 `DATABASE_URL` 指向 RDS。
  - **查配置/运行状态**：读 ECS 上 `/root/ran_ai/.env`、`docker compose -f docker-compose.prod.yml config`、运行中容器环境或容器内 `get_settings()`；只输出目标非密钥字段，避免打印完整 `.env`。
  - `scratch/query_latest_messages.py` 等直连 `SessionLocal()` 的脚本连的是**本地库**，仅用于纯本地开发自测，不能当线上真实记录。
- **企微接口调试指南**：关于如何与 QiWe API 网关交互（包括拉取群成员、获取用户昵称等）、排查 `QIWEI_GUID` 等凭证问题、以及连接远程数据库运行诊断脚本的详细指引，请参考文档 [docs/qiwe_debugging_guide.md](file:///home/chr/桌面/ran_ai/docs/qiwe_debugging_guide.md)。
- **修改后端代码后必须重启**：修改 `backend/src/` 下的 Python 文件后，**必须**运行 `docker restart ran-ai-backend` 重启后端服务，否则改动不会生效。这是最容易忘记的步骤，每次修改后端代码后都要检查是否需要重启。

## DEPLOYMENT & PRODUCTION UPDATES (部署与发布规范)
- **代码部署 vs 配置变更（必须分开）**：部署代码跑 `scratch/local_prebuilt_deploy.py`，它**只更新代码、不碰生产 `.env` 与 `docker-compose.prod.yml`**（打包已排除，解压不覆盖）；要改生产配置（密钥/数据库/QiWe guid 等）才单独跑 `scratch/configure_remote_files.py`（先改其内置模板再跑）。**严禁**在部署脚本里加回"覆盖 `.env`/`docker-compose.prod.yml`"的步骤，否则服务器上的手改配置会被覆盖、可能搞坏线上。详见 `docs/deployment.md` 1.5 节。
- **生产数据库使用阿里云 RDS**：生产环境通过 `DATABASE_URL` 连接阿里云 RDS PostgreSQL 15 + vector 扩展，`docker-compose.prod.yml` 不应再启动内置 PostgreSQL，避免生产数据分裂。本地开发仍使用 `docker-compose.yml` 的本地 PostgreSQL。（"以线上为准、禁止本地推断"的排查规则见 DEBUGGING 段）
- **本地预构建上传（严禁在 ECS 编译）**：为防止 ECS 资源卡死与编译时长时间不可用，前端编译必须在本地运行 `npm run build` 生成 standalone 制品后再打包上传。
- **保护生产依赖**：Next.js standalone 运行时所需的 node_modules 位于 `frontend/.next/standalone/node_modules/`。
  - 编写 `.dockerignore` 时，**严禁**全局排除 `node_modules`，必须使用前缀锚定（如 `/node_modules`）以保留子目录下的生产依赖。
  - 编写 `tar` 打包命令时，**严禁**全局过滤 `node_modules`（如 `--exclude='node_modules'`），必须限定为开发依赖目录（如 `--exclude='frontend/node_modules'`）。
- **容器内部健康检查**：Alpine/Slim 生产镜像中通常没有 `curl`。严禁在 `docker exec` 中直接执行 `curl` 进行健康状态探测，必须使用容器内已有的运行时引擎发起探测：
  - **后端（Python）**：`docker exec ran_ai-backend-1 python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"`
  - **前端（Node）**：`docker exec ran_ai-frontend-1 node -e "const http = require('http'); http.get('http://localhost:3000')"`
- **生产启动命令**：`docker compose -f docker-compose.prod.yml up -d --build`。完整指南：`docs/deployment.md`。

## TESTING PROTOCOL (用户只说"测试"时必须执行)
- **唯一测试规范入口**：收到"测试""完整测试""检查项目测试"等请求时，必须先阅读并严格执行 `docs/testing/README.md` 和 `docs/testing/TEST_CASES.md`；用户无需重复提供测试命令、测试用例或报告要求。
- **测试用例固定入口**：`docs/testing/TEST_CASES.md` 是可复用测试用例清单；不得把 `docs/reviews/` 里的历史执行记录当作测试用例来源。
- **默认只测试，不改码**：测试任务中严禁修改业务代码、测试代码、测试数据生成逻辑或预期结果来让测试通过。发现问题后只记录证据与修复建议，等待用户明确要求修复。
- **报告必须新建**：每次测试结束后，必须在 `docs/reviews/` 新建带当前日期时间的执行记录与总结报告；严禁直接把历史报告中的旧结果改成"通过"。
- **失败必须如实报告**：不得吞掉异常、跳过失败项或仅报告通过率。报告必须区分：通过、失败、跳过、未执行、环境阻塞，并记录对应命令与关键证据。
- **真实测试需授权**：默认禁止运行会调用真实 LLM、产生费用、向真实群发送消息或永久入库的测试。只有用户明确要求"运行真实 E2E/真实模型测试"时，才可按 `docs/testing/README.md` 的真实测试流程执行。

## ENGINEERING PRINCIPLES (工程化纪律)
- **全局视野优先**：审查与修改代码时，必须先理清上下游调用链路与系统架构，先抓大再抓小。严禁只顾局部语法而导致破坏整体数据流向。
- **拒绝"头痛医头"**：严禁通过简单的 `if/else` 堵漏或吃掉异常（Swallow Exceptions）来修 Bug。遇到深层架构问题应提出重构建议，该报错（Fail Fast）就立刻抛出。
- **防御性编程**：所有外部 I/O（DB、第三方 API、大模型调用）严禁假设绝对可靠。必须预判超时、字段缺失等异常，并提供合理的兜底（Fallback）或捕获机制。
- **最小化侵入**：修复局部 Bug 时，严格保持原有代码风格、目录结构和核心设计。非明确授权，严禁大范围重构或引入新的第三方依赖。
- **先查阅，后假设**：严禁仅凭文件名"脑补"函数定义。在调用任何内部模块前，必须先真实读取对应的源码文件以对齐参数和返回值。
- **及时提交**：完成修复或小功能后，必须立即主动 commit，不需要等待用户确认。按逻辑拆分提交，避免大量改动堆积在工作区。
- **未授权不改码**：若用户未明确要求修改代码（如仅提问、讨论、审查），只输出分析和修改建议，询问用户是否执行，不得擅自改动文件。
- **解释原则**：向用户解释功能时，必须使用通俗语言，说明"具体是什么功能"和"举例"，避免堆砌代码字段名。用户不关心代码细节，只关心功能含义和实际效果。
- **提示词与代码改动的优先级**：业务规则默认**先改系统提示词、跑测试看效果**；提示词优化空间用尽或效果不达标，再上代码硬约束。**禁止软硬并行提交**，避免调试时无法定位是提示词没写对还是代码逻辑没走对。例外仅限风控编码解析、文档强一致性约束等必须代码兜底的场景。
- **经验沉淀（跨工具自主进化）**：运行中发现的坑/隐性约定，只有**够硬、够值钱**——即"不写下来下次几乎一定还会踩、且影响面大"的那种——才写进本文件（`AGENTS.md` = `CLAUDE.md`，两个工具都读）。这是唯一的沉淀位置，**不另设经验文档**；Claude 也不能只写自己的私有 memory（Codex 看不见）。铁律：①**必须先向用户提议、经明确确认后再写**，严禁静默追加；②只写非常值得的少数，一两句讲清"是什么坑+怎么应对"，先去重（已有就更新别新开）；③临时调试细节、一次性现象一律不写，保持本文件精简权威。

## WIP 接力（跨工具/跨额度断点续做）
背景：Claude/codex 订阅有额度上限，可能在一个需求做到一半时无预警掐断，需切换工具续做；prompt 缓存绑定账号且短命，切回后重建缓存很贵。为此用一份持续更新的接力文档承接断点，让接手的工具（本机 CC/codex 均读得到）无缝续做。

- **固定位置**：`docs/wip/CURRENT_TASK.md`（已 gitignore，同一时刻只对应一个正在推进的需求）。
- **开局先看**：接到"实现/修改类"需求时，先检查该文件是否存在；存在则先读它接手，而不是从零重新理解。
- **接到新需求先建**：覆盖写入该文件，写清三件事——【需求目标】要做成什么、【为什么/约束】背景与硬约束、【验收标准】怎样算完成。
- **边做边更新（关键）**：每完成一步、每次 commit 后顺手更新"已完成 / 下一步 / 当前卡点"，保证任何时刻它都是最新进度——因为限额掐断没有预警，不能指望事后补写。
- **完成即删**：需求验证通过并部署上线后，删除该文件，保持目录干净（临时工作态，非业务数据，删除无损失）。
- **适用范围**：仅用于需要多步、可能跨会话/跨工具的实现类需求；纯提问、讨论、审查、一次性小改不必建。
