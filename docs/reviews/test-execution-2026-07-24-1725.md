# 测试执行记录：生产后端停机期间 QiWe 回调行为

- 执行时间：2026-07-24 17:03–17:25（Asia/Shanghai）
- 当前提交：`685aceb fix(qiwe): safely recover confirmed unsent replies`
- 测试类型：针对性生产故障注入 + 本地可重复回归
- 实验目标：蔡浩然实验服务单 `RAI-20260622-502EB2`
- 实验群绑定：测试前从生产 RDS 重新解析，未使用缓存的 `group_id`
- 工作区原有未跟踪文件：`.impeccable/critique/2026-07-16T16-20-36Z__frontend-src-app-users-id-page-tsx.md`，未改动

## 生产故障注入

### 前置保护

- 从生产 RDS 确认实验服务单和当前群绑定。
- 记录该服务单 `ai_auto_reply_enabled=true`。
- 临时将实验服务单单群自动回复关闭，避免测试消息触发真实 LLM 或真实出站回复。
- 测试结束后已恢复为 `true`。
- Caddy 容器始终运行，只停止 FastAPI 后端容器。
- 停机命令带 140 秒兜底 watchdog；实际停机窗口为 120 秒。

### 时间线与证据

| 时间（北京时间） | 事件 | 证据 |
|---|---|---|
| 17:03:41 | 后端容器停止 | 探针输出 `BACKEND_STOPPED_SEND_NOW` |
| 17:04:18 | 用户在实验群发送 `回调测试测试-20260724-170112` | QiWe 历史 `seq=8817520`、`msgUniqueIdentifier=1215459040906849777` |
| 17:04:21 | QiWe 请求固定回调地址 | Caddy access log：HTTP `502`，耗时约 10ms |
| 17:06:03 | 后端恢复健康 | 容器内 `/health` 返回正常 |
| 17:06–17:24 | 多次检查延迟重试 | Caddy 始终只有最初一条 `502`，没有后续 2xx 回调 |
| 17:24 | 最终数据库检查 | `inbound_events=[]`、`messages=[]`、`outbound=[]` |
| 17:25 | 公网健康检查 | `https://admin.humanburning.ai/health` 返回 `{"status":"ok"}` |

QiWe 历史接口返回完整窗口，并能精确找到测试消息；这证明消息存在于 QiWe 消息档案。与此同时，恢复后超过 20 分钟未观察到回调重试，生产 durable inbox 和业务消息表均没有该消息。

### 判定

- **通过**：后端停机期间，固定回调域名仍被请求，Caddy 如实返回 `502`。
- **失败（发现生产可靠性缺口）**：当前 QiWe 网关在观察窗口内没有重试失败回调。
- **通过**：测试消息没有触发真实 LLM、机器人回复或出站消息。
- **通过**：后端自动恢复，实验服务单开关恢复原值，公网健康检查正常。

## 本地可重复检查

| 项目 | 命令 | 状态 | 结果 |
|---|---|---|---|
| 后端编译 | `PYTHONPYCACHEPREFIX=/tmp/ran_ai_pycache uv run python -m compileall src scripts` | 通过 | 无语法/导入编译错误 |
| 后端完整测试 | `uv run pytest tests -q` | 通过 | `437 passed in 13.27s` |
| 前端类型检查 | `npx tsc --noEmit --incremental false` | 通过 | 无类型错误 |
| Playwright 用例发现 | `npm run test:e2e -- --list` | 通过 | 发现 1 个文件、6 个用例 |
| 完整浏览器交互 | `npm run test:e2e` | 未执行 | 本次为生产回调故障注入，不涉及页面交互 |

第一次完整 pytest 被误并发启动两次，两个进程争用同一个本地测试数据库后被终止；随后在允许访问本机 Docker PostgreSQL 的环境中单独重跑，最终 `437 passed`。未吞掉失败或把中断运行计为通过。

## 费用与数据

- 未调用真实 LLM、Embedding 或内容生成接口，未产生模型费用。
- 未调用机器人发送接口，没有向群内发送机器人测试消息。
- 调用了 QiWe 只读历史接口。
- 用户手工发送的测试消息永久存在于 QiWe 历史中；由于回调失败且未重试，它未进入燃人 AI 数据库。
- 未修改业务代码、测试代码、测试预期或生产配置文件。
