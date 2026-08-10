# 燃人AI 自动化测试用例与 OpenCode 执行计划

本文档是 OpenCode / AI Agent / 开发者执行自动化测试时的固定测试用例入口。目标是**全面自动化测试**，不是人工肉眼点页面。

执行测试时先读：

1. `AGENTS.md`
2. `docs/testing/README.md`
3. `docs/testing/TEST_CASES.md`

`README.md` 规定测试边界、命令和报告格式；本文档规定具体要测什么。

---

## 0. 执行方式分层

OpenCode 执行测试时必须覆盖三类自动化：

| 层级 | 执行者 | 方式 | 作用 | 是否人工 |
|---|---|---|---|---|
| L1 | 后端/前端命令 | pytest、tsc、repo Playwright spec | 可重复、可回归、适合 CI | 否 |
| L2 | OpenCode 自动化 | Playwright MCP + API/DB 查询 | 自动打开页面、构造数据、点击流程、截图取证 | 否 |
| L3 | 真实模型自动化 | 诊断控制台 E2E-01 或授权后 pytest | 真实 LLM 链路和内容质量评审 | 否，但必须授权 |

执行规则：

- L1 必跑，除非环境阻塞。
- L2 必跑，使用 OpenCode 自带 Playwright MCP；不得让人手工点页面代替。
- L3 默认不跑，只有用户明确授权真实 E2E 时才跑。
- 每个用例报告必须标明执行方式：`repo-spec`、`opencode-mcp`、`api-db-check`、`real-e2e`、`未执行` 或 `环境阻塞`。
- 页面测试必须区分“页面加载成功”和“交互流程成功”。

---

## A. Repo Spec 与命令级自动化（L1）

目标：先用可重复命令确认核心模块不破，再让 OpenCode 做页面与内容深测。

| ID | 命令/入口 | 覆盖范围 | 最低验收 |
|---|---|---|---|
| SPEC-01 | `cd backend && PYTHONPYCACHEPREFIX=/tmp/ran_ai_pycache uv run python -m compileall src scripts` | 后端语法与导入 | 命令通过 |
| SPEC-02 | `cd backend && uv run pytest tests -q` | 后端 01-08 套件 | 全部通过；失败时记录具体测试名和输出 |
| SPEC-03 | `cd frontend && npx tsc --noEmit --incremental false` | 前端类型检查 | 命令通过 |
| SPEC-04 | `cd frontend && npm run test:e2e -- --list` | repo Playwright spec 可发现 | 能列出 `frontend/e2e/admin-smoke.spec.ts` 中的测试 |
| SPEC-05 | `cd frontend && npm run test:e2e` | repo Playwright 基线 | 若前端服务和浏览器依赖可用则运行；否则标记环境阻塞 |

当前 repo Playwright spec 只覆盖后台核心页面 smoke，不等于完整页面测试通过。完整页面交互由 OpenCode MCP 执行 PW 用例。

---

## B. 安全健康自测（诊断控制台 01-08）

目标：验证核心流程通畅，不调用真实 LLM，不向真实群发消息，测试数据回滚。

| ID | 执行方式 | 范围 | 最低验收 |
|---|---|---|---|
| HC-01 | repo-spec | 入营问卷 | 问卷提交、画像生成、两份交付文档创建成功；确定性事实不丢失 |
| HC-02 | repo-spec | 聊天风控 | 普通、高风险、退款、续购、拒绝机器人继续解释等场景能得到正确状态 |
| HC-03 | repo-spec | 打卡 | 打卡创建、结构化字段、排序与展示一致 |
| HC-04 | repo-spec | 阶段报告 | 3/7/14/21/28 天阶段报告生成链路可用，毕业报告入口已停用 |
| HC-05 | repo-spec | 知识沉淀 | 档案官生成 FAQ/案例候选、人工审核入库、知识块同步可用 |
| HC-06 | repo-spec | 策略开关 | 文档发送、AI 回复、软删除等开关生效 |
| HC-07 | repo-spec | QiWe 通道 | 聚合、幂等去重、问卷指令、发送失败兜底不破坏回调 |
| HC-08 | repo-spec | 成效引擎 | 成效提取、阶段状态、行动焦点、时间感知追问可用 |

