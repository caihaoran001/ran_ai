# RAG 评测体系实现验证执行记录

- 时间：2026-08-10 18:15 CST
- 验证代码：`5276269`（评测运行时）+ `1be760a`（前端评测台）
- 范围：本次新增的评测集契约、报告 API、隔离容器、RAGAS 适配和前端页面
- 边界：未运行全量后端测试；未连接生产 RDS；未调用 DeepSeek、Qwen、Embedding 或消息渠道；未执行正式 53 条质量评测

## 工作区与环境

- 验证前基线提交：`fd69898 docs: record canonical product rule update`
- 本次只提交评测相关文件；工作区原有 `.impeccable/` 与调研资料未纳入提交。
- 本地服务：后端 `:8000`、前端 `:3002`、PostgreSQL 均已运行；修改后已重启本地后端并确认 `/health` 返回 `ok`。

## 执行结果

| 项目 | 状态 | 命令 / 证据 |
|---|---|---|
| Python 语法与导入 | 通过 | `UV_CACHE_DIR=/tmp/uv-cache PYTHONPYCACHEPREFIX=/tmp/ran_ai_pycache uv run python -m compileall -q src scripts eval` |
| RAG 核心评测集 | 通过 | `uv run python scripts/validate_rag_eval_cases.py`；28 条核心用例，11 条检索、17 条跳过，必需风险标签完整 |
| 后端定向测试 | 通过 | `uv run pytest tests/test_rag_eval_cases.py tests/test_rag_eval_runtime.py tests/test_rag_evaluation_reports.py -q`；`8 passed in 1.15s` |
| RAGAS 镜像依赖 | 通过 | 镜像内 `openai=2.53.0`、`ragas=0.4.3`、`langchain-community=0.4.1`；四个 collections 指标类导入成功 |
| RAGAS 适配构造 | 通过 | 无网络实例化 `RagasScorer` 成功；四个 `ascore` 方法签名与传参一致 |
| Mock 评测容器 | 通过 | `docker compose -f docker-compose.eval.yml -f docker-compose.eval.mock.yml up --no-deps rag-eval`；退出码 0，随后状态为 `Exited (0)`，镜像与容器均保留 |
| Mock 报告 API | 通过 | 本地 `/api/rag-evaluations` 返回 28 条报告摘要：27 通过、1 失败、关键事实错误 1 次，六项指标及分母完整 |
| 前端类型检查 | 通过 | `npx tsc --noEmit --incremental false` |
| 前端生产构建 | 通过 | `npm run build`；`/rag-evaluations` 作为动态路由生成，First Load JS 110 kB |
| Playwright 用例发现 | 通过 | `npm run test:e2e -- --list`；共 11 条，包含 `/rag-evaluations loads` |
| RAG 页面浏览器 smoke | 通过 | `PLAYWRIGHT_BASE_URL=http://127.0.0.1:3002 npx playwright test e2e/admin-smoke.spec.ts --grep '/rag-evaluations loads'`；`1 passed` |
| 桌面与移动端视觉检查 | 通过 | 1440px 桌面和 390px 移动端均完成真实浏览器截图；指标、红色告警、失败证据展开可见，无横向溢出 |

## 过程中发现并修正

1. RAGAS 0.4.3 与自动解析出的 `langchain-community 0.4.2` 组合在导入时缺少旧 VertexAI 模块。评测镜像固定为 `langchain-community>=0.4.1,<0.4.2` 后，RAGAS 与四项指标均可导入。
2. 若改写导致零召回，不能因为缺少上下文而跳过评分。现在该条的 Context Recall、Context Precision 和 Faithfulness 明确记为 0，并参与改写伤害率分母。
3. 班主任在当前会话明确纠正的 300ml 属于会话权威事实，应跳过知识库；评测契约新增事实来源层级，避免用旧知识覆盖最新纠正。

## 数据与费用

- 业务数据库写入：0。
- 真实模型与 Embedding 调用：0。
- 真实群消息：0。
- 本地保留物：`diagnostics/rag-evaluations/` 下的 Mock 演示报告，以及状态为 `Exited (0)` 的本地评测容器；均非业务数据。
