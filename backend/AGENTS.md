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
│   │   ├── case_library.py  # 优秀案例库检索
│   │   ├── checkin_questionnaires.py  # 打卡问卷模板
│   │   ├── checkin_service  # 打卡处理
│   │   ├── content_policy.py # 内容发送策略 (风控拦截 + AI 回复模式)
│   │   ├── document_service # 交付文件生成
│   │   ├── document_templates.py # 文档模板引擎 (处方单/执行单/阶段报告)
│   │   ├── docx_service.py  # python-docx 渲染落盘
│   │   ├── embedding_service.py # Embedding 向量生成
│   │   ├── intake_facts.py  # 入营事实确定性提取
│   │   ├── intervention_service.py # 风控干预分析 (LLM 风险/意图识别)
│   │   ├── knowledge_evolution.py  # 知识草稿自动生成
│   │   ├── knowledge_evolution_settings.py # 知识进化配置
│   │   ├── knowledge_faq_service.py # 结构化 FAQ 管理
│   │   ├── outcome_engine.py # 成效追踪引擎 (多里程碑 + 行动焦点)
│   │   ├── questionnaire_template.py # 入营问卷模板
│   │   ├── reminder_service # 定时提醒 + 知识进化调度 + outcome snapshot
│   │   ├── risk_service.py  # 风险判断
│   │   ├── service_case_queries.py # 服务单查询辅助
│   │   └── user_state_profile.py # 用户动态状态画像 (outcome → JSONB 投影)
│   ├── workers/       # 后台任务 (定时提醒调度器)
│   ├── db/            # 数据库会话管理
│   └── core/          # 配置和常量
├── alembic/           # 数据库迁移
├── tests/             # 自动化测试 (Mock LLM + 独立真实 LLM E2E)
│   ├── conftest.py       # Mock AIService + FastAPI test app
│   ├── test_01_intake.py # 入营全链路
│   ├── test_02_chat.py   # 聊天风控
│   ├── test_03_checkin.py# 打卡数据
│   ├── test_04_stage_reports.py # 阶段报告
│   ├── test_05_evolution.py # 知识进化
│   ├── test_06_toggles.py # 系统开关
│   ├── test_e2e_01_real_llm.py # 独立 E2E-01：28 天完整旅程 + 多用户画像矩阵 + RAG 自进化
│   ├── test_07_qiwe_channel.py # 健康自测 07：QiWe 聚合去重 + 失败兜底
│   └── test_08_outcome_engine.py # 健康自测 08：成效提取 + 状态更新 + 行动焦点
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
| 定时提醒 | `src/app/services/reminder_service.py` | 打卡提醒 + 阶段报告生成 + 知识进化调度 + outcome snapshot |
| 入营事实提取 | `src/app/services/intake_facts.py` | 问卷确定性事实抽取 (BMI/减重带) |
| 服务单查询 | `src/app/services/service_case_queries.py` | `get_active_service_case` / `get_service_case` |
| 成效引擎 | `src/app/services/outcome_engine.py` | 多里程碑阶段状态 + 行动焦点评分 + outcome snapshot |
| 用户状态画像 | `src/app/services/user_state_profile.py` | outcome events → JSONB state 投影 (action_focus / need_analysis / body_feedback) |
| Embedding 服务 | `src/app/services/embedding_service.py` | 知识 chunks 向量化 (OpenAI 兼容) |
| 内容策略 | `src/app/services/content_policy.py` | 风控拦截 + AI 回复模式判断 (manual_review / auto_send) |
| 风控干预 | `src/app/services/intervention_service.py` | LLM 风控识别 + service_case 风险/续购/退款合并 |
| 结构化 FAQ | `src/app/services/knowledge_faq_service.py` | FAQ 业务表 + 知识索引同步 |
| 案例库 | `src/app/services/case_library.py` | 优秀案例库检索 |
| 文档模板 | `src/app/services/document_templates.py` | 处方单/执行单/阶段报告/毕业报告模板 |
| DOCX 渲染 | `src/app/services/docx_service.py` | python-docx 落盘为 .docx |
| 风险判断 | `src/app/services/risk_service.py` | `is_high_risk` 等风险判定 |
| 打卡问卷模板 | `src/app/services/checkin_questionnaires.py` | 每日打卡问卷 schema 调度 |
| 入营问卷模板 | `src/app/services/questionnaire_template.py` | 入营问卷字段 JSON |
| 知识进化配置 | `src/app/services/knowledge_evolution_settings.py` | 知识进化定时参数 + 开关 |
| 自动化测试 | `tests/` | 56 用例 (Mock LLM + 真实 LLM E2E)，见 `tests/README.md` |

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
