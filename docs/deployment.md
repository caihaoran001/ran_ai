# 燃AI 公有云部署说明

本文档说明如何把当前项目从本机开发模式部署到云服务器生产模式。

## 1. 本地开发和云端生产的区别

同一套代码支持两种运行方式：

| 场景 | 命令 | 目的 | 特点 |
|---|---|---|---|
| 本地开发 | `docker compose up -d` | 改代码、调试、测试 | 挂载源码目录，前端使用 dev server |
| 生产/云端 | `docker compose -f docker-compose.prod.yml up -d --build` | 长期稳定运行 | 不挂载源码，前端先 build 后 start，Caddy 提供 HTTPS 入口 |

生产化不会破坏本地开发方式。`docker-compose.yml` 继续用于本机开发，`docker-compose.prod.yml` 用于云端或本地模拟生产。

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

- `postgres`：PostgreSQL + pgvector 镜像。
- `backend`：FastAPI 后端，启动时自动执行 Alembic migration。
- `frontend`：Next.js standalone 生产服务。
- `caddy`：公网 HTTP/HTTPS 入口。

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

## 5. 云服务器首次部署

推荐先用单台云服务器：

- Ubuntu 22.04/24.04
- Docker + Docker Compose
- 2C4G 起步，4C8G 更稳
- 域名解析到服务器公网 IP

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

## 6. QiWe callback 配置

生产环境 callback 地址：

```text
https://your-domain.com/api/channels/qiwe/callback?secret=你的_QIWEI_CALLBACK_SECRET
```

如果 QiWe 后台支持自定义 header，也可以使用：

```text
X-QIWEI-CALLBACK-SECRET: 你的_QIWEI_CALLBACK_SECRET
```

## 7. 上线前检查

部署后执行：

```bash
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs --tail 100 backend
```

然后按测试规范执行自动化检查：

- `docs/testing/README.md`
- `docs/testing/TEST_CASES.md`

默认先跑非真实 E2E。真实 `E2E-01` 只在明确授权后执行。

## 8. 备份与运维底线

至少要做：

- 定期备份 PostgreSQL。
- 保存 `.env` 到安全位置，不放 git。
- 监控磁盘、内存、CPU。
- 查看 AI 调用成本看板。
- 出问题时先看后端日志：

  ```bash
  docker compose -f docker-compose.prod.yml logs -f backend
  ```

MVP 阶段可以先用 Docker volume 保存数据库；真实客户数据增长后，建议迁移到云厂商托管 PostgreSQL。
