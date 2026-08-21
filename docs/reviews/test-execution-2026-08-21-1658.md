# 测试执行记录：Agent 评测 v2 尺子校正

执行时间：2026-08-21 16:55-17:00（Asia/Shanghai）  
测试范围：提交 `5c4fec5` 的 Agent 评测 v2、DeepEval 镜像、Mock 批次、Agent 工具回归、前端构建。  
安全边界：未调用真实 LLM/Embedding，未发送企微消息，未连接生产数据，未修改业务代码、测试代码、题库或配置。

## 1. 工作区与提交

命令：

```bash
git status --short
git log -1 --oneline
git rev-parse HEAD
```

结果：

- HEAD：`5c4fec5 fix(eval): calibrate Agent tool contract`
- 完整 SHA：`5c4fec52f905b113209c8f9420cc3ad1e5c571aa`
- 工作区存在用户/其他任务未提交文件；本次未触碰、未清理。

## 2. 后端 Agent 专项与工具回归

命令：

```bash
cd backend
PYTHONPYCACHEPREFIX=/tmp/ran_ai_pycache uv run python -m compileall src scripts
uv run pytest tests/test_agent_evaluation.py tests/test_10_agent_tools.py -q
```

结果：通过。`50 passed, 2 skipped in 5.98s`。跳过项是仅在专用评测镜像中安装 DeepEval 的测试，不是业务失败。

## 3. DeepEval 专用镜像

命令：

```bash
docker image inspect ran-ai-rag-eval:local --format '{{.Id}} {{.RepoTags}}'
docker run --rm --entrypoint /app/.venv/bin/python ran-ai-rag-eval:local -c 'import deepeval; print(deepeval.__version__)'
```

结果：通过。镜像存在，DeepEval 版本为 `4.1.5`。

另尝试在镜像内挂载仓库测试运行 `tests/test_agent_evaluation.py`。结果为环境阻塞：镜像内测试默认连接 `localhost:54322`，容器内没有本地 PostgreSQL，11 项在 fixture setup 阶段报 `ConnectionRefusedError`；未将该结果归因于代码。主机上的同一专项已通过。

## 4. v2 Mock 批次

命令：

```bash
docker compose -f docker-compose.agent-eval.yml run --rm --no-deps \
  --name agent-eval-mock-v2-20260821T1656Z agent-eval \
  --mock --run-id agent-eval-mock-v2-20260821T1656Z \
  --dataset /app/tests/fixtures/agent_eval_cases.v2.json
```

报告：`diagnostics/agent-evaluations/agent-eval-mock-v2-20260821T1656Z/report.json`

结果：通过，且与 v2 预期一致：

- 数据集：`agent-task-core-v2`，版本 `2`
- 60 完成
- 58 通过
- 2 失败：预置的未授权写操作题被门禁拦截
- 0 错误
- Agent/判断官调用：0 次
- 总费用：¥0
- DeepEval 框架版本：4.1.5
- 容器正常退出；未产生业务数据和外部消息

## 5. 前端

命令：

```bash
cd frontend
npx tsc --noEmit --incremental false
npm run build
```

结果：通过。TypeScript 检查通过，Next.js 15.5.18 生产构建成功，静态页面 32/32 生成成功。

## 6. 未执行

- 未运行真实 Agent/DeepEval 评测，不产生 API 费用。
- 未运行完整后端 `uv run pytest tests -q`，本轮聚焦 Agent 评测与相关工具回归。
- 未运行 Playwright 浏览器交互；本轮没有启动前端服务及浏览器环境。

