# 燃人AI 公有云部署说明

本文档说明如何把当前项目从本机开发模式部署到云服务器生产模式。

## 1. 本地开发和云端生产的区别

同一套代码支持两种运行方式：

| 场景 | 命令 | 目的 | 特点 |
|---|---|---|---|
| 本地开发 | `docker compose up -d` | 改代码、调试、测试 | 挂载源码目录，前端使用 dev server |
| 生产/云端 | `docker compose -f docker-compose.prod.yml up -d --build` | 长期稳定运行 | 不挂载源码，前端先 build 后 start，Caddy 提供 HTTPS 入口 |

生产化不会破坏本地开发方式。`docker-compose.yml` 继续用于本机开发，`docker-compose.prod.yml` 用于云端或本地模拟生产。

## 1.5 实际部署流程：代码部署 vs 配置变更（重要，含 AI 接手须知）

线上用 `scratch/local_prebuilt_deploy.py` 部署（本地编译前端 → 打包 → 上传 ECS → rebuild → 健康检查）。**代码和配置分开管，互不干扰：**

| 你要做的 | 跑什么 | 会动什么 |
|---|---|---|
| **部署代码**（日常、高频） | `uv run --no-project --with paramiko python scratch/local_prebuilt_deploy.py` | 只更新代码（后端 `src`、前端 standalone）+ rebuild 容器。**不碰**服务器的 `.env` 和 `docker-compose.prod.yml`（打包已排除） |
| **改生产配置**（少见） | `uv run --with paramiko python scratch/configure_remote_files.py` | 用脚本内置模板**整体覆盖**服务器的 `.env` 和 `docker-compose.prod.yml` |

**为什么分开**：部署代码很频繁；如果每次部署都覆盖 `.env`，一旦有人在服务器上手改过配置（如换 QiWe `guid`），就会被模板覆盖回去、搞坏线上。所以**部署代码绝不动配置**；改配置是一个单独、显式的动作。

**改生产配置的正确步骤**：① 先改 `scratch/configure_remote_files.py` 里的模板 → ② 跑 `configure_remote_files.py` 推到服务器 → ③ 按需重启容器。

> ⚠️ 给后续接手者（含 AI 助手）：**不要在 `local_prebuilt_deploy.py` 里加回"覆盖 .env / docker-compose.prod.yml"的步骤**。任何生产配置变更都走 `configure_remote_files.py`，保持部署代码与改配置解耦。

## 2. `.env` 为什么不进 git，但部署还能用

`.env` 里包含 AI Key、QiWe Token、数据库密码、后台密码等敏感信息，不能提交到 git。

正确做法：

1. 仓库提交 `.env.production.example`，只放占位符和配置模板。
2. 云服务器上手工创建真实 `.env`：

   ```bash
   cp .env.production.example .env
   nano .env
   ```

3. `docker compose -f docker-compose.prod.yml ...` 会自动读取当前目录的 `.env`。
4. 生产 compose 中关键变量使用 `${VAR:?message}`，如果没填会直接启动失败，避免用弱默认值上线。

不要把本机开发用的 `.env` 原样复制到云服务器。生产环境建议从 `.env.production.example` 新建一份，只填写云端真实需要的域名、数据库密码、AI Key、QiWe Key、后台账号等配置。

如果后续使用云厂商密钥管理或 CI/CD，也可以在部署流程中把密钥注入为环境变量，不需要把真实 `.env` 放进仓库。

## 3. 生产服务结构

生产 compose 包含：

- `backend`：FastAPI 后端，启动时自动执行 Alembic migration。
- `frontend`：Next.js standalone 生产服务。
- `caddy`：公网 HTTP/HTTPS 入口。

数据库使用阿里云 RDS PostgreSQL 15，不在 ECS 的 Docker Compose 中启动 PostgreSQL。后端通过 `.env` 中的 `DATABASE_URL` 连接 RDS：

```env
DATABASE_URL=postgresql+asyncpg://用户名:密码@RDS内网地址:5432/数据库名
```

推荐优先使用 RDS 内网地址，并确保 ECS 与 RDS 在同一 VPC/可达网络内。

Caddy 路由规则：

