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
| **部署代码**（日常、高频） | `uv run --no-project --with paramiko python scratch/local_prebuilt_deploy.py` | 只更新代码（后端 `src`、前端 standalone）并重建/重启 `backend`、`frontend`；常驻 `ingress` 和 Caddy 不动。切换前自动执行 Alembic 业务表迁移和 LangGraph checkpoint 初始化/升级。**不碰**服务器的 `.env` 和 `docker-compose.prod.yml`（打包已排除） |
| **改 compose 结构**（少见） | `uv run --with paramiko python scratch/configure_remote_files.py` | 用仓库当前 `docker-compose.prod.yml` **整体覆盖**服务器 compose；**默认不碰 `.env`** |
| **改 .env 密钥**（很少） | 直接在服务器上 `nano /root/ran_ai/.env` | 线上 `.env` 由人手维护、以线上为准；见下方警告 |

**为什么分开**：部署代码很频繁；如果每次部署都覆盖 `.env`，一旦有人在服务器上手改过配置（如换 QiWe `guid`），就会被模板覆盖回去、搞坏线上。所以**部署代码绝不动配置**；改配置是一个单独、显式的动作。

> ⚠️ **`configure_remote_files.py` 内嵌的 `.env` 模板已严重过期**（实测 2026-07-31：线上 `QIWEI_GUID` / `AI_API_KEY` / `QWEN_API_KEY` 与模板不同，且线上多出 `AI_AGENT_ENABLED` / `WEB_SEARCH_ENABLED` / `TAVILY_API_KEY` 等键）。所以该脚本**默认只推 compose、不写 `.env`**。要改线上密钥请直接改服务器上的 `.env`；只有确认模板已和线上逐键对齐，才可用 `--with-env` 覆盖。

**改 compose 结构的正确步骤**：① 改仓库根目录的 `docker-compose.prod.yml` → ② 跑 `configure_remote_files.py`（只推 compose）→ ③ 按需重启容器。

> ⚠️ 给后续接手者（含 AI 助手）：**不要在 `local_prebuilt_deploy.py` 里加回"覆盖 .env / docker-compose.prod.yml"的步骤**。compose 结构变更走 `configure_remote_files.py`，`.env` 只在服务器上手改，保持部署代码与改配置解耦。

### 一次性场景：ingress 代码或 compose 服务名变化时

日常部署**故意不重建 `ingress`**（它常驻、稳定，只用旧的已知good镜像）。但当 `ingress` 的代码（新增路由、鉴权）或它在 compose 里的服务名/环境发生变化时，必须做一次性重建（顺序很重要，避免 `frontend` 指向尚不存在的 `ingress`）：

1. `local_prebuilt_deploy.py` 先部署新代码、构建新镜像（`backend`/`ingress` 共用 `ran-ai-backend:local`），此时服务器 compose 仍是旧的、C 端仍由 `backend` 兜底服务，全程可用。
2. `configure_remote_files.py` 推新 compose（只推 compose、不碰 `.env`）；容器还没重建，无副作用。
3. 先单独把 `ingress` 起在新镜像上并验活：`docker compose -f docker-compose.prod.yml up -d ingress`，再 `docker exec ran_ai-ingress-1 python -c "import urllib.request;print(urllib.request.urlopen('http://localhost:8001/health').read())"`（顺带用真实 token 打一次 `/api/questionnaires/{token}` 确认新路由返回 200）。**必须等 `ingress` 起来并验活后**，才让 `frontend` 切过去。
4. 一次性 `docker compose -f docker-compose.prod.yml up -d --remove-orphans` 重建 `frontend`（拿到 `SERVER_INGRESS_BASE_URL`→`ingress:8001`）、重建 `backend`（新 compose 定义），并清掉改名后的孤儿 `qiwe-ingress`。
5. **`caddy` 必须显式 `docker restart ran_ai-caddy-1`**——它的 Caddyfile 是 bind-mount，部署写入的是**新 inode 的新文件**，而 `up -d` 不会因挂载文件内容变化而重建 caddy，`caddy reload` 也只会重读同一个旧 inode；不重启 caddy 会继续把回调解析到改名前的 `qiwe-ingress` upstream → 回调 502。重启后用 `docker exec ran_ai-caddy-1 grep ingress /etc/caddy/Caddyfile` 确认容器内已是 `ingress:8001`。

