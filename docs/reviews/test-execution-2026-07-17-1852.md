# 真实模型回复精简回放执行记录

## 基本信息

- 执行时间：2026-07-17 18:30～18:52 +0800
- 工作目录：`/home/chr/桌面/ran_ai`
- 分支：`langgraph-agent-tools`
- 待验证提交：
  - `204ef19 fix(agent): keep health replies concise`
  - `d1ba3c9 fix(profile): recover legacy preference facts`
- 真实模型：`deepseek-v4-pro`
- 温度：`0.3`
- 用户授权：固定 12 次真实模型调用
- 消息通道：未调用，未向任何真实群发送消息
- 生产业务数据：未新增或修改；仅在生产容器中读取已配置模型并直连模型 API
- 费用：产生真实 API 费用；捕获到 11 条 usage，第 4 次因会话中断未取回 usage

## 执行前环境

`git status --short`：

```text
?? .impeccable/critique/2026-07-16T16-20-36Z__frontend-src-app-users-id-page-tsx.md
```

该文件是用户现有未跟踪文件，本次未修改。

`git log -3 --oneline`：

```text
d1ba3c9 fix(profile): recover legacy preference facts
204ef19 fix(agent): keep health replies concise
55d81f9 feat: add needs and habit portrait dimensions
```

`docker compose ps`：通过。本地 backend/frontend/postgres 均在运行，Postgres 为 healthy。首次在沙箱中读取 Docker API 被拒，获得只读权限后成功。

## 实际执行命令

```bash
sed -n '1,320p' docs/testing/README.md
sed -n '1,360p' docs/testing/TEST_CASES.md

env UV_CACHE_DIR=/tmp/uv-cache PYTHONPYCACHEPREFIX=/tmp/ran_ai_pycache \
  uv run pytest tests/test_02_chat.py tests/test_08_outcome_engine.py \
  tests/test_10_agent_tools.py tests/test_14_case_memory.py -q -o addopts=

env UV_CACHE_DIR=/tmp/uv-cache uv run --no-project --with paramiko \
  python /tmp/run_real_agent_replay.py --dry-run

env UV_CACHE_DIR=/tmp/uv-cache uv run --no-project --with paramiko \
  python -u /tmp/run_real_agent_replay.py

env UV_CACHE_DIR=/tmp/uv-cache uv run --no-project --with paramiko \
  python -u /tmp/run_real_agent_replay.py --start-seq 5 --end-seq 6
env UV_CACHE_DIR=/tmp/uv-cache uv run --no-project --with paramiko \
  python -u /tmp/run_real_agent_replay.py --start-seq 7 --end-seq 8
env UV_CACHE_DIR=/tmp/uv-cache uv run --no-project --with paramiko \
  python -u /tmp/run_real_agent_replay.py --start-seq 9 --end-seq 10
env UV_CACHE_DIR=/tmp/uv-cache uv run --no-project --with paramiko \
  python -u /tmp/run_real_agent_replay.py --start-seq 11 --end-seq 12

docker compose ps
```

## Mock 回归

- 状态：通过
- 方式：`repo-spec`
- 结果：`86 passed in 2.95s`
- 覆盖：聊天风控与上下文选择、成效/画像投影、回复 Agent 提示词契约、案例记忆与旧偏好恢复。

## 真实模型调用记录

原计划为 5 组回复新旧对照（10 次）+ 2 组上下文选择（2 次）。首个长会话在第 4 次请求期间到达约 60 秒工具会话上限：第 4 次已发起并计入 12 次授权额度，但未取回内容与 usage。后续改为每批 2 次，未补跑第 4 次，避免超额。

### 1. 烤肉后跑步

- 旧提示词：

```text
刚吃完烤肉先别急着跑，肠胃还在集中消化，这时候剧烈运动容易肚子疼。建议散步半小时左右，感觉不胀了再慢跑或快走回去会更舒服。
```

