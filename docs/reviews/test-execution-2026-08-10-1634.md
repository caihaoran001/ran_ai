# 测试执行记录 2026-08-10 16:34

## 范围与环境

- 当前提交：`6dd9744 refactor: simplify retrieval evidence`
- 工作区仅有用户原有未跟踪文件/目录，本次未纳入。
- 本轮范围：删除 AI 运行详情中的 FAQ 修订号和内容指纹；保留当时实际召回内容；按用户确认的口径统一生产冲突 FAQ。
- 没有调用回复 LLM，没有发送真实消息。更新 6 条生产 FAQ 时按标准同步流程执行了 6 次 Embedding。

## 代码验证

| 状态 | 命令/检查 | 结果 |
|---|---|---|
| 通过 | 3 个召回观测定向 pytest | `3 passed`；命中名次、知识身份和当时内容仍正常保留 |
| 通过 | `cd backend && uv run pytest tests -q` | `686 passed in 21.14s` |
| 通过 | `cd backend && python -m compileall -q src scripts` | 无语法/导入错误 |
| 通过 | `cd frontend && npx tsc --noEmit --incremental false` | 类型检查通过 |
| 通过 | `cd frontend && npm run build` | Next.js 生产构建成功，`/agent-operations` 正常生成 |
| 通过 | `cd frontend && npm run test:e2e -- --list` | 发现 10 条用例 |
| 部分通过 | 沙箱外 `npm run test:e2e` | 9 通过、1 失败；仍是已知 `/users` 标题契约失配，与本轮改动无关 |
| 通过 | 重启本地 backend 并请求 `/health` | 返回 `{"status":"ok"}` |

## 生产更新与验收

- 更新前对 FAQ 编号、内部修订号和完整旧正文做了预检，6 条全部匹配后才开始写入。
- 已更新 FAQ：117、120、172、179、195、262。
- 统一口径：1 瓶盖≈1.5 小刻度；1 大刻度=3 小刻度≈2 瓶盖；晚 A 每次 1 大刻度配约 300ml 温水。
- 6 个对应知识块均为 active，向量均存在，知识块内部修订与 FAQ 一致。
- 更新后只读枚举全部 11 条相关 active FAQ，未再发现“1 瓶盖=1 小刻度”或“晚 A 一大刻度配 150/200ml”的反向口径。

## 未执行

- 未部署代码到生产；生产当前已生效的只是 FAQ 内容和同步向量。
- 未用真实回复 LLM 重放原案例，因此未把“知识已统一”等同于“模型回复已验收”。
