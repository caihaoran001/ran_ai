# 测试执行记录 2026-07-31 20:10

## 范围与结论更正

- 目标：保留 `deepseek-v4-flash` thinking，定位并修复入营画像+两份交付文件生成失败。
- 本记录更正 `test-execution-2026-07-31-1931.md` 中“Flash 与大 package 不适配”的原因判断；旧记录保留当时现象，不直接改成通过。
- 本轮使用合成问卷，不写业务数据库，不创建服务单/画像/文档，不发送 QiWe 消息。

## 根因证据

原样请求的输入约 `15900` token，远低于 DeepSeek V4 上下文上限，两份文档正常最终 JSON 仅约 `4000-4300` token。

### 默认 thinking + 16000

- `finish_reason=stop`
- `completion_tokens=4345`，全部计为 `reasoning_tokens`
- `reasoning_content` 包含 8475 字完整 JSON
- `content` 为空

生产客户端正确读取了正式正文字段 `choices[0].message.content`；失败是因为 V4 没有进入最终正文阶段，不是字段读错。

### thinking + JSON Output + 16000

- `finish_reason=length`
- `completion_tokens=15999`
- `reasoning_tokens=14786`
- 正式 `content` 只来得及输出部分 JSON

证明仅增加 JSON Output 不足以解决，thinking 和正文共享同一输出预算。

### thinking + JSON Output + 32768

- `finish_reason=stop`
- `completion_tokens=6265`
- `reasoning_tokens=1998`
- `content` 9669 字，顶层 `shared_analysis/profile/documents` 和两份文档键均完整

### 最终方案：thinking + low effort + JSON Output + 50000

- `finish_reason=stop`
- `completion_tokens=7829`
- `reasoning_tokens=3735`
- `content` 9515 字，完整可解析 JSON
- thinking 仍明确开启，50000 只是文档场景上限，费用按实际用量计算

## 实现验证

- 新增独立 `AI_DOCUMENT_MAX_TOKENS=50000`，不放大聊天的 `AI_MAX_TOKENS`。
- 文档 JSON 场景发送 `thinking.type=enabled`、`reasoning_effort=low`、`response_format=json_object`。
- 系统提示词明确要求思考后必须把完整 JSON 写入 `content`。
- 禁止把 `reasoning_content` 当正式交付文件兜底；如果只有思考没有正文，明确失败并记录原因。

## 真实 Flash 用例

### 大体重 + 成年稳定舒适体重

- 状态：**通过**
- `240斤 -> 120kg`、成年稳定舒适体重 `170斤 -> 85kg`归一正确
- D 带、基础目标 `28天减重6-7斤`、挑战目标 `向8斤推进` 一致
- 画像、两份文档、五项打卡和生成前一致性闸门全部通过

### 孕期人工复核 + A 带

- 状态：**通过**
- `manual_review_required=true`
- 基础目标 `先完成7天基础执行，D7复评`
- 挑战目标 `暂不设置挑战数字，D7复评`
- A 带用户可见目标中无减重数字

注：临时 harness 为了隔离长请求，两个样例分开执行；其汇总器因单次只有1个用户，无法执行“两用户打卡题面是否不同”的跨用户比较，因此进程汇总退出码为 1；两个业务样例自身均为 `pass=true, findings=[]`，不是产品失败。

### 腰高比明显矛盾

- 状态：**通过**
- `ready_for_document_generation=false`
- 包含中文阻断原因
- 新增模型调用数 `0`

## 自动化测试

```bash
cd backend
uv run pytest tests/test_ai_service_document_generation_options.py tests/test_01_intake.py -q
```

结果：`73 passed in 2.60s`。

```bash
cd backend
uv run pytest tests/ -q
```

结果：`541 passed in 15.84s`。

## 本地运行检查

- 后端代码修改后已执行 `docker restart ran-ai-backend`。
- 容器 `/health` 返回 `{"status":"ok"}`。
- 容器加载 `AI_DOCUMENT_MAX_TOKENS=50000`。
- 本轮未部署生产。

## 费用与数据

- 产生了真实 Flash API 调用费用；诊断请求跨多个临时进程，未形成一个可靠的总金额，不虚构精确合计。
- 两个最终业务样例各使用 2 次模型调用（安全抽取 + 文档 package）。
- 无业务数据库写入，无 QiWe 发送。
