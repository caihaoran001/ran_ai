# 测试执行记录 · 2026-07-28 18:47

范围：入营交付文档（《个人能量重启诊断报告》《28天高燃人生执行建议书》）本轮修复的回归验证 + 隔离真实模型内容复测。

对应提交：`ce98960` `4b6f7f3` `c1f800d`（在 `482ff45` `1a8de4f` 之后）。

---

## 1. 自动化测试（Mock LLM）

| 命令 | 结果 |
|---|---|
| `uv run pytest tests/ -q -p no:randomly --ignore=tests/test_e2e_01_real_llm.py` | **472 passed**，0 failed |
| `uv run pytest tests/test_01_intake.py -q -p no:randomly` | **24 passed** |
| `npx tsc --noEmit --incremental false`（frontend） | exit 0 |

本轮新增测试（全部为先失败后通过，不是改预期让它过）：

- `test_daily_checkin_keeps_number_question_without_unit_bracket`
- `test_daily_checkin_fallback_items_carry_their_category`
- `test_daily_checkin_logs_every_substitution`
- `test_shared_analysis_is_the_single_source_for_cross_document_facts`
- `test_force_sync_keeps_document_text_when_shared_analysis_is_empty`
- `test_intake_prompt_requires_five_independent_daily_checkin_questions` 增补 6/10 节分工与厘米数占位符断言

**环境阻塞（非本轮引入）**：`npm run lint` 无法执行——Next 已弃用 `next lint`，命令会进入交互式 ESLint 配置向导。记为未执行，不计入通过/失败。

## 2. 隔离真实模型复测

### 隔离条件（执行前后均已核对）

- 样本来自生产 RDS **只读**导出的 3 份匿名入营问卷（去除身份字段），落在本机 `/tmp/realtest/`
- 提示词用**本地新代码**构造（生产容器跑的是旧代码），只借用容器内密钥
- 直连 `AsyncOpenAI`，**绕过 `AIService`**，因此不写 `ai_usage_logs`
- 不调用任何业务生成接口，不写 `user_documents` / `user_profiles` / `service_cases`
- 不发送任何 QiWe 消息，产物只在 `/tmp`

### 生产零写入复查（测试后，只读）

窗口 `2026-07-28 17:00+08` 起：

| 表 | 结果 |
|---|---|
| `user_documents` 新增 / 更新 | 0 / 0 |
| `user_profiles` 新增 / 更新 | 0 / 0 |
| `service_cases` 新增 | 0 |
| `questionnaire_submissions` 新增 | 0 |
| `case_no like 'ISOLATED-TEST%'` | 0 |
| `ai_usage_logs` 新增 | 66 —— 场景全为 `reply_agent`(28) / `intervention_risk`(15) / `archivist_extract`(13) / `qwen_vl_translate`(9) / `agent_reminder`(1)，**无任何文档生成场景** |
| `agent_runs` 新增 | 35 —— `reply/interactive`(21) / `archivist/scheduled_scan`(13) / `reply/scheduled_reminder`(1) |
| 外发消息 | 5 条，内容均为真实用户对话（化验结果、咳嗽、戒指传感器等） |

结论：新增记录全部为线上真实业务流量，无一条可归因于本次测试。**测试对生产零写入。**

### 逐项结果（3 份样本，模型 `deepseek-v4-pro`，temperature 0.3）

| 验收项 | 结果 |
|---|---|
| 管理带是业务带位，不含内部复核标签 | ✅ 3/3 |
| 用户可见正文无内部标签泄漏 | ✅ 3/3 |
| 五项打卡结构完整（order 1-5、无栏目标题污染、含 activity 项） | ✅ 3/3 |
| 打卡项 category 全部有值 | ✅ 3/3（修复前样本1010 第5项为空） |
| 执行建议书真实 PDF 页数 = 1 页 A4 | ✅ 3/3（`soffice --convert-to pdf` + `pdfinfo`） |
| 诊断报告页数在 2–4 页 | ✅ 3/3（4 / 3 / 4 页；样本1010 由 4 页降至 3 页） |
| 蔬菜口径「每餐」冲突 | ✅ 0/3（上一轮 1/3 失败） |
| 无编造厘米数 | ✅ 3/3（上一轮样本1017 编出「腰围缩小5cm以上」，本轮消失；样本1010 的 1–2cm 可追溯到用户自填「腰围减少10厘米」并被正确判定过激后下调） |
| 6节「优先解决的三件事」只讲原因、不含可量化执行参数 | ✅ 3/3 |
| 10节「本周直接执行」4–5 条、只讲动作、覆盖三件事之外的维度 | ✅ 3/3（均为 5 条，均含产品节律项） |
| 两份文件 28天任务逐字一致 | ✅ 3/3（强制覆写生效） |
| 两份文件总路径目标逐字一致 | ✅ 3/3 |
| 趋势直接证据 / 当前状态支持信号分离 | ✅ 3/3 |
| 换题留痕日志 | ✅ 样本555 有一项复合问题（「晚餐主食是否≤1拳并且晚餐后未进食」）被正确判废，warning 完整打出 rejected / fallback_used |

### 未通过 / 待跟进（如实记录）

**F-1｜同一份问卷两次运行的带位与安全闸门结论不稳定（本轮新发现，未修复）**

样本1010（BMI 21.3）：

| | 第 2 轮（18:04） | 第 3 轮（18:44） |
|---|---|---|
| 模型 `weight_band` | A带｜修复塑形 | **B带｜轻盈启动** |
| `manual_review_required` | **true**（理由：持续明显不适——总是累、便秘、胀气；49岁女性睡眠破碎） | **false** |
| `band_confidence` | medium | high |

问卷内容完全相同，提示词仅新增 6/10 节分工与厘米数约束（与安全闸门无关）。BMI 21.3 的确定性带位是 A带｜修复塑形，但 `_resolve_management_band` 设计上优先采用模型的综合判断带位，因此最终落地 B带。

安全闸门**漏报**（true → false）是危险方向：同样的健康信号一次触发人工复核、一次不触发，意味着复核覆盖不可预期。属于可靠性问题，不在本轮范围内，建议单独立项。

---

## 3. 复现方式

```bash
# Mock 全量
cd backend && uv run pytest tests/ -q -p no:randomly --ignore=tests/test_e2e_01_real_llm.py

# 隔离真实模型（需授权，会产生模型费用；脚本在 /tmp/realtest/，未入库）
uv run python /tmp/realtest/build_prompts.py     # 本地新代码构造提示词
python3 /tmp/realtest/run_remote.py              # 容器内直连模型，绕过 AIService
uv run python /tmp/realtest/evaluate.py          # 本地渲染 + soffice/pdfinfo + 验收检查
```