必须记录：

- 诊断控制台是否显示 `01-08` 连续编号。
- `E2E-01` 是否独立显示，且未被安全健康自测自动执行。
- 若失败，记录具体套件、命令、关键日志和是否有业务数据残留。

---

## C. 契约与内容准确性测试

目标：验证同一事实在不同模块中的表达一致，避免“流程通了但内容错了”。

| ID | 执行方式 | 契约 | 最低验收 |
|---|---|---|---|
| CT-01 | api-db-check + opencode-mcp | 入营问卷 → 画像 → 文档 | 姓名/称呼、性别、年龄、身高、体重、BMI、腰围、减重带、28天目标不冲突 |
| CT-02 | api-db-check | 文档打卡项一致性 | 《28天执行标准》最终 Block 7 = `profile.personalized_checkin_fields` = 每日行动问卷 API 字段 |
| CT-03 | api-db-check | 打卡字段类型 | 含“是否”的字段为 RADIO，选项为“是/否”；评分/体重等非判断题为输入字段 |
| CT-04 | api-db-check | 最新文档优先 | 已有旧画像默认字段时，每日行动链接仍优先读取最新执行文档 |
| CT-05 | api-db-check + opencode-mcp | 文档口径一致 | 处方单与执行标准的蔬菜、运动、睡眠、产品节律建议不互相矛盾 |
| CT-06 | api-db-check + opencode-mcp | 风险/人工介入一致 | 高风险、退款、投诉、拒绝机器人继续解释应在服务单、审核控制台、提醒中一致呈现 |
| CT-07 | api-db-check | 成效数据一致 | 成效事件、阶段状态、阶段快照、用户动态画像不互相矛盾；问卷提交应补齐建档类成效证据 |
| CT-08 | api-db-check + opencode-mcp | 成本统计 | 服务单成本与全局看板成本来自 `ai_usage_logs`，金额保留两位，token 数与调用数可追溯 |

最低抽样要求：

- 每次测试至少抽 1 个新建入营服务单做 CT-01 到 CT-04。
- 如本次改动涉及文档、画像、打卡、成效、审核、消息通道，必须执行相关 CT 项。

---

## D. OpenCode Playwright MCP 页面交互测试（L2）

目标：验证运营和用户真实页面操作。执行者是 OpenCode，工具是它自带的 Playwright MCP。不得要求人手工点页面。

| ID | 执行方式 | 页面/流程 | 最低验收 |
|---|---|---|---|
| PW-01 | opencode-mcp | 登录与导航 | Basic Auth 后可进入后台；侧栏导航到用户、审核、运营规则、看板、诊断台 |
| PW-02 | opencode-mcp | 入营问卷 | 打开 `/q/{token}`，填写/一键提交成功，重复提交显示已提交状态 |
| PW-03 | opencode-mcp + api-db-check | 每日行动链接 | 字段与最新执行文档 Block 7 一致；必填校验和提交成功状态可见 |
| PW-04 | opencode-mcp | 用户管理 | 正常/已删除切换、用户详情、已删除服务单访问不崩溃 |
| PW-05 | opencode-mcp | 服务单详情 | 问卷链接、聊天记录、打卡、交付文件、成效引擎、成本显示正常；聊天默认滚到最新消息 |
| PW-06 | opencode-mcp | 审核控制台 | 新待审核 AI 回复/文档/知识草稿出现后自动刷新；可审核通过/驳回/确认发送 |
| PW-07 | opencode-mcp | 运营规则配置 | AI 回复策略、文件发送策略、问卷发送计划、入营提交回执、提醒配置可保存并回显 |
| PW-08 | opencode-mcp | 数据看板 | 指标卡可点击 drill-down；诊断入口不干扰看板主信息 |
| PW-09 | opencode-mcp | 系统诊断 | 01-08 布局清晰；运行时有进度、日志和失败信息 |