- `/api/channels/qiwe/*` 直连后端，用于 QiWe callback。
- `/health` 直连后端。
- 其他请求全部进入前端。

不要把所有 `/api/*` 都直连后端。后台管理页面依赖前端 `/api/backend/*` 代理注入内部 `BACKEND_ADMIN_API_KEY`。

## 4. 文档文件存储策略

生产模式下不依赖长期保存 docx 文件。

系统持久化保存：

- `user_documents.content_json`
- `user_documents.content_md`
- 审核状态、发送状态、时间戳

系统临时生成：

- 用户点击下载时，从 `content_json` 即时渲染 docx，响应结束后删除临时文件。
- 发送文件到群时，从 `content_json` 即时渲染 docx，上传 QiWe 后删除临时文件。

这样即使容器重建或磁盘清理，历史文档仍可以从数据库重新生成。

交付文件/阶段报告可使用独立模型：

```env
AI_MODEL=deepseek-v4-flash
AI_DOCUMENT_MODEL=deepseek-v4-pro
```

`AI_DOCUMENT_MODEL` 只影响入营交付文件、阶段报告和毕业报告；聊天回复、风控识别等仍按 `AI_MODEL` / `AI_RISK_MODEL` 走，避免把群聊回复一起切到更慢模型。

## 5. RDS 准备

当前生产数据库使用阿里云 RDS PostgreSQL 15，并已安装 `vector` 插件。上线前仍建议确认以下事项：

1. RDS 白名单 / 安全组允许 ECS 访问。
2. 数据库、用户、密码已创建。
3. `vector` 插件可用。
4. `pg_trgm` 插件可用。项目会在 migration 中执行 `CREATE EXTENSION IF NOT EXISTS pg_trgm`，用于中文关键词 fallback 检索的 GIN 索引。

可以在 RDS 上执行：

```sql
SELECT version();
SELECT name, installed_version
FROM pg_available_extensions
WHERE name IN ('vector', 'pg_trgm');
```

如果 `pg_available_extensions` 能查到但 `installed_version` 为空，说明扩展可用但尚未安装；后端启动时 Alembic 会尝试安装。若 RDS 权限不足，需要在 RDS 控制台或用高权限账号预先安装。

## 6. 云服务器首次部署

推荐先用单台云服务器跑应用容器：

- Ubuntu 22.04/24.04
- Docker + Docker Compose
- 2C4G 起步，4C8G 更稳
- 域名解析到服务器公网 IP
- 阿里云 RDS PostgreSQL 15 作为数据库

步骤：

```bash
git clone <your-repo-url> ran_ai
cd ran_ai
cp .env.production.example .env
nano .env
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs -f backend
```

确认：

```bash
curl https://your-domain.com/health
```

应返回：

```json
{"status":"ok"}
```

确认数据库 migration 状态：

```bash
docker compose -f docker-compose.prod.yml exec backend uv run alembic current
```

## 7. QiWe callback 配置

生产环境 callback 地址：

```text
https://your-domain.com/api/channels/qiwe/callback?secret=你的_QIWEI_CALLBACK_SECRET
```

如果 QiWe 后台支持自定义 header，也可以使用：

```text
X-QIWEI-CALLBACK-SECRET: 你的_QIWEI_CALLBACK_SECRET
```

## 8. 上线前检查

部署后执行：

```bash
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs --tail 100 backend
```

然后按测试规范执行自动化检查：

- `docs/testing/README.md`
- `docs/testing/TEST_CASES.md`

默认先跑非真实 E2E。真实 `E2E-01` 只在明确授权后执行。

## 9. 备份与运维底线

至少要做：

- 定期备份 RDS PostgreSQL，并确认备份可恢复。
- 保存 `.env` 到安全位置，不放 git。
- 监控磁盘、内存、CPU。
- 查看 AI 调用成本看板。
- 出问题时先看后端日志：

  ```bash
  docker compose -f docker-compose.prod.yml logs -f backend
  ```

不要在 ECS 上再运行第二套生产 PostgreSQL，避免线上数据分裂。开发环境仍可继续使用本地 `docker-compose.yml` 内置的 PostgreSQL。
