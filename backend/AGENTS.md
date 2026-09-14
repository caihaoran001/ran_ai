# 后端开发说明

本文件只记录后端独有的架构约束和代码定位。项目级协作、授权、提交、WIP、测试授权及通用工程规则以仓库根目录的 `AGENTS.md` 为准；这里不重复维护通用命令、目录细节和易变清单。

改动后端业务前，先读与任务直接相关的架构小节和定位表，再以源码、测试和当前配置核对实际行为。定位表用于找到入口，不代替对调用链的确认。

## 代码地图与任务定位

下面列的是稳定入口，不是完整文件清单。新增或迁移功能时，以源码中的实际调用方为准。

| 任务 | 主要入口 | 说明 |
| --- | --- | --- |
| 应用启动与生命周期 | `src/app/main.py` | FastAPI 应用、启动和关闭流程 |
| API 路由 | `src/app/api/routes/` | 按业务模块划分的 HTTP 入口 |
| 数据模型与数据库 | `src/app/models/`、`src/app/db/`、`alembic/` | SQLAlchemy 模型、会话和迁移 |
| 消息编排 | `src/app/services/ranren_ai/engine.py`、`engine_graph.py` | 入站消息、风控、回复守卫和发送编排 |
| 渠道抽象 | `src/app/services/channels/base.py`、`registry.py` | `MessageChannel` 协议和当前渠道注册 |
| QiWe 入站与聚合 | `src/app/services/qiwe_service/` | 回调入库、消息聚合、发送对账及非文本消息 |
| 回复 Agent | `src/app/services/agent/graph.py` | LangGraph ReAct 回复生成与 checkpoint |
| 回复提示词 | `src/app/services/agent/prompts.py` | 群聊回复的系统提示词与用户提示词 |
| Agent 工具 | `src/app/services/agent/tools/factory.py`、`agent/tools/` | 工具按服务数据、提醒、生命周期、技能、知识和联网等类别注册；不要在此维护工具数量或逐项名单 |
| 档案官 | `src/app/services/archivist/trigger.py`、`graph.py` | 调度扫描增量并写入打卡、画像、成效、FAQ 和案例相关产物 |
| 知识检索与发布 | `src/app/services/knowledge_index.py`、`knowledge_evolution.py`、`knowledge_faq_service.py` | `knowledge_chunks` 检索、草稿审核及 FAQ 同步 |
| 知识素材 | `src/app/services/knowledge_base/` | Markdown 素材、来源清单和关键词白名单；素材是否启用以数据库索引为准 |
| 入营事实与文档 | `src/app/services/intake_generation.py`、`intake_facts.py`、`document_service.py` | 问卷安全信息、确定性计算、画像和交付文件生成 |
| 文档与归档提示词 | `src/app/services/document_prompts.py`、`stage_report.py`、`archivist/extract_agent.py` | 分别定位入营文档、阶段报告与档案官的运行时提示词；改动前核对实际调用处 |
| 文档交付 | `src/app/services/document_templates.py`、`document_delivery.py`、`docx_service.py` | 文档结构、发送审核和临时 DOCX 渲染 |
| 定时任务 | `src/app/services/reminder_service.py` | 提醒、阶段报告和档案官扫描调度 |
| 风控与发送策略 | `src/app/services/risk_service.py`、`content_policy.py` | 风险判断及人工审核/自动发送策略 |
| 后端测试 | `tests/`、`tests/README.md` | 测试结构和快速入口；具体测试以实际目录和测试规范为准 |

## 架构不变量

### 消息可靠性与渠道隔离

- 入站回调先持久化为可恢复的入站事件，再交给后端处理；业务进程重启不能使已收到的回调丢失。聚合后的 `ChannelMessage` 要能追溯到对应的入站记录，并与业务消息在持久化边界上保持一致。
- 核心业务只接收统一的渠道消息，不依赖 QiWe 的请求格式或发送接口。所有出站消息都通过 `MessageChannel` 抽象，禁止在业务引擎中直接调用具体渠道 API。
- 出站发送采用“先记录、再请求、回写结果”的发送账本。网络超时或进程中断时，账本必须保留可追踪的发送状态；LangGraph checkpoint 只能恢复工作流，不能替收入站持久化和出站账本。

### 外层编排与内层回复 Agent

