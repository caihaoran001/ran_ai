# 统一回复 Agent 真实评测执行记录

- 执行时间：2026-08-23 10:34—11:45（Asia/Shanghai）
- 评测代码：`c39a9cc0062281996c47b255dc338b1a4079a74d`
- 运行编号：`reply-agent-eval-real-c39a9cc-20260823T0233Z`
- 运行环境：ECS 隔离评测容器，DeepEval 4.1.5，`deepseek-v4-flash`
- 用户授权：已明确授权真实模型调用；费用上限 ¥10；固定数据集只启动一次；禁止自动重跑

## 执行前门禁

| 检查 | 结果 |
|---|---|
| ECS 部署版本等于 `c39a9cc` | 通过 |
| 没有其他统一评测容器正在运行 | 通过 |
| 本次运行目录不存在 | 通过 |
| 本次容器名不存在 | 通过 |
| 生产后端健康检查 | 200 |

## 实际命令

```bash
cd /root/ran_ai
docker compose -f docker-compose.reply-agent-eval.yml run -d \
  --name ran_ai-reply-agent-eval-real-c39a9cc-0233 \
  --no-deps \
  -e REPLY_AGENT_EVAL_GIT_SHA=c39a9cc0062281996c47b255dc338b1a4079a74d \
  reply-agent-eval \
  --run-id reply-agent-eval-real-c39a9cc-20260823T0233Z \
  --max-cost-yuan 10
```

执行期间持续读取 `status.json`，没有启动第二个容器，也没有自动重跑。

## 终态

| 项目 | 结果 |
|---|---:|
| 总题数 | 80 |
| 完成 | 80 |
| 通过 | 56 |
| 未通过 | 24 |
| 运行错误 | 0 |
| 模型及 Embedding 调用 | 873 |
| Token | 1,662,817 |
| 缓存命中 Token | 1,000,960 |
| 估算费用 | ¥4.897465 |
| 容器终态 | Exited (0) |

费用构成：判断官约 ¥3.7041，意图判断约 ¥0.7316，回复与 Agent 生成约 ¥0.4609，Embedding 约 ¥0.0009。所有调用均成功并带 usage。

## 运行后验收

- 后端容器内部 `/health`：200。
- 前端容器内部 `/login`：200。
- 报告 API 可列出本次运行，状态 `completed`，80 题、56 过、24 未过、费用 ¥4.897465。
- 报告：`/root/ran_ai/diagnostics/reply-agent-evaluations/reply-agent-eval-real-c39a9cc-20260823T0233Z/report.json`。
- 逐次 RAG 判断与 Embedding 证据：同目录 `calls.jsonl`。
- 未向真实群发送消息，未修改业务数据，未重启业务容器。

## 本次未执行

本次范围仅是用户授权的统一回复 Agent 真实质量评测，不是全项目回归。因此未执行完整后端 pytest、全量 Playwright、真实消息通道测试或业务数据写入测试；这些项目不能据本报告标记为通过。
