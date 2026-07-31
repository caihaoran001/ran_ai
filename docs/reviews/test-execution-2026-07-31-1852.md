# 测试执行记录 2026-07-31 18:52

## 环境与工作区

- 基线 commit：`69a5474 fix(checkins): 打卡界面只展示结构化内容`。
- 工作区包含本次“入营问卷与交付文档 V2.2 对齐”改动，以及用户原有未跟踪资料；测试期间未还原、覆盖或修改业务实现。
- `docker compose ps`：**环境阻塞**。当前执行沙箱无权访问 `/var/run/docker.sock`，返回 `permission denied while trying to connect to the docker API`。

## 授权、外部调用与数据

- 未调用真实 LLM，未产生模型费用。
- 未调用 QiWe 或其他真实消息通道，未发送消息。
- 未创建或永久保留测试服务单、聊天、打卡或知识数据。
- 本轮只执行测试命令和新增测试报告；未为通过测试而修改业务代码、测试代码、配置或预期结果。

## 实际命令与结果

### 1. 环境检查

```bash
git status --short
git log -1 --oneline
docker compose ps
```

- Git 状态和基线 commit：**通过**。
- 容器状态：**环境阻塞**，原因见上。

### 2. 后端语法检查（SPEC-01，repo-spec）

```bash
cd backend
PYTHONPYCACHEPREFIX=/tmp/ran_ai_pycache UV_CACHE_DIR=/tmp/ran-ai-uv-cache \
  uv run python -m compileall src scripts
```

结果：**通过**，退出码 0。

### 3. 后端完整 pytest（SPEC-02 / HC-01 至 HC-08，repo-spec）

```bash
cd backend
timeout 120s env PYTHONPYCACHEPREFIX=/tmp/ran_ai_pycache \
  UV_CACHE_DIR=/tmp/ran-ai-uv-cache uv run pytest tests -q
```

结果：**环境阻塞**，退出码 124。

- 命令在首个测试的全局数据库 fixture 停住，约 60 秒后仅输出 pytest-rerunfailures 的重试标记 `R`，120 秒到期后被 `timeout` 终止。
- 根因可复现为当前沙箱无法连接本机 PostgreSQL，同时无 Docker socket 权限，无法改为在 backend 容器内执行。
- 因 fixture 未完成，本次没有测试进入有效执行，也没有 pass/fail/skip 汇总；不能把它报告为测试失败，更不能报告为完整 pytest 通过。
- 没有生成 pytest warning 或 asyncpg 最终汇总；因此本轮无法判断完整套件是否存在后台任务异常被吞掉的风险。

为确认测试规模，另行执行：

```bash
cd backend
PYTHONPYCACHEPREFIX=/tmp/ran_ai_pycache UV_CACHE_DIR=/tmp/ran-ai-uv-cache \
  uv run pytest tests --collect-only -q
```

结果：**通过**，共收集 `537 tests`；这只证明用例可发现，不代表 537 个用例已执行。

### 4. 本需求核心确定性契约（api-db-check 的无数据库子集）

```bash
cd backend
PYTHONPYCACHEPREFIX=/tmp/ran_ai_pycache UV_CACHE_DIR=/tmp/ran-ai-uv-cache \
  uv run python -c "...直接加载 tests/test_01_intake.py 并执行 11 个零参数契约测试..."
```

结果：**通过，11/11**。覆盖：

- 成年后稳定舒适体重、斤/公斤归一与腰高比事实。
- 明显测量冲突阻断原因与非致命复测提示边界。
- 人工复核时基础目标和挑战目标的 D7 复评口径。
- 28 天目标按月度上限派生、不得越过上限、A 带不出现数字。
- 共享分析作为两份文档的一致事实源，以及 A 带用户可见目标兜底。

这些检查绕过了 pytest 的数据库 autouse fixture，仅验证确定性函数契约，不能替代完整 pytest 或真实 API/DB 链路。

### 5. 前端类型与 repo Playwright（SPEC-03 至 SPEC-05）

```bash
cd frontend
npx tsc --noEmit --incremental false
npm run test:e2e -- --list
timeout 90s npm run test:e2e
```

- TypeScript：**通过**，退出码 0。
- Playwright 用例发现：**通过**，发现 3 个文件中的 10 个测试。
- Playwright 实际执行：**环境阻塞**。10 个 worker 均在浏览器启动阶段失败，核心错误为 `sandbox_host_linux.cc:41 ... 不允许的操作 (1)`；页面尚未打开，因此不能记为页面加载失败或交互失败。

## 用例层级状态

| 范围 | 执行方式 | 状态 | 说明 |
|---|---|---|---|
| SPEC-01 | repo-spec | 通过 | 后端 compileall 通过 |
| SPEC-02 / HC-01~08 | repo-spec | 环境阻塞 | 537 个用例可收集；数据库 fixture 无法建立连接，0 个有效执行 |
| SPEC-03 | repo-spec | 通过 | TypeScript 通过 |
| SPEC-04 | repo-spec | 通过 | 10 个 Playwright 用例可发现 |
| SPEC-05 | repo-spec | 环境阻塞 | Chromium 被当前沙箱禁止启动 |
| CT-01 / CT-05 | api-db-check | 部分通过 | 11 个代码级确定性契约通过；真实 API/DB 用户结构样例环境阻塞 |
| CT-02~04 | api-db-check | 未执行 | 本次没有可回滚数据库链路；动态五项实现未改，但不能沿用历史结果当本次通过 |
| PW-01~09 | opencode-mcp | 环境阻塞 | 浏览器在页面打开前被沙箱拒绝启动 |
| E2E-01 | real-e2e | 未执行 | 已废弃，且用户未授权真实模型测试 |

## 可复现与后续补跑

在能够访问项目 PostgreSQL 和 Docker socket 的正常宿主终端执行：

```bash
cd /home/chr/桌面/ran_ai/backend
PYTHONPYCACHEPREFIX=/tmp/ran_ai_pycache UV_CACHE_DIR=/tmp/ran-ai-uv-cache uv run pytest tests -q
```

若浏览器沙箱正常，再在 `frontend/` 执行 `npm run test:e2e`。补跑后应新建报告，不应把本报告直接改写成通过。
