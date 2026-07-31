# 测试执行记录 2026-07-31 19:05

## 环境与授权

- 基线 commit：`2f22ab8 fix(agent-operations): refine time ranges`。
- 用户明确授权调用真实模型，最终指定模型为 `deepseek-v4-flash`。
- 测试范围：用合成问卷、实际 `AIService`、当前入营系统提示词和正式生成归一化链路，验证 V2.2 交付文档口径。
- 不写业务数据库，不生成持久服务单/画像/交付文件，不发 QiWe 或其他真实消息。
- 工作区原有未跟踪资料未修改。

## 测试设计

临时执行器：`/tmp/real_llm_intake_v22.py`，SHA-256：

```text
53421179b122f76129b88d9cb95740c80d49bf70632e1bd438c0d1535fc0f1ab
```

执行器显式覆盖 `AI_MODEL=deepseek-v4-flash` 和 `AI_DOCUMENT_MODEL=deepseek-v4-flash`，不修改项目 `.env`。计划覆盖：

1. `240斤` 当前体重、`170斤` 成年稳定舒适体重、腰高比偏高但非录入矛盾：验证体重归一、D 带、4kg/8斤上限、两份文档一致性和动态五项。
2. 怀孕 20 周的 A 带用户：验证人工复核、基础目标与挑战目标 D7 口径、A 带无减重数字。
3. `170cm + 15cm腰围`：验证 `ready_for_document_generation=false`、中文阻断原因以及生成前零模型调用。

## 实际命令与结果

### 1. 当前容器模型核查

```bash
docker exec ran-ai-backend python -c "from app.core.config import get_settings; s=get_settings(); print({'AI_MODEL': s.ai_model, 'AI_DOCUMENT_MODEL': s.ai_document_model or s.ai_model})"
```

状态：**环境阻塞**。

```text
permission denied while trying to connect to the docker API at unix:///var/run/docker.sock
```

### 2. Flash 真实模型生成

```bash
cd backend
timeout 900s env -u all_proxy -u http_proxy -u https_proxy \
  -u ALL_PROXY -u HTTP_PROXY -u HTTPS_PROXY \
  PYTHONPYCACHEPREFIX=/tmp/ran_ai_pycache \
  UV_CACHE_DIR=/tmp/ran-ai-uv-cache \
  uv run python -u /tmp/real_llm_intake_v22.py
```

状态：**环境阻塞，人工终止**。

- 脚本进入第一个样例并输出 `START high_weight_stable_history`。
- 第一个请求等待约 180 秒仍无响应；没有进入第二个样例。
- 为避免底层连接重试继续占用时间，发送 `Ctrl-C` 终止，进程退出码 1。
- `/tmp/real_llm_intake_v22_results.json` 未生成，因此没有任何完整模型输出可供内容评审。

### 3. 网络根因复现

```bash
python -c "import socket; print(socket.create_connection(('api.deepseek.com', 443), timeout=5))"
```

状态：**环境阻塞**，立即返回：

```text
socket.gaierror: [Errno -3] Temporary failure in name resolution
```

当前沙箱无法解析或连接 DeepSeek API；同时 Docker socket 和 SSH 外连也被沙箱禁止，无法改到本地容器或生产 ECS 内执行。

## 调用、费用与数据

- 完整成功的真实模型响应：`0`。
- 可审计 usage 行：`0`；临时执行器只在模型返回后记录 token/费用，因此没有 token 数据。
- 费用：**无法确认**。请求曾进入客户端等待阶段，但没有完成响应；不能证明上游完全未收到请求，也不能虚构为 0 元。
- 在用户指出模型应为 Flash 前，曾短暂启动过一次读取本地 Pro 配置的测试并立即终止，同样没有完整响应或结果文件。
- 未写业务数据库、未发消息、未保留业务测试数据。

## 用例状态

| 用例 | 执行方式 | 状态 | 证据 |
|---|---|---|---|
| 超过100kg + 稳定舒适体重完整生成 | real-e2e | 环境阻塞 | 首个 API 请求无响应，DNS 不可用 |
| 人工复核 D7 + A带无数字 | real-e2e | 未执行 | 首个样例未完成 |
| 腰高比明显矛盾生成前阻断 | api-db-check | 未执行 | 脚本未运行到第三个样例 |
| 两份文档一致性 | real-e2e | 未执行 | 无模型输出 |
| 每个用户动态五项 | real-e2e | 未执行 | 无两个完整样例可比较 |

## 可复现补跑

在能访问外网的普通宿主终端保留当前 `/tmp/real_llm_intake_v22.py` 后，执行上面的 Flash 命令即可。成功时应生成 `/tmp/real_llm_intake_v22_results.json`，并输出模型调用数、token、估算费用、逐样例问题和总通过状态。