此过程 `frontend`/`backend`/`caddy` 重建有几秒公网抖动，`ingress` 全程不重启、C 端公开入口不吃 502；入站企微消息由 `inbound_events` 落库+重放兜底，不丢。

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

- `backend`：FastAPI 后端。标准部署会在切换容器前预执行 Alembic migration；容器启动时仍会再执行一次作为幂等安全网。
- `ingress`：稳定常驻的轻量【入站边缘入口】，只持 DB 连接池、绝不跑 LLM/调度器/langgraph。承载两类外部入站流量：① QiWe 回调（鉴权解析后写入 RDS `inbound_events`）；② 面向 C 端用户、免 admin key 的公开入口——公开问卷展示与提交、短链跳转。日常业务部署不重启它，`backend` 频繁重启时这些外部入口不吃 502。
- `frontend`：Next.js standalone 生产服务。C 端三处公开调用（问卷展示 GET、问卷提交 POST、短链 GET）在 SSR/route 里经 `SERVER_INGRESS_BASE_URL` **直连 `ingress:8001`**（不走 Caddy）；后台管理 `/api/*` 仍经前端代理直连 `backend`。
- `caddy`：公网 HTTP/HTTPS 入口。

数据库使用阿里云 RDS PostgreSQL 15，不在 ECS 的 Docker Compose 中启动 PostgreSQL。后端通过 `.env` 中的 `DATABASE_URL` 连接 RDS：

```env
DATABASE_URL=postgresql+asyncpg://用户名:密码@RDS内网地址:5432/数据库名
```

推荐优先使用 RDS 内网地址，并确保 ECS 与 RDS 在同一 VPC/可达网络内。

回复 Agent 的 LangGraph checkpoint 也复用这个 RDS，只创建普通 PostgreSQL
表，不需要新实例或额外扩展。`scratch/local_prebuilt_deploy.py` 会在新镜像构建完成、
旧容器仍运行时，先执行 `uv run alembic upgrade head`，再执行
`uv run ran-ai-checkpoint-setup`。如果迁移、建表或建索引失败，部署会在切换容器前中止并保留
旧版本；此时再根据错误补齐目标 schema 的 DDL 权限。

Caddy 路由规则：

- `/api/channels/qiwe/callback` 直连 `ingress`，先落 RDS 再返回 2xx。
- 其他 `/api/channels/qiwe/*` 仍直连 `backend`。
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
AI_MODEL=deepseek-v4-pro
AI_DOCUMENT_MODEL=deepseek-v4-pro
AI_PROMPT_COST_PER_1M_TOKENS=12.55
AI_COMPLETION_COST_PER_1M_TOKENS=25.11
```

`AI_DOCUMENT_MODEL` 只影响入营交付文件和阶段报告；聊天回复、风控识别等仍按 `AI_MODEL` / `AI_RISK_MODEL` 走，避免把群聊回复一起切到更慢模型。

## 5. RDS 准备

当前生产数据库使用阿里云 RDS PostgreSQL 15，并已安装 `vector` 插件。上线前仍建议确认以下事项：

1. RDS 白名单 / 安全组允许 ECS 访问。
2. 数据库、用户、密码已创建。
3. `vector` 插件可用。
4. `pg_trgm` 插件可用。项目会在 migration 中执行 `CREATE EXTENSION IF NOT EXISTS pg_trgm`，用于中文关键词 fallback 检索的 GIN 索引。
5. 应用数据库账号可在目标 schema 创建普通表和索引（LangGraph 首次 checkpoint 初始化需要；不需要新增扩展）。

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
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml run --rm --no-deps backend uv run alembic upgrade head
docker compose -f docker-compose.prod.yml run --rm --no-deps backend uv run ran-ai-checkpoint-setup
docker compose -f docker-compose.prod.yml up -d
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
docker compose -f docker-compose.prod.yml logs --tail 100 ingress
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
