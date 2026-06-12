# 燃人AI 1.0 - 企业微信群内部运营系统 后端与架构深度审计报告

本审计报告对 `ran_ai` 项目的整体后端架构、数据库交互、后台任务调度（Scheduler）、AI 服务容灾以及前端 Next.js 15 数据渲染层进行了全方位的代码走读与静态分析。报告梳理出了目前系统在生产环境高可用、高并发及大规模运营场景下面临的潜在隐患，并给出了具体而清晰的工程级优化建议。

---

## 🔍 核心审计发现与优化清单

### 1. ⚙️ 定时任务调度器竞态条件与分布式锁缺失（高风险）
*   **当前实现**：
    *   在 `ReminderService` 中，后台提醒任务、问卷发放和每日 FAQ 知识进化草稿扫描以一个独立的定时循环逻辑运行（在 `scheduler.py` 中起独立 asyncio 循环）。
    *   提醒逻辑的防重依赖于在数据库写入 `ReminderEvent`（例如 `rule_id="daily_checkin"`, `event_date=today`）。
*   **潜伏隐患**：
    *   **时序漏洞 (TOCTOU)**：系统在判断“今天是否已提醒”后，**立即调用外部 WeCom API 发送消息，发送成功后才创建并 Commit 数据库事务中的 `ReminderEvent`**。
    *   在真实的生产高可用部署下（如多实例副本容器部署，或单实例中误启动了多个 Scheduler 线程/进程），如果两个实例在同一秒内检测到同一服务单“尚未提醒”，它们会同时判定为 `False`，同时调用外部 API 发送提醒，最终产生**重复向同一个微信群群发消息**的恶性体验 Bug。
*   **优化方案**：
    *   **行级锁/咨询锁 (SELECT FOR UPDATE / Advisory Lock)**：使用 PostgreSQL 提供的排他锁或咨询锁锁定该服务单，或引入 Redis 建立分布式红锁（Redlock）。
    *   **状态先置（Optimistic Check）**：先向数据库写入“发送中/已发送”标记，在事务 Commit 成功后再触发网络 API 调用；或者在数据库中为 `ReminderEvent` 的 `(service_case_id, rule_id, event_date, event_type)` 建立物理唯一联合索引，通过物理约束天然防御并发 race。

---

### 2. ⚡ 异步事件循环中的 CPU 密集型/文件 IO 阻塞（中风险）
*   **当前实现**：
    *   在 `document_service.py` 中，生成交付 DOCX 文档时直接在 `async` 上下文中同步调用了 `write_template_docx(content_json, output_path)`。
*   **潜伏隐患**：
    *   `python-docx` 是一个完全同步、CPU 绑定的文档组装库。在渲染含有复杂表格、文本分块以及调用微软雅黑字体进行元数据映射编码时，可能会造成 0.2~1 秒的线程停顿。
    *   在 Python 异步单线程模型下，**如果在主异步上下文直接同步执行此类 CPU/IO 操作，事件循环将被瞬间挂起（Blocked）**。这意味着在文档生成的 1 秒内，整个后端 API 无法响应其他请求，高频的微信消息回调也会发生超时报错。
*   **优化方案**：
    *   **下推至线程池**：利用 Python 异步生态的 `asyncio.to_thread`（或自定义 `ThreadPoolExecutor`），将耗时的文档写盘操作隔离在工作线程中执行：
        ```python
        import asyncio
        await asyncio.to_thread(write_template_docx, content_json, output_path)
        ```
    *   这可以保证主事件循环 100% 实时响应高并发请求。

---

### 3. 🌐 AI 外部连接超时、限流与容灾退避机制不足（中风险）
*   **当前实现**：
    *   `ai_service/client.py` 中的 `generate_text`、`extract_intervention_insight` 等核心大脑服务，直接通过 OpenAPI Client 进行网络请求。
*   **潜伏隐患**：
    *   直连第三方大模型服务（如阿里 DashScope 或 DeepSeek API）极易遇到瞬时网络抖动、模型服务 503 临时不可用、或高频访问时触发 HTTP 429 速率限制（Rate Limit）。
    *   如果没有任何重试与备份冷切换机制，单次 API 失败将直接阻断用户的问卷画像生成、打卡解析及实时聊天互动，缺乏企业级服务的弹性（Resilience）。
*   **优化方案**：
    *   **引入指数退避重试**：使用 Python 健壮的 `tenacity` 库在 `client.py` 中为大模型 API 请求配置带有指数抖动退避（Exponential Backoff with Jitter）的自动重试，过滤 transient errors（如 429 或临时 502/503）。
    *   **多模型灾备冷备方案 (Active-Passive Fallback)**：配置备用 client 密钥（如配置一个廉价且速度快、稳定性极佳的 OpenAI / 阿里备用系列模型），当检测到主模型抛出严重不可恢复异常时，秒级无感降级至备用大模型，保障服务 99.99% 在线率。

---

### 4. 🗃️ 知识向量化（Embedding）的大规模冷启动速度瓶颈（低风险）
*   **当前实现**：
    *   为了防止触发阿里 API 的最大单批次 `10` 个限制，我们把同步 missing chunks 的批大小（Batch Size）重构为极其安全的 `8`。
    *   但是在循环生成向量的 `embed_missing_chunks` 逻辑中，批次之间采用的是串行单步 `await` 等待。
*   **潜伏隐患**：
    *   在小规模测试（180 个块）下没有问题，但中长期如果导入成千上万条 FAQ 和群聊沉淀知识块，单线程串行等待会让冷启动初始化向量化的过程耗时长达数十分钟。
*   **优化方案**：
    *   **并发批处理批次**：在确保不突破第三方 API 的每分钟限制（RPM/TPM Limit）的前提下，使用 `asyncio.gather` 配合 `asyncio.Semaphore(3)` 控制最大连接并发数，并行化生成多个批次的 Embedding，将冷启动与大容量数据同步效率提高 3~5 倍。

---

### 5. 🎨 前端 Next.js 15 页面静态“冻结”风险与水合调优（低风险）
*   **当前实现**：
    *   看板页、详情页、画像展现等高动态页面数据实时从 Postgres 提取。
*   **潜伏隐患**：
    *   **静态编译冻结**：Next.js 15 拥有非常激进的默认缓存策略。如果在编译（Next Build）时，由于没有正确声明，导致页面被打包器静态优化为了 `.html` 文件，则在生产环境中用户无论如何刷新页面，都可能看到“编译打包时刻数据”的冷冻切片。
*   **优化方案**：
    *   **强声明动态行为**：在所有高频获取实时数据库数据的前端路由组件中，明确加入：
        ```typescript
        export const dynamic = "force-dynamic";
        ```
    *   **水合压制优化**：对于客户端与服务端解析可能存在环境差异的数据渲染（如 IP、容器名称下载链接、或基于时区的 Date 格式化），我们在多处加入的 `suppressHydrationWarning` 属性非常完美，中长期推荐统一采用 React 19 标准的 `useEffect` 挂载状态判断，以保证组件生命周期的纯洁性。

---

## 🛠️ 下一步行动建议

1.  **为 `document_service.py` 补充 `asyncio.to_thread`**：将 `write_template_docx` 写盘操作进行非阻塞线程包裹，消除高频生成时的 UI 卡顿隐患。
2.  **为 AI Client 配置 `tenacity` 指数重试**：防范外部大模型网关的突发性网络阻塞与 429 访问受限。
3.  **在 Next.js 关键数据看板文件顶部添加 `force-dynamic` 物理声明**：防止上线后页面被 NextJS 15 自动编译为静态冷冻快照。
