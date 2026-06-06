# 测试与诊断待办汇总

**更新时间**：2026-06-06
**用途**：收敛已删除临时 review 工单中尚未完全落地或需要确认的事项。已修复项不再保留原工单，避免后续 Agent 重复处理。

---

## 已确认修复并移除原工单

- 诊断 `active_run` 卡死：已实现 stale 清理、超时、手动 reset，并补诊断单测。
- Docker 后端 reload + SSE 卡住页面：Docker Compose 后端默认关闭 Uvicorn reload。
- E2E-01 顺序：已放到 `01-08` 之后。
- E2E 进度解析、汇总日志、自动滚动、成本区分：已实现基础版本。
- E2E 质量评审失败规则：已收敛为“任一评分子项 < 3 即失败”。
- 蔬菜打卡硬替换：已移除，保留提示词约束。
- 诊断 history 持久化：后端支持 `DIAGNOSTICS_HISTORY_PATH`，Docker 默认挂载到宿主机 `./diagnostics/diagnostics_history.json`。
- Playwright 基线：已新增 `frontend/playwright.config.ts` 与 `frontend/e2e/admin-smoke.spec.ts`，覆盖后台核心页面和诊断台基础结构。
- Mock Time 归属：成效追问时间感知已并入 `backend/tests/test_08_outcome_engine.py`，不再单独拆文件。

---

## 仍建议补齐

### 1. E2E 结果优化闭环

最近一次可恢复的 DB 证据：

- `diagnostic_run_id`: `e2e-1780651033-295f6df2`
- 调用次数：521
- tokens：2,141,611
- 成本：¥2.5306
- 时间：2026-06-05 17:17:19 到 18:33:55（北京时间）

该轮详细 stdout 已在容器重建前丢失。后续新跑 E2E-01 时，终端日志会写入宿主机 `./diagnostics/diagnostics_history.json`，应基于新的持久化结果做针对性优化。

### 2. E2E 质量评审稳定性

当前采用 1 次综合 LLM 评审，优点是省 token、避免 4 次评审互相矛盾；缺点是单次输出较长，失败原因可能不够细。

建议保持当前方案，只有当连续出现“评审理由太粗无法定位”时，再考虑改为：

- 先 1 次综合评审；
- 对低于 3 分的维度再追加专项复核；
- 不默认 4 次全量评审。

### 3. Playwright 深度交互覆盖

当前仅有后台核心页面 smoke spec。后续可继续补：

- 问卷填写、提交、已提交态。
- 每日行动链接字段与执行文档 Block 7 一致性。
- 审核控制台 SSE 自动刷新。
- 删除用户、已删除服务单访问与软删除隔离。
