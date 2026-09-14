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

## Architecture — 子系统索引（按需细读）

系统全景与各子系统的"为什么这么设计"已下沉到 **`backend/AGENTS.md` 的 Architecture 节**（消息分层、回复 Agent、RAG、文档/画像、档案官/成效、风控）。改动相关代码前先读那一节。这里只留一句话导航：

- **消息分层**（channel-agnostic）：常驻 `ingress` 接 QiWe 回调并先落 RDS `inbound_events` → 可重启 `backend` 持续消费 → `ChannelMessage` → ~8s 聚合 → `RanrenAIEngine`（风控/入库/守卫）。发消息一律走 `channels` 抽象，不直连通道 API。
- **回复生成**：持久化 `EngineGraph` 平铺风险/回复守卫/发送编排，内部只保留可循环复用的 **LangGraph ReAct Agent**（`agent/graph.py::generate_agent_reply` + `agent/tools/`）；两层 Graph 拓扑均在进程启动时预编译并由全部服务单共享（ReAct 按普通回复有 checkpoint、定时提醒无 checkpoint 预编译两个运行变体，不是新增一层），请求级 engine/服务单/工具绑定通过 LangGraph `Runtime.context` 隔离。每个入站 turn 使用稳定哈希 `thread_id` 写 PostgreSQL checkpoint，中断后从未完成节点恢复。聚合窗口 `inbound_events` 与外部发送 `SENDING/SENT/FAILED` 账本仍分别承担传输边界可靠性，不能被 checkpoint 替代。
- **知识检索 / RAG**：普通用户消息的意图识别会结合近期对话产出 nullable `knowledge_query`（指代消解、补全省略、去口语化，不增加独立模型调用）：仅健康服务通用知识、产品规则、饮食/运动/执行方法问题返回字符串并检索 DB `knowledge_chunks`（排除 `source_type='case'`），实时数据/系统操作/纯上报/确认收尾/普通闲聊返回 `null` 并跳过检索；字段缺失、空字符串或非法类型才回退本轮原问题。优秀案例仅在用户明显受挫/想放弃时由 Agent 调 `search_success_case`，并在生成工具 `query` 时按同一规则改写，只查 `source_type='case'` 的向量索引。原始 `user_query` 始终保留给回复 Agent。当前没有未命中后直接读取本地 Markdown 的运行时 fallback；本地文件还必须列入 `knowledge_base/knowledge_sources.json` 的 `active` 清单并同步成 `markdown` chunk 才参与检索，历史文件只保留不默认启用。
- **文档 / 画像**：LLM 出结构化 JSON → 模板渲 Markdown → 落库 → 下载/发送时才临时渲 `.docx`。每份新生成文件会在同一事务写入不可变的 `user_document_originals` 首稿快照（结构化 JSON + Markdown）；`user_documents` 只代表运营编辑后的当前稿，首稿不在前端展示但用于问题回溯。运行时系统提示词以 `document_service.py` 为准。
- **成效 / 结构化抽取（档案官）**：**不在消息路径实时跑**。`archivist/` 在 scheduler 里周期扫描（DB 派生 + 行锁，去抖 ~3min）；每批未归档 delta 只调用一次 tool-calling 模型（非 LangGraph/ReAct），同一响应上报打卡/画像/成效/状态/FAQ/用户小结/优秀案例素材小结/案例候选，随后与水位线在同一事务确定性落库。历史只传结构化事实和滚动小结，不重复传已归档聊天原文；`outcome_engine.py` 退为其调用的抽取工具，`user_state_profile.py` 投影 JSONB 画像。
- **Agent 运行观测**：回复轮次、Agent 定时提醒与档案官批次统一写 `agent_runs`；裸 `ChatOpenAI` 的每次模型调用补写 `ai_usage_logs.agent_run_id`，回复 ReAct 工具调用写 `agent_tool_calls`。观测账本用独立事务尽力保留成本和失败终态，不参与业务判断；后台 `/agent-operations` 只展示启用后的当前数据，不回填或伪造历史运行。
- **风控**：LLM 识别风险（非关键词），高风险强制人工审核；策略见 `content_policy.py`。
- **退群收口**：「服务中」与「客户在场」是绑定不变量——`member_left_at` 非空的服务单状态必然已完结（2026-08-21 起）。服务单的状态收口/接回恢复只能走 `service_case_activation.py` 的 `close_case_on_member_left` / `restore_case_on_member_return`（实时退群通知、进群核查、6h 对账自愈三处都挂它），严禁在别处直接赋值 `status`/`ended_at` 绕开生命周期审计判据，否则接回恢复会失灵、看板在服人数会失真。

