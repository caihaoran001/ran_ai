# 测试执行记录：入营运动频次统一

- 时间：2026-08-03 16:30 +0800
- 代码版本：`c380953 fix(documents): unify weekly activity frequency`
- 工作区：仅存在执行前已存在的 `.impeccable` 和本地调研资料未跟踪文件；本次未触碰。

| 项目 | 命令 / 方式 | 结果 | 证据 |
| --- | --- | --- | --- |
| 入营定向回归 | `cd backend && ... uv run pytest tests/test_01_intake.py -q` | 通过 | 71 项通过；覆盖共同字段填 2 次、三处原文为 2/4/5 次时统一为 3 次，且每周一次复测不被误改。 |
| 后端编译 | `python -m compileall -q src/app/services/intake_generation.py src/app/services/document_prompts.py src/app/services/document_templates.py` | 通过 | 无语法或导入错误。 |
| 后端完整回归 | `cd backend && ... uv run pytest tests -q` | 通过 | `544 passed in 16.03s`。 |
| 前端类型检查 | `cd frontend && npx tsc --noEmit --incremental false` | 通过 | 无输出、退出码 0。 |
| Playwright 用例发现 | `cd frontend && npm run test:e2e -- --list` | 通过 | 发现 3 个 spec、10 项测试。 |
| 浏览器交互 | 本地 Docker / Playwright | 环境阻塞 | 本机 Docker socket 无权限；本次未改前端，未把“用例可发现”写成页面交互通过。 |
| 真实模型全文验证 | 生产容器内直接调用当前文档模型、只读问卷样本 440 | 通过 | `deepseek-v4-flash`，传输请求 1 次；画像 3 处、诊断全文 1 处、执行全文 1 处运动频次均为每周 3 次。 |
| 生产发布与健康 | 生产后端镜像重建、检查点初始化、重建 backend | 通过 | 线上版本为 `c380953c2332`，容器内 `/health` 和公网 `/health` 均返回 `{"status":"ok"}`；最近 100 行后端日志无 `ERROR` / `Traceback`。 |

真实模型验证的边界：只读一份既有问卷并在内存中生成、归一化和渲染；未创建服务单、画像、交付文件或消息，也未写模型用量账本。验证结束后已删除生产机及容器内临时脚本和日志。

部署说明：常规完整包两次均因执行环境约 30 秒前台限制在上传阶段被终止，生产版本始终保持旧版且健康；随后只上传本提交涉及的三个后端源文件，在生产机重建 backend 镜像后完成切换。中间提交均为测试报告，未遗漏运行时代码。
