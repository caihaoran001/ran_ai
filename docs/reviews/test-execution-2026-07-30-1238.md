# 真实模型打卡工具命中测试执行记录

执行时间：2026-07-30 12:36–12:38 CST

## 环境与授权

- 当前提交：`b4dffce feat(checkin): record chat checkins in reply agent`
- 模型：`deepseek-v4-pro`
- 用户已明确授权调用真实模型测试 `record_checkin` 命中效果。
- 未经过消息发送编排，未向 QiWe 或其他真实群发送消息。
- 使用本地 PostgreSQL 的单一外层事务构造临时用户、服务单、五项清单和入站消息；工具内 `commit` 在探针中替换为 `flush`，测试结束后统一回滚。
- 未修改业务代码、测试代码、配置文件或测试预期。
- 执行前已有未提交内容：根目录 `AGENTS.md` 的实验群说明，以及 `.impeccable/`、客户调研、文字记录、第三次参考文件等未跟踪文件；本次未改动这些内容。

## 实际命令

### 1. 探针语法检查

```bash
cd backend
env UV_CACHE_DIR=/tmp/uv-cache \
  PYTHONPYCACHEPREFIX=/tmp/ran_ai_pycache \
  uv run python -m py_compile /tmp/ran_ai_real_checkin_tool_probe.py
```

状态：通过。

### 2. 首次真实模型执行

```bash
cd backend
env UV_CACHE_DIR=/tmp/uv-cache \
  PYTHONPYCACHEPREFIX=/tmp/ran_ai_pycache \
  uv run python /tmp/ran_ai_real_checkin_tool_probe.py
```

状态：环境阻塞，未发出模型请求。

关键错误：

```text
ValidationError: Unknown scheme for proxy URL URL('socks://127.0.0.1:7890/')
```

说明：宿主机代理变量使用 `socks://`，当前 OpenAI 客户端初始化时不接受该格式。失败发生在 `ChatOpenAI` 初始化阶段，临时事务已回滚，没有产生模型费用。

### 3. 去除当前进程代理变量后重试

```bash
cd backend
env -u ALL_PROXY -u all_proxy \
  -u HTTP_PROXY -u http_proxy \
  -u HTTPS_PROXY -u https_proxy \
  -u NO_PROXY -u no_proxy \
  UV_CACHE_DIR=/tmp/uv-cache \
  PYTHONPYCACHEPREFIX=/tmp/ran_ai_pycache \
  uv run python /tmp/ran_ai_real_checkin_tool_probe.py
```

状态：通过。

## 场景结果

真实回复 Agent 保留线上同构的全部 23 个工具，`record_checkin` 已成功绑定。

| 场景 | 预期 | 实际工具行为 | 写入结果 | 最终回复 | 状态 |
|---|---|---|---|---|---|
| `午B喝了` | 调工具，记录当天午B | 调用 `record_checkin` 1 次 | `target_date=2026-07-30`，写入 `product_schedule.noon_b` | `记上了，今天午B已打卡。` | 通过 |
| 凌晨 1:50 补 7 月 29 日 | 调工具，明确写入前一天 | 调用 `record_checkin` 1 次 | `target_date=2026-07-29`；写入早C，并匹配清单第 1/3/5 项 | `7月29日补记好了：早C已喝、全天蔬菜2拳、饭后困评分5。早点休息。` | 通过 |
| 图片解析明确正在喝咖啡 | 调工具，按图片证据记录早C | 调用 `record_checkin` 1 次 | `target_date=2026-07-30`；写入 `product_schedule.morning_c`，证据类型为 `image` | `早C已记录，今天的咖啡收到了。` | 通过 |
| `谢谢，知道了` | 不调用打卡工具 | 未调用任何工具 | 无打卡、无回执 | `<no_reply>` | 通过 |

汇总：

- 正样本工具命中：`3/3`
- 正样本工具执行成功：`3/3`
- 非打卡误调用：`0/1`
- 跨日目标日期正确：是
- 工具成功后第二次模型调用生成回复：是
- 临时数据回滚验证：通过
- 真实消息发送：0

## 模型调用与费用

- 模型调用：7 次（三个正样本各 2 次，非打卡对照 1 次）
- 输入 token：53,732
- 输出 token：1,373
- 总 token：55,105
- 按当前本地配置单价 `12.55 / 25.11 元每百万输入/输出 token` 粗略估算：约 `0.71 元`
- 该估算未单独扣除供应商缓存优惠，以实际账单为准。

## 未覆盖

- 未发送真实 QiWe 群消息，因此未验证渠道发送、员工抢答和 8 秒聚合后的真实回调链路。
- 未下载并调用真实图片视觉解析；图片场景从现有图片解析结果文本进入回复 Agent，验证的是回复 Agent 的工具选择与写入。
- 语音输入按当前需求明确不在范围内。
- 仓库已废弃全链路真实 LLM E2E；本次只执行用户要求的受控工具命中测试。