执行要求：

- 页面加载通过和交互通过必须分开记录。
- 若缺少特定数据，先构造可回滚测试数据；不能简单标记“未测但应可用”。
- 截图或 trace 保存到本次执行报告引用的位置。

---

## E. 真实 LLM E2E（E2E-01，L3） [已废弃]

由于真实大模型端到端测试耗时长、API 开销大且结果容易受生成波动干扰，目前该全链路测试已全面废弃，已从项目及诊断控制台中移除。

---

## F. Mock Time / 定时任务测试

目标：验证提醒、阶段报告、成效快照等时间触发逻辑。

建议归属：

- 不单独新建大文件，优先放入现有相关套件：
  - 成效快照/阶段状态：`test_08_outcome_engine.py`
  - 消息通道提醒或打卡提醒：`test_06_toggles.py` 或后续拆出的 `test_09_reminders.py`
- 只在时间触发场景明显增多时，再拆独立 reminder 测试文件。

最低验收：

- 使用 monkeypatch/freezegun 等方式控制当前时间。
- 覆盖工作日/周末、时区、同日不重复、超过阈值触发、未超过阈值不触发。
- 不能依赖真实等待。

---

## G. RAG 问题改写、召回与忠诚度评测

目标：用同一批版本化用例分开评价“问题有没有改对”、“知识有没有找对”和“回复有没有忠实使用事实”，避免只看最终回复后猜根因。

| ID | 执行方式 | 范围 | 最低验收 |
|---|---|---|---|
| RAG-01 | repo-spec | `rag_eval_cases.v1.json` 固定数据契约 | 固定 50 题不动态换题；25 条 FAQ 改写 + 25 条脱敏真实聊天；35 条检索 + 15 条跳过；用例 ID/来源引用有效且关键风险标签无缺失 |
| RAG-02 | 授权后自动评测 | 检索决策与改写传导 | 报告检索决策错误率；用原问题与实际改写分别召回，报告改写伤害率，不用改写文本相似度代替真实召回收益 |
| RAG-03 | 授权后自动评测 | 知识召回 | RAGAS Context Recall / Context Precision 均有分数和逐条证据；普通问答排除 `case` 素材 |
| RAG-04 | 授权后自动评测 | 回复忠诚度 | RAGAS Faithfulness / Answer Relevancy 均有分数；离线判官直接对照权威事实检查数字、比较、单位或否定关系，不维护错误说法黑名单；反转另计红色告警，不被均值抵消 |
| RAG-05 | repo-spec | 隔离运行与报告 | RAGAS 固化在专用镜像；Mock 运行零模型/零 DB 调用；容器结束后为 `Exited (0)`；报告 API 可列出和读取明细 |
| RAG-06 | repo-spec + opencode-mcp | 评测前端 | 可切换历史批次并看到 25+25 固定题库构成和每题来源，六项指标分组展示，关键事实告警醒目，可沿原问题→改写/召回→最终回复下钻失败证据 |

RAG-01 零费用入口：

```bash
cd backend
UV_CACHE_DIR=/tmp/uv-cache PYTHONPYCACHEPREFIX=/tmp/ran_ai_pycache \
  uv run python scripts/validate_rag_eval_cases.py
UV_CACHE_DIR=/tmp/uv-cache PYTHONPYCACHEPREFIX=/tmp/ran_ai_pycache \
  uv run pytest tests/test_rag_eval_cases.py tests/test_rag_eval_runtime.py \
  tests/test_rag_evaluation_reports.py -q

cd ..
docker compose \
  -f docker-compose.eval.yml \
  -f docker-compose.eval.mock.yml \
  up --no-deps rag-eval
```

上述入口只证明评测集、报告链路和页面数据源可用，**不代表当前模型的改写、召回或回复质量已通过**。RAG-02 至 RAG-04 的正式运行必须在 ECS 连接只读 RDS，并在真实模型与 Embedding 获得当次授权后执行。
