# RAG 固定 50 题生产部署验证执行记录

- 时间：2026-08-11 01:16–01:30 CST
- 目标环境：阿里云 ECS `43.98.170.61`、生产 RDS、`https://admin.humanburning.ai`
- 发布版本：`e5ea52c4d9588228ca41c7b5780375b556c291e9`
- 边界：发布仅更新代码和 backend/frontend 镜像，未覆盖生产 `.env` 和 `docker-compose.prod.yml`，未重启 ingress/Caddy，未执行付费真实模型评测。

## 发布执行

| 项目 | 状态 | 证据 |
|---|---|---|
| 本地前端预构建 | 通过 | 发布脚本执行 `npm run build` 成功 |
| 代码打包与上传 | 通过 | 上传 82,557,661 bytes；打包排除 `.env`、生产 compose、本地调研目录和 WIP |
| 生产镜像与 checkpoint 初始化 | 通过 | backend/frontend 构建成功；`ran-ai-checkpoint-setup` 在切换前成功 |
| 生产容器切换 | 通过 | backend/frontend 重建后正常运行；ingress 持续运行 3 天且 healthy，Caddy 持续运行 10 天 |
| 构建缓存清理 | 通过 | `docker builder prune -f` 清理 1.209 GB 未引用缓存 |
| 公网恢复探测 | 通过 | 切换后后端前 5 次探测返回 502，第 6 次恢复 200；前端始终 200 |

> 发布脚本输出的 195.65 秒从“远程构建开始”计时，包含旧容器仍在服务的构建阶段，不能当作真实不可用时长。可观测的公网异常是最后切换后连续 5 次后端 502。

## 独立生产验收

| 项目 | 状态 | 线上证据 |
|---|---|---|
| 部署版本 | 通过 | `/root/ran_ai/DEPLOYED_VERSION` 记录完整 SHA `e5ea52c4d958...`、`main`、发布时间 `2026-08-11 01:16:53 +0800` |
| 工作区标记 | 已说明 | `worktree_dirty: yes` 来自发布前已存在的未跟踪调研文件；这些目录被发布 tar 显式排除，运行代码对应上述 SHA |
| 后端内部健康 | 通过 | 容器内 `/health` 返回 `{"status":"ok"}` |
| 前端内部健康 | 通过 | 容器内 `/login` 返回 HTTP 200 |
| 公网健康 | 通过 | `https://admin.humanburning.ai/health` 返回 `ok`，`/login` 返回 200 |
| 数据库迁移 | 通过 | Alembic `20260803_0068 (head)` |
| 固定题库文件 | 通过 | ECS 挂载源文件为 `rag-fixed-50-v1`；50 题，FAQ/真实聊天 25/25，检索/跳过 35/15 |
| 生产报告 API | 通过 | 新批次 `eval-20260810T172914Z`；Mock，50/50 完成，49 通过、1 失败、0 运行错误，关键事实错误 1 |
| 失败证据 | 通过 | 失败用例为 `rag-chat-cap-ambiguity`，即瓶盖与一大刻度比较关系反转 |
| FAQ 行数 | 通过 | 启用且未禁用 FAQ 仍为 208，与发布前候选盘点一致 |

## 独立 RAGAS 评测容器

| 项目 | 状态 | 证据 |
|---|---|---|
| 专用镜像 | 通过 | `ran-ai-rag-eval:local`，镜像 ID `bc68daadb0aa` |
| 依赖隔离 | 通过 | 评测镜像 `ragas=0.4.3`、`openai=2.53.0`；生产 backend 镜像未安装 RAGAS |
| 零费用 Mock | 通过 | ECS 上执行固定 50 题 Mock，不连数据库、LLM、Embedding 或消息渠道 |
| 停止态保留 | 通过 | `ran_ai-rag-eval-1` 保留为 `Exited (0)`，不常驻占用 CPU，镜像层和依赖下次直接复用 |

## 前端真实浏览器验收

- 使用生产管理员登录和 `https://admin.humanburning.ai` 运行 Playwright 只读验收。
- 结果：`1 passed in 16.2s`。
- 验证内容：页面显示 FAQ 25、真实聊天 25、应检索 35、应跳过 15；可筛选唯一失败题并展开查看预期权威事实、Mock 回复和比较反转原因。

## 未执行

- 未运行付费真实 LLM/Embedding/RAGAS 基线。
- 未调用任何真实群消息渠道或有副作用工具。
- 未调整生产评分阈值或修改题库来迎合结果。