**鉴权（跨栈安全红线）**：后端 `/api/*` 需 `X-Admin-API-Key`（`BACKEND_ADMIN_API_KEY`），`/health` 与公开问卷（token）例外。该 key **只**存在于 Next 服务端 ↔ FastAPI 之间，**严禁**暴露到 `NEXT_PUBLIC_*` 或浏览器；前端同源代理后端 API。

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
- **数据库枚举按大写成员名落库**：SQLAlchemy Enum 默认存成员名（如 `REVIEWING`），API 序列化才是小写 value；新增枚举值的 Alembic 迁移必须 `ADD VALUE '大写名'`，写小写等于白加且测试测不出（生产已因此留下 `archived`/`ARCHIVED` 双值残留，2026-08-21 加 SUPERSEDED 时又踩一次）。

## DEBUGGING & DIAGNOSTICS (调试与排查指南)
- **查看实时运行日志**：在排查消息收发、大模型调用、企微回调等问题时，**必须**在项目根目录运行 `docker logs -f --tail 100 ran-ai-backend` 查看最新日志。
- **查真实数据/配置一律以线上为准，禁止拿本地推断**：排查任何真实业务数据（聊天记录、服务单、消息 payload、画像等）或线上配置/运行状态时，**必须查生产**，严禁用本地 `.env`、本地 DB、`.env.example` 或代码默认值推断。原因：本地库是种子/测试数据，且**阿里云 RDS 只在 ECS 的 VPC 内可达，本地连不到 RDS**。
  - **查数据（RDS）**：SSH 进 ECS（`43.98.170.61`，见 `scratch/*remote*.py` 的 paramiko 写法或 `docs/qiwe_debugging_guide.md`）后 `docker exec -i ran_ai-backend-1 uv run python -` 跑查询脚本——只有容器内 `DATABASE_URL` 指向 RDS。
  - **查配置/运行状态**：读 ECS 上 `/root/ran_ai/.env`、`docker compose -f docker-compose.prod.yml config`、运行中容器环境或容器内 `get_settings()`；只输出目标非密钥字段，避免打印完整 `.env`。
  - `scratch/query_latest_messages.py` 等直连 `SessionLocal()` 的脚本连的是**本地库**，仅用于纯本地开发自测，不能当线上真实记录。
