# WSL 本地环境验收执行记录

时间：2026-09-09；基线 commit：9d0856c。开始时工作区干净；本次仅创建被忽略的本地配置/构建文件、运维脚本及本报告，不修改业务代码或测试代码。

## 范围与边界

本次是本地环境安装验收，不是完整项目回归。未连接生产数据库、未复制真实模型或企微密钥、未调用真实模型（费用 0）、未发送真实消息。保留本地数据库结构和迁移种子；Mock 测试业务数据回滚。

## 命令与结果

- 通过：`docker compose up -d postgres`；本地 pgvector PostgreSQL 15 健康。
- 通过：`docker compose build --build-arg HTTP_PROXY=http://127.0.0.1:7890 --build-arg HTTPS_PROXY=http://127.0.0.1:7890`，随后 `docker compose up -d`。初始仓库 Dockerfile 不读取锁文件；本机 scratch 专用 Dockerfile 改为 frozen/ci 后重新构建，版本与仓库锁文件一致。
- 通过：容器启动执行 `uv run alembic upgrade head`、`uv run ran-ai-checkpoint-setup`、`uv run ran-ai-api`。
- 通过：`timeout 3 docker logs -f --tail 100 ran-ai-backend`，退出 124 为主动限制日志跟随时长；日志包含 checkpoint runtime started / Application startup complete。
- 通过：`docker exec ran-ai-backend uv run alembic current` 为 `20260904_0001 (head)`。
- 通过：本地 psql 查询扩展 vector / pg_trgm 均存在。
- 通过（api-db-check）：`python3 scratch/check_local_environment.py`：health、登录页 200，账号登录成功，前端代理 users/dashboard 200，users/dashboard/system-diagnostics 页面 200；直接无鉴权后端 API 返回 401。
- 首次失败（repo-spec）：`cd backend && uv run --frozen pytest tests/test_01_intake.py -q`，98 passed / 7 failed / 14 rerun，29.29 秒。空 AI Key 让后台入营消费跳过模型生成分支，相关文档/回执断言失败。原始日志 `/tmp/ran-ai-local-intake-test.log`。
- 复核通过（repo-spec）：`cd backend && AI_API_KEY=local-mock-only-not-a-real-key uv run --frozen pytest tests/test_01_intake.py -q`，105 passed，9.27 秒；仅测试进程使用无效占位 Key，既有 Mock 替换保持原样，普通服务 .env 仍无 Key。日志 `/tmp/ran-ai-local-intake-test-configured.log`。
- 通过：测试后 SQL 查询 users=0 / cases=0，无测试用户和服务单残留。
- 浏览器安装初次环境阻塞：`npx playwright install chromium` 不识别 Ubuntu 26.04；`PLAYWRIGHT_HOST_PLATFORM_OVERRIDE=ubuntu24.04-x64 npx playwright install chromium` 成功下载兼容构建。首次启动缺少 libnspr4.so，随后安装 `playwright install-deps chromium` 补齐系统依赖。

## 明确未执行

完整 pytest、完整 repo Playwright suite、全量契约与内容评审、真实 LLM/企微 E2E 均未执行；不以本次环境验收代替完整回归或模型质量验收。

## 浏览器最终结果

通过（临时 Playwright 脚本）：`node scratch/check_local_browser.cjs` 实际填写账号密码、点击登录、跳转 /users 并确认“正常用户”标题，截图保存在本机 scratch/local-users-smoke.png。首次脚本误把标题定位为“用户管理”而超时；对照页面源码修正定位后通过，未修改业务页面或仓库测试。
