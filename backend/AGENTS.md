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
│   │   ├── agent/           # 回复生成：LangGraph ReAct Agent (graph/tools/prompts/context_builder)
│   │   ├── archivist/       # 档案官：周期扫描抽 delta → 打卡投影/成效/FAQ/案例草稿 (async 流水线, 见下)
│   │   ├── qiwe_service/    # QiWe 消息聚合
│   │   ├── knowledge_base/  # 本地 Markdown FAQ fallback
│   │   ├── case_library.py  # 优秀案例库检索
│   │   ├── checkin_questionnaires.py  # 打卡问卷模板
│   │   ├── checkin_service  # 打卡处理
│   │   ├── content_policy.py # 内容发送策略 (风控拦截 + AI 回复模式)
│   │   ├── document_service # 交付文件生成
│   │   ├── document_templates.py # 文档模板引擎 (处方单/执行单/阶段报告)
│   │   ├── docx_service.py  # python-docx 渲染落盘
│   │   ├── embedding_service.py # Embedding 向量生成
│   │   ├── intake_facts.py  # 入营事实 + 安全减重空间/A-D带确定性计算
│   │   ├── intervention_service.py # 风控干预分析 (LLM 风险/意图识别)
│   │   ├── knowledge_evolution.py  # 知识草稿审核、查重与发布
│   │   ├── knowledge_faq_service.py # 结构化 FAQ 管理
│   │   ├── outcome_engine.py # 成效抽取/评分工具 (由档案官 archivist 周期调用, 非消息路径实时跑)
│   │   ├── questionnaire_template.py # 入营问卷模板
│   │   ├── reminder_service # 定时提醒 + 阶段报告 + 档案官扫描调度 + 打卡追问
│   │   ├── risk_service.py  # 风险判断
│   │   ├── service_case_queries.py # 服务单查询辅助
│   │   └── user_state_profile.py # 用户动态状态画像 (outcome → JSONB 投影)
│   ├── workers/       # 后台任务 (定时提醒调度器)
│   ├── db/            # 数据库会话管理
│   └── core/          # 配置和常量
├── alembic/           # 数据库迁移
├── tests/             # 自动化测试 (Mock LLM + 独立真实 LLM E2E)，test_01..test_15 + 若干专项 (agent/archivist/case/short_link/sse 等)
│   ├── conftest.py       # Mock AIService + FastAPI test app
│   ├── test_01_intake.py ~ test_08_outcome_engine.py # 入营/聊天风控/打卡/阶段报告/知识进化/开关/QiWe/成效
│   ├── test_09..test_15 # 退款/安全/agent 工具/图片翻译/档案官影子/矛盾追问/案例记忆/案例合成
│   ├── test_e2e_01_real_llm.py # 独立 E2E-01：28 天完整旅程 + 多用户画像矩阵 + RAG 自进化
│   └── ... # test_agent_reminders / test_case_search / test_events_sse / test_short_link 等专项
└── pyproject.toml     # 依赖和入口点
```

## Architecture — the big picture

改后端业务代码前先读本节，理清上下游数据流，再动局部。

### Messaging is layered (channel-agnostic core)
Message handling is split into three layers so business logic never touches a specific channel's API:
```
app/services/channels/     MessageChannel protocol, channel registry, QiWe adapter
app/services/ranren_ai/    RanrenAIEngine — channel-agnostic business engine
app/services/qiwe_service/ QiWe group-message aggregation
```
A stable `ingress` process accepts QiWe callbacks and commits them to RDS `inbound_events` before returning 2xx. The restartable business backend continuously claims that inbox, turns each event into a `ChannelMessage`, aggregates consecutive messages from the same user/group over an ~8s window, then hands the merged turn to `RanrenAIEngine` (engine.py). The persisted `EngineGraph` keeps orchestration flat (risk → reply prep → generate → guards → send); only the reusable **LangGraph ReAct agent** (`app/services/agent/graph.py::generate_agent_reply`, agent⇄tools loop with a tool-call cap) remains as an inner graph. Both graph topologies are precompiled at process startup and shared by every service case; ReAct keeps one checkpointed variant for inbound replies and one no-checkpointer variant for scheduled reminders, which are two runtime variants of the same topology rather than another graph layer. Per-invocation engine, case, model, and tool bindings travel through LangGraph `Runtime.context`, never through shared mutable graph fields. Both layers use one stable, hashed `thread_id` per inbound turn and PostgreSQL checkpoints, so an interrupted run resumes at its unfinished node. **To send messages, always go through the `channels` abstraction — never call a channel API directly.**

Checkpoint does not replace transport durability: `inbound_events` is the cross-container inbox and protects callbacks while the business backend is stopped or while messages wait in the ~8s aggregation window. Its dispatcher uses `PENDING → PROCESSING → PROCESSED`, prioritizes QiWe spam-block notices, expires ordinary backlog older than five minutes without auto-replying, and links each event to the durable inbound `Message` in the same transaction that creates it. Outbound `messages` rows in `SENDING/SENT/FAILED/UNKNOWN` remain the record-first ledger around the external QiWe API. Checkpoint tables are initialized by `ran-ai-checkpoint-setup` as a controlled deployment step; application startup only opens and validates the psycopg pool.

### Reply generation is an agent (`app/services/agent/`)
`generate_agent_reply` runs the process-shared inner `CompiledStateGraph` (agent node ⇄ tools node) over invocation-scoped `base_model.bind_tools(...)`. The model may emit `<no_reply>` to stay silent. Before sending, the outer EngineGraph re-checks staff-took-over / newer-inbound guards and drops the reply if stale.

聊天 agent 能拿到的外部信息分**两类**：

**① 每轮自动注入**（不调工具，`run()` 拼进提示词）：当前北京时间（`_current_time_context`）；本轮用户消息 + 说话人 + 近期群聊；**隐性用户背景**（`profile_memory_service` 把 `UserProfile` + `UserStateProfile` + `ArchivistCaseMemory` 压成"结论小抄"：稳定事实/历程小结/长期偏好/稳定习惯/当前状态/最近话题/风险边界，每域最多 5 条取最新、每行≤120字）；**参考问答**（首次 LLM 调用前按意图识别产出的 nullable `knowledge_query` 决定：字符串则预检索、明确 `null` 则跳过、字段缺失/异常则回退本轮原问题；始终排除优秀案例）；skill 目录（仅名字+简介）。**完整带证据的 `state_json` 档案不再给 agent 查**——结论已在隐性背景里；完整档案给人从 `/users/{id}` 页面看。

**② 按需调工具**（22 个，`agent/tools/`）：
- 服务单只读 7 个（`_is_data_tool`，查不到走安全兜底不编造）：`get_service_progress`、`get_recent_checkins`、`get_weight_trend`、`list_delivery_documents`+`read_delivery_document`、`list_group_files`+`read_group_file`。
- 优秀案例 1 个：`search_success_case`（仅在用户明显受挫、平台期挫败、自我怀疑、动力低或想放弃时按需调用；普通参考问答不是工具，见下）。
- 定时提醒 3 个：`create_group_reminder`/`list_group_reminders`/`cancel_group_reminder`（定时任务自身触发那次运行会关掉，`include_reminder_tools=False`，防提醒套提醒）。
- 打卡追问偏好 2 个：`get_checkin_followup_preferences`/`update_checkin_followup_preferences`。
- 五项追问清单 3 个：`get_checkin_checklist`/`set_checkin_checklist`/`restore_default_checkin_checklist`（设置/恢复只接受当前群已识别工作人员同一条消息明确 @ 机器人；清单独立版本化，不依赖系统发送文件）。
- 服务生命周期 3 个：`set_service_start_date`/`continue_service`/`end_continued_service`（只能由当前群聊显式 @ 机器人的消息触发；开始日期控制 28 天按天任务且不依赖问卷，续服沿用原服务天数、不新建周期）。
- 技能 2 个：`skill`（读 SKILL.md 全文）/`read_skill_file`。
- 联网 1 个：`web_search_lifestyle`（Tavily，硬拦健康/身体指标/隐私类，每轮≤2 次）。

### Knowledge retrieval / RAG (reference QA + success-case tool)
两条链路共用 DB 表 `knowledge_chunks`，但检索时明确隔离，不混合返回：

- **参考问答条件预检索**：普通用户消息进入回复 Agent 前本来就会做一次风控/运营意图识别；该模型同时结合当前消息和近期对话产出 nullable `knowledge_query`，不增加独立 Query Rewrite 模型调用。仅当消息包含健康服务通用知识、产品规则、饮食/运动/执行方法问题时，输出最长 300 字的独立查询（只做指代消解、补全省略和去口语化，不得回答或新增事实）；实时数据查询、系统操作、纯数据上报、确认收尾和普通闲聊输出 JSON `null`。混合消息只改写其中的知识问题。`EngineGraph` 将原始 `user_query` 和 `knowledge_query` 分开传递，三态语义必须保留：字符串 → `_build_knowledge_context()` 检索；明确 `null` → 注入“暂无相关参考问答”且不调 embedding/DB；字段缺失、空字符串或非法类型（含工作人员跳过意图识别）→ 回退原问题检索。实际检索始终传 `exclude_source_types={"case"}`，使用向量（pgvector）+ 白名单关键词（pg_trgm ILIKE）双路 RRF；embedding key 未配时仅走关键词。
- **优秀案例按需检索**：`search_success_case` 已在 `build_agent_tools()` 注册。只有 Agent 判断用户明显受挫、平台期挫败、自我怀疑、动力低或想放弃时才调用；Agent 在生成唯一的字符串参数 `query` 时结合最近对话完成同样的指代消解、上下文内省略补全和去口语化，保留数字/时间/否定/不确定表达，不得新增事实，不另设结构化检索条件或独立改写调用。工具调用 `retrieve_case_context_db()`，只查 `source_type='case'`，仅走向量检索、最多返回 1 条，无关键词 fallback；未配置 embedding 或无达标结果时返回无案例。

本地 Markdown **不是未命中后的运行时 fallback**。`knowledge_base.py::retrieve_knowledge()` 当前没有生产调用点；本地素材必须先列入 `knowledge_base/knowledge_sources.json` 的 `active` 清单，再经 `/knowledge-base/index/sync` 同步为 `source_type='markdown'` 的 DB chunk，才会参与上面的参考问答检索。旧 Markdown 目前只在 `retained_legacy` 留痕，默认不启用。关键词候选严格限于 `keyword_whitelist.json`，不要把任意 jieba token 都拿去查库。

知识草稿在审核通过时统一用向量只找候选，命中候选时才额外调用一次结构化 LLM 判定，不设“完全一致文本直接跳过”的业务分支。`knowledge_drafts.content_hash` 只按来源消息与扫描上下文防同一提取任务重放，不参与知识重复判断。普通重复由系统跳过或版本化更新；只有同一问题的业务回答冲突返回审核员选择正确口径。FAQ 历史由 `knowledge_faq_versions` 留存，优秀案例替代时旧 chunk 标为 `superseded`；每次查重与裁决追加写入 `knowledge_dedupe_events`。案例查重必须先限定同一 `service_case_id`，不能因不同用户经历相似就合并。

### Documents & profiles
Questionnaire submission → a structured safety-factor extraction (`_extract_safety_factors`, one small call) → deterministic intake facts calculate the safe-reduction room and exact A/B/C/D band → LLM generates a user profile and explains that fixed band. The extraction must run *before* the facts: the band is written into every goal and path in both documents, so it cannot be corrected after the main generation call. It feeds two things, and they are deliberately separate. (1) How far the protected lower bound is raised above the BMI-20 auto-review line — +0.5 BMI per risk category (muscle clearly insufficient / major illness or medication / recovery risk), capped at +1.5, with a doctor-specified floor winning when more conservative. This is the *only* way risk moves the band: a raised floor narrows the safe room, and the band follows the number. (2) Whether the questionnaire exposes a red-risk condition (pregnancy/lactation, adolescents, eating-disorder risk, major medical conditions, red-flag symptoms) — this **does not change the band**; it forces the document-review marker, which holds both documents from auto-sending (`document_manual_review_hold`, not bypassable by `auto_send`) until operations confirms. A case already at `risk_level=high` lights the same marker via `_force_high_risk_manual_review`. Extraction failure falls back to no adjustment and no marker rather than blocking delivery. Service-case detail can generate two delivery docs (处方单, 28天执行单) and stage reports (3/7/14/21/28-day). The 28-day point ends the current service phase and does not generate a separate graduation report. Flow: LLM emits **structured JSON** for key fields → backend templates (`document_templates.py`) render editable Markdown → persisted as `content_json`/`content_md` → only at download/send time does `docx_service.py` render a temporary `.docx` (deleted after). **The authoritative system prompts live in `backend/src/app/services/document_service.py`; `手动版提示词.md` is reference only and must not be used at runtime.**

### Archivist — outcome / check-in / knowledge extraction (NOT per-message, NOT on the reply path)
成效抽取**不在消息主链路上实时跑**。消息进来只负责入库/回复；结构化抽取由独立的「档案官」`app/services/archivist/` 承担，在 **scheduler 进程** (`ran-ai-scheduler`) 里周期扫描完成：

- **触发** (`archivist/trigger.py::run_archivist_scan`)：`reminder_service.check_archivist_once` 按 `reminder_check_interval_seconds` 周期调用。**DB 派生找候选服务单 + 行锁防双跑**（重启不丢，不用内存 timer）。闸门：仅当累积 delta 含关键类目（可结构化打卡 / 稳定个人事实 / 阶段成效）且用户停说约 3min 才走 Fast 快触发；无增量则 `no_delta` 跳过。
- **抽取** (`archivist/graph.py`)：普通 async 流水线（load_context → extract → commit），不使用 LangGraph/ReAct。每批 delta 只调用一次挂载 `report_*` schema 的模型；一批可跨多天，模型在同一响应里多次上报，工具只承载结构化输出、不落库。历史只传结构化事实、用户历程小结和优秀案例素材小结，不重复传已归档聊天原文。
- **产物**：同一次模型输出覆盖打卡、画像、成效、状态、纠正、FAQ、两类滚动小结和优秀案例候选；确定性代码校验后与水位线在同一事务写入，不设后置衍生任务。只有本批写入新正向成效且模型确认变化显著稳定时才创建优秀案例草稿。`outcome_engine.py` 是档案官调用的成效写入/评分工具，`user_state_profile.py` 仍把 outcome 投影为 JSONB `user_state_profiles`（action_focus / need_analysis / body_feedback / risk_profile），AI 回复只读取用户历程小结，不读取优秀案例素材小结。
- 打卡追问 (`archivist/followup.py`) 由外部确定性触发：每 case 每天最多一条，共用 `ReminderEvent(rule_id="checkin_followup")` 天闸（见 memory「打卡追问线上已开启」）。

### Agent observability
`agent_runs` 是回复对话轮次、Agent 定时提醒和档案官实际运行批次的统一账本；明确记录成功、无输出、主动不回复、转人工、被新消息覆盖、功能关闭与失败等终态。回复 ReAct 的每次模型调用和档案官每批唯一一次模型调用都关联到 `ai_usage_logs.agent_run_id`，可执行工具调用写 `agent_tool_calls` 并用模型 tool-call ID 防 checkpoint 重放重复计数。观测写入使用独立异步事务、失败只告警，不改变发送、归档、水位线或 checkpoint 语义。

### Risk control
MVP risk is identified by **LLM intent/info extraction**, not hardcoded keywords. High-risk users never auto-receive AI content (forced human review); only the preset high-risk safety script may send immediately. Content policy (`content_policy.py`): `manual_review` (default — AI content goes to human review) vs `auto_send` (low-risk auto-sends, high-risk still reviewed).

### Auth model
Backend admin `/api/*` requires header `X-Admin-API-Key` (`BACKEND_ADMIN_API_KEY`); `/health` and public questionnaire (token-based) are exceptions. The admin key lives **only** between the Next server and FastAPI — never expose it to `NEXT_PUBLIC_*` or browser code. The frontend proxies backend API same-origin.

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
| 燃人 AI 引擎 | `src/app/services/ranren_ai/engine.py` | 渠道无关业务逻辑（风控/入库/守卫），回复委托给 agent |
| 回复生成 Agent | `src/app/services/agent/graph.py` + `src/app/services/agent/checkpoint.py` | `generate_agent_reply` LangGraph ReAct；PostgreSQL checkpoint/初始化在 `checkpoint.py`，工具在 `agent/tools/` |
| 档案官 | `src/app/services/archivist/trigger.py` + `graph.py` | 周期扫描抽 delta：打卡投影/成效/FAQ/案例草稿；触发/行锁见 `trigger.py` |
| QiWe 聚合 | `src/app/services/qiwe_service/aggregator.py` | 群消息聚合 |
| 数据库知识索引 | `src/app/services/knowledge_index.py` | `knowledge_chunks` 检索，支持 embedding/关键词 fallback |
| 知识草稿发布 | `src/app/services/knowledge_evolution.py` | 档案官候选的审核、查重、FAQ/案例发布 |
| 本地知识库 fallback | `src/app/services/knowledge_base.py` | Markdown/LlamaIndex 降级检索 |
| 定时提醒 | `src/app/services/reminder_service.py` | 打卡提醒 + 阶段报告生成 + 档案官扫描(`check_archivist_once`) + 打卡追问兜底 |
| 入营事实提取 | `src/app/services/intake_facts.py` | 问卷确定性事实抽取（BMI/安全减重空间/A-D带） |
| 服务单查询 | `src/app/services/service_case_queries.py` | `get_active_service_case` / `get_service_case` |
| 成效引擎 | `src/app/services/outcome_engine.py` | 成效抽取/评分工具，由档案官周期调用（非消息路径实时跑）|
| 用户状态画像 | `src/app/services/user_state_profile.py` | outcome events → JSONB state 投影 (action_focus / need_analysis / body_feedback) |
| Embedding 服务 | `src/app/services/embedding_service.py` | 知识 chunks 向量化 (OpenAI 兼容) |
| 内容策略 | `src/app/services/content_policy.py` | 风控拦截 + AI 回复模式判断 (manual_review / auto_send) |
| 风控干预 | `src/app/services/intervention_service.py` | LLM 风控识别 + service_case 风险/续购/退款合并 |
| 结构化 FAQ | `src/app/services/knowledge_faq_service.py` | FAQ 业务表 + 知识索引同步 |
| 案例库 | `src/app/services/case_library.py` | 优秀案例库检索 |
| 文档模板 | `src/app/services/document_templates.py` | 处方单/执行单/阶段报告模板 |
| DOCX 渲染 | `src/app/services/docx_service.py` | python-docx 落盘为 .docx |
| 风险判断 | `src/app/services/risk_service.py` | `is_high_risk` 等风险判定 |
| 打卡问卷模板 | `src/app/services/checkin_questionnaires.py` | 每日打卡问卷 schema 调度 |
| 入营问卷模板 | `src/app/services/questionnaire_template.py` | 入营问卷字段 JSON |
| 自动化测试 | `tests/` | test_01..test_15 + 专项 (Mock LLM + 真实 LLM E2E)，见 `tests/README.md` |

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
