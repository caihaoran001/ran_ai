# RAG 固定 50 题评测集执行记录

- 时间：2026-08-11 01:02 CST
- 范围：固定题库、评测运行时、报告兼容、前端逐题证据展示
- 边界：未执行真实 LLM、Embedding 或 RAGAS 付费调用；未发送真实群消息；未将本次代码发布到 ECS

## 数据与安全边界

- 通过 ECS 对生产 RDS 做一次性只读候选盘点：208 条启用 FAQ、12,648 条已分析用户消息。
- 仅执行 `SELECT`，不写业务表；候选查询脚本用后已删除，未纳入提交。
- 25 条真实聊天题已去除人名、手机号、地址、群名等直接身份信息；页面仅保留不可直接识别个人的固定来源引用。

## 执行结果

| 项目 | 状态 | 命令 / 证据 |
|---|---|---|
| 固定题库结构 | 通过 | `uv run python scripts/validate_rag_eval_cases.py`；50 题，FAQ 改写 25、真实聊天 25，应检索 35、应跳过 15，9 类关键风险标签均覆盖 |
| 后端题库与运行时 | 通过 | `uv run pytest tests/test_rag_eval_cases.py tests/test_rag_eval_runtime.py -q`；`9 passed in 0.95s` |
| 报告 API 与旧报告兼容 | 通过 | 后端容器执行 `pytest tests/test_rag_evaluation_reports.py -q`；`3 passed in 1.41s` |
| Mock 评测容器 | 通过 | 固定集 50/50 完成，49 通过、1 失败、0 跳过、0 运行错误；故意注入的瓶盖比较反转被记为 1 次严重事实错误 |
| Mock 报告完整性 | 通过 | 报告记录 FAQ/真实聊天来源各 25 条、5 类预期处理方式；不再输出“禁止说法”字段 |
| 前端类型检查 | 通过 | `npx tsc --noEmit --incremental false` |
| 前端生产构建 | 通过 | `npm run build`；`/rag-evaluations` 构建成功，First Load JS 112 kB |
| 页面基础冒烟 | 通过 | Playwright `/rag-evaluations loads`；`1 passed` |
| 固定题库与逐题证据 | 通过 | Playwright 确认 25+25、35+15，并展开失败题查看权威事实、模拟回复与反转原因；`1 passed in 2.5s` |
| 本地容器验收 | 通过 | 后端修改后已重启，容器内 `/health` 返回 `ok`；前端容器已重建，容器内 `/login` 返回 HTTP 200 |
| 真实模型质量基线 | 未执行 | 本次用户未明确要求“运行真实 E2E/真实模型测试”，依项目协议不产生付费调用 |
| 全量回归 | 未执行 | 本次执行改动相关定向测试，未执行全量后端 pytest 和全量 Playwright |

## 过程中的偏差

- 直接用系统 Python 运行题库校验时，由于未进入项目的 `uv` 环境而导入失败；改用规范命令后通过，不是业务失败。
- 浏览器证据用例首次把“默认已展开”的失败项又点击了一次，导致其收起；测试改为先读取展开状态后稳定通过，未修改业务逻辑迎合测试。

## 数据与费用

- 生产数据库写入：0。
- 真实模型与 Embedding 调用：0。
- 真实群消息：0。
- Mock 报告：`diagnostics/rag-evaluations/eval-20260810T165053Z/report.json`。
