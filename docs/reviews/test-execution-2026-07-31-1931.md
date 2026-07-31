# 测试执行记录 2026-07-31 19:31

## 环境与授权

- 基线 commit：`d846e65 docs(test): record flash model environment block`。
- 用户已明确授权真实模型测试；本轮实际目标模型为生产运行中的 `deepseek-v4-flash`。
- 只使用合成问卷，不写业务数据库，不创建服务单/画像/交付文件，不发送 QiWe 消息。
- 未修改业务代码、测试代码、项目配置或测试预期；测试夹具只存在于 `/tmp/real_llm_intake_v22.py`。

## 预检与生产配置

### DeepSeek 网络

```bash
python -c "import socket; s=socket.create_connection(('api.deepseek.com',443),5); print('deepseek_tcp_ok', s.getpeername()); s.close()"
```

结果：**通过**，TCP 443 可达。

### Docker 与本地容器

```bash
docker ps --format '{{.Names}} {{.Status}}'
docker exec ran-ai-backend uv run python -c \
  "from app.core.config import get_settings; s=get_settings(); print({'local_AI_MODEL': s.ai_model, 'local_AI_DOCUMENT_MODEL': s.ai_document_model or s.ai_model})"
```

结果：**通过**。本地 backend 运行中；本地模型仍为 `deepseek-v4-pro`。

### 生产运行配置

通过 Paramiko SSH 到 ECS 后，在运行容器内执行：

```bash
docker exec ran_ai-backend-1 uv run python -c \
  "from app.core.config import get_settings; s=get_settings(); print({'AI_MODEL': s.ai_model, 'AI_DOCUMENT_MODEL': s.ai_document_model or s.ai_model, 'AI_RISK_MODEL': s.ai_risk_model or s.ai_model, 'AI_MAX_TOKENS': s.ai_max_tokens, 'AI_TIMEOUT_SECONDS': s.ai_timeout_seconds, 'PROMPT_PRICE': s.ai_prompt_cost_per_1m_tokens, 'COMPLETION_PRICE': s.ai_completion_cost_per_1m_tokens})"
```

结果：**通过**。

```text
AI_MODEL=deepseek-v4-flash
AI_DOCUMENT_MODEL=deepseek-v4-flash
AI_RISK_MODEL=deepseek-v4-flash
AI_MAX_TOKENS=16000
AI_TIMEOUT_SECONDS=300
PROMPT_PRICE=1.0 元/百万 token
COMPLETION_PRICE=2.0 元/百万 token
```

测试进程在导入任何 app 模块前显式设置 `AI_MODEL` 与 `AI_DOCUMENT_MODEL` 为 Flash；独立预检打印 `deepseek-v4-flash deepseek-v4-flash`，未误用本地 Pro。

## 真实模型执行

实际命令：

```bash
cd backend
timeout 900s env -u all_proxy -u http_proxy -u https_proxy \
  -u ALL_PROXY -u HTTP_PROXY -u HTTPS_PROXY \
  PYTHONPYCACHEPREFIX=/tmp/ran_ai_pycache \
  UV_CACHE_DIR=/tmp/ran-ai-uv-cache \
  uv run python -u /tmp/real_llm_intake_v22.py
```

### 1. 大体重 + 成年稳定舒适体重

合成输入包含 `170cm`、`240斤` 当前体重、`170斤` 成年稳定舒适体重，以及“先稳定饮食、活动和睡眠节律，再逐步减重”的期待。

执行两次，均使用正式 `_generate_intake_analysis_package`：

1. 第一次：**失败**，模型返回内容解析后没有形成系统可识别的两份文档 payload，正式链路抛出 `RuntimeError: intake analysis package generated empty document content`。
2. 为保留原始安全抽取并继续其他样例，增强临时测试器后重跑：**失败**，模型输出达到 `AI_MAX_TOKENS=16000`，`finish_reason=length`，正式链路抛出 `RuntimeError: AI output truncated at max_tokens=16000`。

安全因子原始响应通过：`safety_review_required=false`，没有凭空制造疾病或人工复核。

### 2. 孕期人工复核 + A带

合成输入包含 `175cm / 63kg`、成年稳定舒适体重 `62kg`、怀孕 20 周，以及“改善睡眠与白天精力”的非减重期待。

- 安全因子抽取：**通过**。Flash 输出 `safety_review_required=true`，原因准确引用“目前怀孕20周，医生建议以稳定状态和正常饮食为主”。
- 完整 package：**失败**。同样达到 `16000` token 上限并以 `finish_reason=length` 截断，未形成可用画像和两份正式文档。
- 因文档未形成，人工复核 D7 文案和 A 带全文无减重数字无法做最终用户可见内容验证。

### 3. 腰高比明显矛盾生成前阻断

第一次夹具误把体脂率字段键当成腰围字段，导致预期阻断样例缺少腰围并意外进入模型调用；发现后立即中断，未把它算成产品结果。

修正临时夹具后单独执行：

```bash
cd backend
env -u all_proxy -u http_proxy -u https_proxy \
  -u ALL_PROXY -u HTTP_PROXY -u HTTPS_PROXY \
  PYTHONPYCACHEPREFIX=/tmp/ran_ai_pycache \
  UV_CACHE_DIR=/tmp/ran-ai-uv-cache \
  uv run python -u /tmp/real_llm_intake_v22.py --blocked-only
```

结果：**通过**。

```text
ready_for_document_generation=false
reasons=['腰围与身高存在明显单位或录入矛盾，请复测并确认后再生成']
model_calls_added=0
```

这证明明显矛盾在真实模型调用前被确定性阻断，不产生正式文档和额外费用。

## 调用、费用与数据

- 完整返回的真实 Flash 响应：7 个，包括安全因子响应和最终被应用判为截断/空文档的响应；另有 1 个因夹具错误启动后人工中断的请求。
- 计划内完整文档生成：`0/3` 可用；其中 `2/3` 达到 16000 token 截断，`1/3` 未形成可识别文档 payload。
- 计划内安全因子：两个目标场景均判断正确。
- 产生真实模型费用：**是**。临时进程在异常退出/人工中断前未持久化 usage 汇总，因此精确 token 与金额无法恢复；不能虚构为 0 元。生产 Flash 计价配置为输入 1 元、输出 2 元/百万 token。
- 未写业务数据库、未发消息、未保留服务单或交付文件。原始合成响应仅保存在 `/tmp/real_llm_intake_v22_raw_calls.json`。

## 用例状态

| 用例 | 执行方式 | 状态 | 结论 |
|---|---|---|---|
| 生产模型与运行参数核查 | real-e2e | 通过 | 三个模型路由均为 Flash，输出上限 16000 |
| 超过100kg与稳定舒适体重完整生成 | real-e2e | 失败 | 一次空文档、一次输出截断 |
| 孕期安全识别 | real-e2e | 通过 | 正确要求人工复核 |
| 人工复核 D7 + A带无数字 | real-e2e | 未执行 | 完整文档在进入最终校验前截断 |
| 腰高比明显矛盾阻断 | api-db-check | 通过 | ready=false、中文原因、0 模型调用 |
| 两份文档一致性 | real-e2e | 未执行 | 无可用文档 payload |
| 每个用户动态五项 | real-e2e | 未执行 | 无两份完整执行文档可比较 |

## 原始证据

- `/tmp/real_llm_intake_v22.py`：本轮临时测试器。
- `/tmp/real_llm_intake_v22_raw_calls.json`：成功返回的安全因子原始响应。
- `/tmp/real_llm_intake_v22_results.json`：修正后的零调用阻断结果。
