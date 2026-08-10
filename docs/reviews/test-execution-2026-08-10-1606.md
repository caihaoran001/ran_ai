# 测试执行记录 2026-08-10 16:06

## 环境与边界

- 当前提交：`f4732b6 test: establish versioned RAG evaluation cases`
- 本次相关提交：`2003467`、`75ddd2f`、`f4732b6`，均带 `Agent-Tool: codex-cli`。
- 工作区仅有用户原有的未跟踪文件/目录，本次未纳入、未修改。
- 本地服务：backend、frontend、postgres 均在运行，postgres 健康。
- 本次没有发送真实企微消息，没有修改生产数据，没有调用真实回复 LLM。
- 生产只读召回复验共执行 11 个问法；由于生产启用向量检索，这 11 次查询可能调用了真实 Embedding API。未写入向量或业务数据。

## 执行记录

| 状态 | 命令/检查 | 关键结果 |
|---|---|---|
| 通过 | `cd backend && PYTHONPYCACHEPREFIX=/tmp/ran_ai_pycache python -m compileall -q src tests` | 无语法错误 |
| 通过 | `cd backend && PYTHONPYCACHEPREFIX=/tmp/ran_ai_pycache python -m compileall -q src scripts` | 无语法错误 |
| 通过 | 5 个召回观测定向 pytest | `5 passed`，覆盖实际命中身份、版本、内容快照和旧运行兼容 |
| 通过 | `cd backend && uv run python scripts/validate_rag_eval_cases.py` | `rag-chat-core-v1` 共 19 条：13 条应检索、6 条应跳过；核心风险标签齐全 |
| 通过 | `cd backend && uv run pytest tests/test_rag_eval_cases.py -q` | `3 passed` |
| 通过 | `cd backend && uv run pytest tests -q` | `686 passed in 21.32s`，无跳过、无 warning/后台 asyncpg 异常 |
| 通过 | `cd frontend && npx tsc --noEmit --incremental false` | 类型检查通过 |
| 通过 | `cd frontend && npm run test:e2e -- --list` | 发现 3 个 spec 内 10 条测试 |
| 通过 | `cd frontend && npm run build` | Next.js 15.5.18 生产构建成功，`/agent-operations` 路由成功生成 |
| 环境阻塞 | 沙箱内 `npm run test:e2e` | Chromium 因 Linux `sandbox_host` 权限无法启动，全部 10 条未进入页面断言；不计为产品失败 |
| 部分通过 | 沙箱外 `npm run test:e2e` | 9 通过、1 失败；`/users` 页面实际已加载，但测试期待标题“用户管理”，当前页面标题为“正常用户” |
| 失败（可复现） | `npx playwright test e2e/admin-smoke.spec.ts --grep "/users loads" --workers=1` | 单线程仍失败，证实是旧 smoke 标题契约失配，非并发抖动；页面内容和用户列表已正常呈现 |
| 通过 | `docker restart ran-ai-backend` + `curl -f http://127.0.0.1:8000/health` | 本地后端已加载新代码，返回 `{"status":"ok"}` |

## 生产知识只读核对

- 当前生产有 208 条 active FAQ，active 知识块缺向量数为 0。
- 瓶盖换算存在直接冲突：有效知识同时写着“1 瓶盖≈1.5 小刻度”和“1 瓶盖=1 小刻度”。
- 晚 A 水量存在多个未注明条件的口径：一大刻度 300–350ml、150ml、两瓶盖约 200ml、一大刻度约 300ml。
- 工作人员历史说法显示水量本来会随用量档位变化：1 瓶盖通常配 150–200ml，1 大刻度通常配约 300ml；但部分 FAQ 未把适用档位说清。
- 本案例最新工作人员纠正为：晚 A 每次 1 大刻度，睡前 1–2 小时，约 300ml 温水。
- 对“一瓶盖是多少刻度”的当前生产召回前三条为晚 A 大刻度、瓶盖换算、各档水量，其中前两条的 1.5/3 换算一致。

## 未执行

- 未运行真实问题改写/回复 LLM 质量评测；当前只校验了评测集结构和覆盖面。
- 未为新的 AI 运行详情构造真实生产回复记录；界面通过类型检查和生产构建，具体新命中卡片待部署后的新回复轮次验收。
- 未部署生产，未修改生产 FAQ。