- **企微接口调试指南**：关于如何与 QiWe API 网关交互（包括拉取群成员、获取用户昵称等）、排查 `QIWEI_GUID` 等凭证问题、以及连接远程数据库运行诊断脚本的详细指引，请参考文档 [docs/qiwe_debugging_guide.md](file:///home/chr/桌面/ran_ai/docs/qiwe_debugging_guide.md)。
- **修改后端代码后必须重启**：修改 `backend/src/` 下的 Python 文件后，**必须**运行 `docker restart ran-ai-backend` 重启后端服务，否则改动不会生效。这是最容易忘记的步骤，每次修改后端代码后都要检查是否需要重启。
- **企微媒体下载单次限 512KB，必须 Range 续传**：企微媒体网关(`tpdownloadmedia`)单次响应最多返回 512KB，以 `206 Partial Content` + `Content-Range: bytes 0-524287/<total>` 分片返回（`206` 是成功码，`raise_for_status()` 不拦，截断会静默发生）。下载入站图片/文件必须按 `Range` 循环续传拼齐整份（见 `ranren_ai/attachments.py::_download_media_bytes`），否则大于 512KB 的图片（≈每张手机照片）只下到开头一截，解码后下半部渲染成灰块，视觉模型会误判"图片没传完整"。

## DEPLOYMENT & PRODUCTION UPDATES (部署与发布规范)
- **代码部署 vs 配置变更（必须分开）**：部署代码跑 `scratch/local_prebuilt_deploy.py`，它**只更新代码、不碰生产 `.env` 与 `docker-compose.prod.yml`**（打包已排除，解压不覆盖）；要改生产配置（密钥/数据库/QiWe guid 等）才单独跑 `scratch/configure_remote_files.py`（先改其内置模板再跑）。**严禁**在部署脚本里加回"覆盖 `.env`/`docker-compose.prod.yml`"的步骤，否则服务器上的手改配置会被覆盖、可能搞坏线上。详见 `docs/deployment.md` 1.5 节。
- **生产数据库使用阿里云 RDS**：生产环境通过 `DATABASE_URL` 连接阿里云 RDS PostgreSQL 15 + vector 扩展，`docker-compose.prod.yml` 不应再启动内置 PostgreSQL，避免生产数据分裂。本地开发仍使用 `docker-compose.yml` 的本地 PostgreSQL。（"以线上为准、禁止本地推断"的排查规则见 DEBUGGING 段）
- **本地预构建上传（严禁在 ECS 编译）**：为防止 ECS 资源卡死与编译时长时间不可用，前端编译必须在本地运行 `npm run build` 生成 standalone 制品后再打包上传。
- **保护生产依赖**：Next.js standalone 运行时所需的 node_modules 位于 `frontend/.next/standalone/node_modules/`。
  - 编写 `.dockerignore` 时，**严禁**全局排除 `node_modules`，必须使用前缀锚定（如 `/node_modules`）以保留子目录下的生产依赖。
  - 编写 `tar` 打包命令时，**严禁**全局过滤 `node_modules`（如 `--exclude='node_modules'`），必须限定为开发依赖目录（如 `--exclude='frontend/node_modules'`）。
  - 在临时 worktree/发布目录构建时，**严禁**把 `frontend/node_modules` 整体符号链接到主工作区后执行 `npm run build`；Next.js 文件追踪可能漏掉运行时依赖，出现“构建通过但容器启动时报 `MODULE_NOT_FOUND`”。必须使用真实依赖目录，并在上传前从 `.next/standalone` 启动一次 `server.js` 冒烟验证。
- **容器内部健康检查**：Alpine/Slim 生产镜像中通常没有 `curl`。严禁在 `docker exec` 中直接执行 `curl` 进行健康状态探测，必须使用容器内已有的运行时引擎发起探测：
  - **后端（Python）**：`docker exec ran_ai-backend-1 python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"`
  - **前端（Node）**：`docker exec ran_ai-frontend-1 node -e "const http = require('http'); http.get('http://localhost:3000')"`
- **生产启动命令**：`docker compose -f docker-compose.prod.yml up -d --build`。完整指南：`docs/deployment.md`。
- **改 `deploy/Caddyfile` 后必须 `docker restart ran_ai-caddy-1`**：Caddyfile 是 bind-mount，部署写入的是**新 inode 的新文件**；`docker compose up -d` 不因挂载文件内容变化重建 caddy，`caddy reload` 也只重读同一个旧 inode——两者都静默无效。不重启 caddy，改路由/换 upstream 名后它仍解析到旧 upstream（曾导致企微回调持续 502）。重启后用 `docker exec ran_ai-caddy-1 grep <新upstream> /etc/caddy/Caddyfile` 确认容器内已更新。
- **改到 ingress 上跑的代码，常规部署不会生效**：`local_prebuilt_deploy.py` 只 `up -d --no-deps backend frontend`，**刻意不重建 ingress**（它常驻接企微回调，重启有几秒空窗）。但 ingress 跑的不只是回调——**公开问卷提交（含入营问卷）也由它处理**，改这些路由后必须单独 `docker compose -f docker-compose.prod.yml up -d --no-deps --force-recreate ingress`（镜像与 backend 同名同 tag，部署时已重建好，约 16 秒恢复健康），再 `docker exec ran_ai-ingress-1 grep <新代码特征> /app/src/app/api/routes/questionnaires.py` 确认容器内确实是新代码；不做这步线上跑的仍是旧代码，且不报错、测试全绿，只能进容器 grep 才发现。推论：ingress 的 environment 只有 4 项（**没有 `AI_API_KEY`**），**严禁用"本进程有没有 key"当功能开关**——曾因此让入营提交不写"待生成"信号，8/2-8/4 提交的服务单全部没有交付文件且无任何报错。
- **构建缓存会滚雪球撑爆 ECS 磁盘**：后端在 ECS 上 `--build`，每次部署叠一层构建缓存且旧层永不自动删，会一路吃满磁盘（曾累积 43G，占满 85%）。`local_prebuilt_deploy.py` 已在部署收尾加 `docker builder prune -f` 兜底；若磁盘告急先跑它救急，并优先怀疑构建缓存而非业务数据（图片等占用极小）。
- **上传 ~80MB 部署包会静默断流，解压前必须校验**：`local_prebuilt_deploy.py` 用一次性 `sftp.put` 传包，在这台 ECS 上会中途断流且不抛错（远端只落一半，脚本干等），已连续踩中两次。半截包一旦解压就用残缺代码覆盖线上。用 `scratch/resumable_frontend_deploy.py` 的做法：分块写 + 断了按远端已有大小续传 + **解压前比对 sha256，不一致就中止**。
- **入站图片存容器磁盘、必须挂持久卷**：聊天记录原图存后端容器内 `/app/.wecom_cache/images`（**懒创建**，无图进来时目录不存在，别误判丢失），数据库只存路径+文字描述。已在 compose 给 backend 挂 `./wecom_cache:/app/.wecom_cache` 持久化，否则容器重建即丢原图、60 天保留期形同虚设。

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
- **先查阅，后假设**：严禁仅凭文件名"脑补"函数定义。在调用任何内部模块前，必须先真实读取对应的源码文件以对齐参数和返回值。**排查/改动某函数前，必须先 grep 确认它真被调用**——本仓库有重构遗留的死代码（定义在、零调用），其过时注释/docstring 会把排查带偏；别假设"有定义=在运行"，先验证调用链再下结论。
- **及时提交**：完成修复或小功能后，必须立即主动 commit，不需要等待用户确认。按逻辑拆分提交，避免大量改动堆积在工作区。
- **提交必须署名工具**：每次 commit 的信息末尾必须单独加一行 `Agent-Tool: <值>`，取值**只能**是 `claude-code`、`codex-cli`、`opencode-v4f`、`opencode-Ox Alpha Free` 四选一（固定字面量，禁止自创 `codex`/`Codex CLI`/`gpt-5`/`opencode` 等写法，否则按工具统计时会漏）。按**你自己实际所属的 CLI** 填（2026-08-21 起本机 opencode 环境一律 `opencode-Ox Alpha Free`，此前历史用 `opencode-v4f`），**严禁照抄** git log 里历史提交的署名行（历史里有 Claude 的 `Co-Authored-By`，别跟着模仿）。用户手工提交不带此行——**缺这行即代表人工改动**，故也严禁替人工提交补写。**该行必须与末尾其它署名行（如 `Co-Authored-By`）紧挨成同一段、中间不留空行**：git 只解析提交信息**最后一个段落**为 trailer，被空行隔开就退化成正文，`--format` 查不到、统计全落空（首次落地即踩此坑）。查询：`git log --format='%h %s | %(trailers:key=Agent-Tool,valueonly)'`。**新工具想加新值，必须先向用户提出并得到确认，再登记到本行**——严禁先按自己想的署上、再回头把规则改成允许（那样统计口径由工具单方面改写，用户不看 diff 就不知道多了一类）。
- **提示词与代码改动的优先级**：业务规则默认**先改系统提示词、跑测试看效果**；提示词优化空间用尽或效果不达标，再上代码硬约束。**禁止软硬并行提交**，避免调试时无法定位是提示词没写对还是代码逻辑没走对。例外仅限风控编码解析、文档强一致性约束等必须代码兜底的场景。
- **经验沉淀**：本文件即本项目的 `CLAUDE.md`；项目专属经验才写这里，通用写法归全局 `AGENTS.md`，写入前必须先提议并经确认。

## WIP 接力（跨工具/跨额度断点续做）
背景：Claude/codex 订阅有额度上限，可能在一个需求做到一半时无预警掐断，需切换工具续做；prompt 缓存绑定账号且短命，切回后重建缓存很贵。为此用一份持续更新的接力文档承接断点，让接手的工具（本机 CC/codex 均读得到）无缝续做。

- **固定位置**：`docs/wip/CURRENT_TASK.md`（已 gitignore，同一时刻只对应一个正在推进的需求）。
- **开局先看**：接到"实现/修改类"需求时，先检查该文件是否存在；存在则先读它接手，而不是从零重新理解。
- **接到新需求先建**：覆盖写入该文件，写清三件事——【需求目标】要做成什么、【为什么/约束】背景与硬约束、【验收标准】怎样算完成。
- **边做边更新（关键）**：每完成一步、每次 commit 后顺手更新"已完成 / 下一步 / 当前卡点"，保证任何时刻它都是最新进度——因为限额掐断没有预警，不能指望事后补写。
- **完成即删**：需求验证通过并部署上线后，删除该文件，保持目录干净（临时工作态，非业务数据，删除无损失）。
- **适用范围**：仅用于需要多步、可能跨会话/跨工具的实现类需求；纯提问、讨论、审查、一次性小改不必建。

## 实验群（开发/测试用）

- **群名**：`NAR -高燃人生-0518线下`
- **group_id**：`10928203839633338`
