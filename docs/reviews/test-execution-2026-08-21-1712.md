# 测试执行记录：Agent 评测 v3

执行时间：2026-08-21 17:08-17:22（Asia/Shanghai）  
安全边界：仅本地代码、专用评测镜像和 Mock；未调用真实 LLM/Embedding，未发送企微消息，未访问生产数据，未修改业务代码、测试代码、题库或配置。

## 工作区与版本

```bash
git status --short
git log -2 --oneline
git rev-parse HEAD
```

结果：最终 HEAD 为 `c6dd776b859993d7c4993754661228762e0c0af0`（`fix(eval): default Agent runs to v3 dataset`）。工作区已有其他未提交文件，本次未触碰。

## v3 题库与后端专项

```bash
cd backend
PYTHONPYCACHEPREFIX=/tmp/ran_ai_pycache uv run python -m compileall src scripts
uv run pytest tests/test_agent_evaluation.py tests/test_10_agent_tools.py \
  tests/test_agent_checkpoint.py tests/test_agent_reminders.py -q
```

结果：通过，`86 passed, 2 skipped in 6.90s`。DeepEval 相关的 2 项按项目设计仅在专用镜像安装，故跳过；不是业务失败。v3 题库加载断言通过，套件 ID 为 `agent-task-core-v3`、版本 3。

## DeepEval 镜像与 v3 Mock

```bash
docker run --rm --entrypoint /app/.venv/bin/python ran-ai-rag-eval:local \
  -c 'import deepeval; print(deepeval.__version__)'
CURRENT_SHA=$(git rev-parse HEAD)
AGENT_EVAL_GIT_SHA="$CURRENT_SHA" docker compose -f docker-compose.agent-eval.yml run --rm --no-deps \
  --name agent-eval-mock-v3-20260821T1720Z agent-eval --mock \
  --run-id agent-eval-mock-v3-20260821T1720Z \
  --dataset /app/tests/fixtures/agent_eval_cases.v3.json
```

结果：镜像 DeepEval `4.1.5`。最终 v3 Mock 批次通过预期：

- run ID：`agent-eval-mock-v3-20260821T1720Z`
- 报告 SHA：`c6dd776b859993d7c4993754661228762e0c0af0`
- 数据集：`agent-task-core-v3` v3，重复 2 轮
- 60 完成、58 通过、2 个预置未授权写操作失败、0 错误
- Agent 调用 0 次，判断官调用 0 次，总费用 ¥0

报告：`diagnostics/agent-evaluations/agent-eval-mock-v3-20260821T1720Z/report.json`

说明：代码切换期间曾有一次旧 HEAD 的 v3 预检（`1702Z`），因 Mock 文本参数合成缺失得到 54/60；该结果标记为无效证据。随后在 `78e3455` 上做过修复确认；最终以 `c6dd776` 显式把 compose 默认数据集切到 v3，并注入完整 SHA 执行 `1720Z`，该批次作为本次唯一可采信的 v3 Mock。

## 前端

```bash
cd frontend
npx tsc --noEmit --incremental false
npm run build
```

结果：通过。TypeScript 检查通过，Next.js 15.5.18 生产构建成功，32/32 静态页面生成成功。

## 未执行

- 未运行真实模型评测或真实 Embedding。
- 未运行完整后端测试全集。
- 未运行 Playwright 浏览器交互。