- 新提示词：

```text
刚吃完先别跑，走路回去更稳妥。这顿大概吃了多少？
```

- 状态：通过。新回复 2 句，去掉精确等待时长，改为保守建议 + 一个关键追问。

### 2. 吃甜后“不得劲”

- 旧提示词：失败。回复为 4 段，先归因年龄、胰岛素敏感度和锻炼，最后才追问具体感受。
- 新提示词：环境中断，第 4 次请求已计入额度，未取回结果，未执行补跑。
- 状态：未完整评估。

### 3. 自助餐社交闲聊

- 旧提示词：

```text
自助餐安排上了。吃得开心，记得先吃菜和肉，少喝甜饮料，吃完溜达一圈别直接回家坐着。明天早上别忘了称个体重打卡。
```

- 新提示词：

```text
周五晚上改善一下挺好。自助餐按“蔬菜、蛋白质、最后主食甜点”的顺序吃，胃会舒服不少。吃得开心。
```

- 状态：失败。新回复从 4 句降为 3 句，也不再主动要求称体重；但对方只是说去吃饭、问同事，并没要饮食建议，模型仍主动说教，且“改善一下挺好”仍带点评感。

### 4. 饥饿反馈 + 不喜欢豆腐

- 旧提示词：

```text
五点饿很正常，消化功能好的人三四小时就会有饥饿感。半碗杂粮米如果蛋白质和蔬菜不够，确实撑不了太久。下一餐可以增加瘦肉、鱼虾或蛋，再搭配足量蔬菜，饱腹感会更强。
```

- 新提示词：

```text
半碗杂粮米纯碳水偏多，蛋白质和蔬菜可能没跟上，这样饱腹感撑不了太久。下次试试搭配一巴掌大小的瘦肉或鱼虾，再加些绿叶菜，会扛饿很多。
```

- 状态：失败。新回复为 2 句且没有再推荐豆腐，证明偏好上下文已被使用；但用户只说了半碗杂粮米，模型直接假定整顿蛋白质和蔬菜不足，没有先问“这顿还吃了什么”。

### 5. 纠正“不累就先别加量”

- 旧提示词：

```text
这句说反了。应该是动作稳定、感觉还有余力再考虑加量。如果做了10-15个已经明显吃力、动作变形或者关节不舒服，就先别往上加。
```

- 新提示词：

```text
这句说反了。应该是动作稳定、恢复正常且仍有余力时再逐步加量；已经明显疲劳、动作变形或关节不适就别加。
```

- 状态：通过。新旧都能直接承认旧回复说反，新回复更紧凑。

### 6. 上下文选择——饥饿反馈

- 输入：`中午吃了半碗杂粮米，五点就饿了`
- 输出：`message_scene=body_feedback`，`memory_scopes=[preferences, current_state]`
- 状态：通过。

### 7. 上下文选择——鸡蛋摄入咨询

- 输入：`一天可以吃几个鸡蛋，早上已经两个水煮蛋了`
- 输出：`message_scene=diet_advice`，`memory_scopes=[preferences, habits, current_state]`
- 状态：通过。

## Token 记录

- 捕获到的 11 条结果：
  - prompt tokens：25,193
  - completion tokens：3,566
  - total tokens：28,759
- 第 4 次调用的 token 与精确费用未取回。

## 未执行 / 未覆盖

- 未执行全量后端 pytest；本次仅重跑与改动相关的 4 个测试文件。
- 未执行前端类型检查和 Playwright；改动不涉及前端。
- 未调用真实 Agent 工具循环；回放使用生产模型、完整系统/用户提示词，但不挂工具，以确保每个场景只产生一次模型调用。
- 未向生产群发送，未进行真实通道端到端测试。

## 数据残留

- 无新建服务单、消息、打卡、画像、FAQ 或向量数据。
- 直连模型调用未写入 `ai_usage_logs`；Token 由本执行记录手工留存。

