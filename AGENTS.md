# PROJECT KNOWLEDGE BASE

燃AI 1.0 - 企业微信群内部运营系统 (FastAPI + Next.js + PostgreSQL)

## CONVENTIONS
- **后端**: Python 3.11+, FastAPI, SQLAlchemy 2.0 async, Pydantic v2, uv 包管理
- **前端**: Next.js 15, React 19, TypeScript, Tailwind CSS, npm 包管理
- **数据库**: PostgreSQL 15, asyncpg 驱动, 软删除 (`deleted_at` 字段)
- **AI**: OpenAI SDK 兼容 (默认 DeepSeek)
- **消息渠道**: `MessageChannel` 协议抽象，支持多渠道扩展

## ANTI-PATTERNS (CRITICAL RULES)
- **禁用同步DB操作**：严禁在后端执行同步数据库操作，全部使用 async，保证高并发性能。
- **保护 Alembic**：严禁直接手动修改已经生成并运行的 Alembic 迁移文件。
- **前端变量**：严禁在前端硬编码 API 路由地址，必须统一使用环境变量。
- **渠道隔离**：严禁在核心业务逻辑中直接调用企微 API，必须通过 `channels` 抽象层进行代理。
- **绝不物理硬删除**：严禁硬删除数据，必须使用软删除（设置 `deleted_at` 字段并结合条件唯一索引）。
- **杜绝关键词硬编码规则**：参考文件应作为大模型输入与知识库素材，业务逻辑判断必须优先使用大模型结构化输出，而非硬编码关键词判断。
- **QA 表四入库限制**：检索正文只放“问法/参考回复/回复结构”；分类、风险、转人工字段必须置于 metadata 中，防止污染检索文本。
- **关键词白名单局限**：Fallback 检索仅限使用 `keyword_whitelist.json` 的业务白名单。
- **FAQ 与 Chunk 同步**：审核通过/人工维护的 FAQ 必须保存到 `knowledge_faqs` 并同步至 `knowledge_chunks`，严禁只追加到 Markdown 文件。
- **打卡解析原则**：打卡结构化完全以问卷/表单提交的结构化字段为主，不再新增 `#打卡` 文本正则解析。
- **提示词来源**：系统提示词完全以 `backend/src/app/services/document_service.py` 内部定义的为准，原 `手动版提示词.md` 仅为参考，不得参与运行时 LLM 呼叫。