- `EngineGraph` 是消息处理的外层编排，负责风控、回复前后的新消息/人工接管等守卫，以及最终发送；`agent/graph.py` 的 LangGraph ReAct Agent 只负责生成回复和按需使用工具。
- 不要把业务守卫、发送账本或渠道调用塞进回复 Agent，也不要因为增加一个工具或回复分支再套一层互相重叠的编排图。请求级的服务单、模型和工具绑定必须保持在本次调用范围内，不能依赖跨请求共享的可变状态。

### 知识检索（RAG）

- 普通消息的意图分析会结合近期对话给出可空的 `knowledge_query`：知识问题改写后检索，非知识问题跳过检索；字段缺失、空字符串或类型不合法时才回退本轮原问题。回复 Agent 始终保留原始用户消息，不用改写结果替代对话内容。
- 普通参考问答和优秀案例是两条隔离的检索语义：普通知识检索 `knowledge_chunks` 时排除 `source_type='case'`；优秀案例只在回复 Agent 判断用户明显受挫、想放弃等场景时通过 `search_success_case` 查询案例索引。两类内容不能混合返回。
- 本地 Markdown 不是检索未命中后的运行时 fallback。素材必须先列入 `knowledge_base/knowledge_sources.json` 的启用清单，再通过 `/knowledge-base/index/sync` 同步为数据库中的 Markdown chunk，之后才参与参考问答检索；旧素材留存不等于已启用。
- FAQ 的业务记录与检索 chunk 必须保持同步。关键词候选只能使用业务白名单；不能把任意分词结果当成业务规则或检索条件。

### 文档、画像与安全计算

- 问卷提交后，先提取安全相关信息并完成确定性事实计算，再让模型生成画像和交付文档。安全边界、目标和审核标记不能依赖模型自由发挥，也不能在文档生成完成后才补算。
- 需要人工复核的风险标记必须阻止文档自动发送；`auto_send` 不能绕过人工复核闸门。文档模型只接收可交付的安全事实，不应把运营内部审核信息当作用户可见正文。
- 模型输出结构化数据，由后端模板生成 Markdown 并持久化；下载或发送时才渲染临时 DOCX。每次新生成文档都要在同一事务保留不可变的首稿快照（`user_document_originals`），运营编辑只改变当前稿，不能覆盖首稿。

### 服务单生命周期

- 客户退群后，`member_left_at` 非空的服务单不能仍处于服务中。退群收口和回群恢复统一调用 `service_case_activation.py` 的 `close_case_on_member_left` / `restore_case_on_member_return`；实时通知、进群核查和定时对账都不能绕过生命周期审计直接改状态或结束时间。

### 档案官与实时回复解耦

- 打卡、画像、成效、FAQ 和案例候选等结构化归档由 `archivist/` 在 scheduler 中扫描未归档增量完成，不在用户消息的实时回复路径上执行。实时路径的职责是入库、风控、回复和发送。
- 档案官提交水位线与本批确定性产物时保持同一持久化边界；重复扫描不能重复处理已归档增量。历史上下文优先使用已归档的结构化事实和滚动小结，不要每批重新发送全部聊天原文。
- `outcome_engine.py` 等成效处理由档案官调用；不要在聊天回复路径中另起一套实时成效抽取，避免阻塞回复并产生两套来源。

### 风控、观测与鉴权

- 风险识别结果决定内容是否进入人工审核；高风险服务单不能自动接收 AI 内容，人工审核策略不能被普通自动发送开关绕过。
- 回复轮次、定时 Agent 运行和档案官批次写入 `agent_runs`；模型用量和工具调用分别关联运行账本。观测写入使用独立事务，观测失败只能告警，不能改变回复、发送、归档或 checkpoint 的业务结果。
- 后端管理 API 使用 `X-Admin-API-Key`（配置项 `BACKEND_ADMIN_API_KEY`）鉴权；健康检查和公开问卷是明确例外。管理密钥只存在于服务端到后端的调用链，禁止进入 `NEXT_PUBLIC_*` 或浏览器代码。

## 测试入口

- 后端测试的目录结构、fixture、Mock LLM 和快速命令见 `backend/tests/README.md`；不要在本文件维护测试编号、数量或逐文件清单。
- 用户提出“测试”或“完整测试”时，先遵循仓库根目录 `docs/testing/README.md` 和 `docs/testing/TEST_CASES.md`。具体测试文件以当前 `tests/` 实际内容为准，历史执行报告不是测试用例来源。
- 真实模型、真实群消息和其他有外部副作用的测试遵循根目录测试规范中的授权边界；本文件不另行复制一套测试流程。
