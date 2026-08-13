# 测试执行记录（2026-08-13 10:40 CST）

## 环境与范围

- 基线提交：`fc00f63 feat(checkin): unify completion and reward settlement`
- 工作区：本次问卷奖励回执、文档五项示例及测试改动未提交；另有既存未跟踪文件，未纳入本次改动。
- 范围：入营问卷回执、生成时五项示例答案、两份文件发送完成触发、批量/逐份幂等、前端默认回执，以及全量后端回归和前端构建。
- 真实 LLM / 真实消息：本阶段未调用、未发送、未产生费用、未保留测试业务数据。

## 执行明细

1. `PYTHONPYCACHEPREFIX=/tmp/ran_ai_pycache env UV_CACHE_DIR=/tmp/uv-cache uv run python -m compileall -q src scripts`
   - 状态：通过。
2. `env UV_CACHE_DIR=/tmp/uv-cache PYTHONPYCACHEPREFIX=/tmp/ran_ai_pycache uv run pytest tests/test_checkin_questionnaires.py tests/test_service_plan_activation.py tests/test_01_intake.py -q`
   - 状态：通过，`97 passed`。
   - 覆盖：五个个性化原题逐字同序、第一项不被通用产品题替换、`原题｜已填写答案`、两份齐发一次、重复发送不重复、批量/逐份一致、历史答案缺失时跳过、奖励开关控制承诺。
3. `env UV_CACHE_DIR=/tmp/uv-cache PYTHONPYCACHEPREFIX=/tmp/ran_ai_pycache uv run pytest tests -q`
   - 状态：失败，`747 passed, 6 failed, 12 rerun`。
   - 失败全部位于 `tests/test_curated_video_distribution.py`，表现为测试间遗留视频分发数据导致游标、每日运行和目标群断言污染；与本次改动文件、问卷、文档发送链路无交叉。
4. `env UV_CACHE_DIR=/tmp/uv-cache PYTHONPYCACHEPREFIX=/tmp/ran_ai_pycache uv run pytest tests/test_curated_video_distribution.py -q`
   - 状态：失败，`1 passed, 6 failed, 12 rerun`，单文件复现同一既存隔离问题。
5. `npx tsc --noEmit --incremental false`
   - 状态：通过。
6. `npm run build`
   - 状态：通过，Next.js 生产构建成功，33 个静态页面生成完成。
7. `npm run test:e2e -- --list`
   - 状态：通过，发现 14 个 Playwright 用例。
8. 完整浏览器交互 `npm run test:e2e`
   - 状态：未执行；本次修改是后台默认回执文本与发送契约，核心行为已由 Mock 通道的后端契约测试覆盖，生产部署后另做真实服务单验收。
9. `docker restart ran-ai-backend` 后 `curl -sS -f http://127.0.0.1:8000/health`
   - 状态：通过，返回 `{"status":"ok"}`。

## 失败证据与复现

- 复现命令：`cd backend && env UV_CACHE_DIR=/tmp/uv-cache PYTHONPYCACHEPREFIX=/tmp/ran_ai_pycache uv run pytest tests/test_curated_video_distribution.py -q`
- 代表性失败：首次采集预期 `inserted == 0`，实际读到既存事件并得到 `1`；午前预期当日运行数为 `0`，实际为 `1`。日志中同一源群事件编号持续递增，证明该测试文件的重跑隔离未清理其独立提交数据。
