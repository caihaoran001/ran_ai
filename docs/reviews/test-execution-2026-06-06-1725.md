# 燃人AI 测试执行记录（第二轮）

## 测试环境

- **执行时间**: 2026-06-06 17:25 (UTC+8)
- **当前 Commit**: bae3385 improve diagnostics e2e progress
- **工作区状态**: 未跟踪文件 `.codex/` 和上轮测试报告
- **Docker 服务**: 全部运行中

## 测试结果汇总

### SPEC（Repo Spec 命令级）- 5 个

| ID | 状态 | 说明 |
|------|------|------|
| SPEC-01 | ✅ 通过 | 后端语法检查 |
| SPEC-02 | ⚠️ 超时 | pytest 全量运行超时，单独运行正常 |
| SPEC-03 | ✅ 通过 | 前端类型检查 |
| SPEC-04 | ✅ 通过 | Playwright spec 列表 |
| SPEC-05 | ⚠️ 环境阻塞 | 本地没装 Playwright 浏览器 |

### HC（诊断控制台健康检查）- 8 个

| ID | 状态 | 测试数量 |
|------|------|----------|
| HC-01 | ✅ 通过 | 7 passed |
| HC-02 | ✅ 通过 | 9 passed |
| HC-03 | ✅ 通过 | 4 passed |
| HC-04 | ✅ 通过 | 4 passed |
| HC-05 | ✅ 通过 | 5 passed |
| HC-06 | ✅ 通过 | 16 passed |
| HC-07 | ✅ 通过 | 15 passed |
| HC-08 | ✅ 通过 | 30 passed |

**总计**: 90 passed, 0 failed

### CT（契约与内容准确性）- 8 个

| ID | 状态 | 说明 |
|------|------|------|
| CT-01 | ✅ 通过 | BMI 字段一致（服务单2853） |
| CT-02 | ✅ 通过 | 打卡字段一致（5项） |
| CT-03 | ✅ 通过 | 打卡字段类型正确 |
| CT-04 | ✅ 通过 | 最新文档优先 |
| CT-05 | ✅ 通过 | 文档口径一致 |
| CT-06 | ✅ 通过 | 风险状态一致 |
| CT-07 | ✅ 通过 | 成效数据一致 |
| CT-08 | ✅ 通过 | 成本统计正确 |

### PW（Playwright MCP 页面交互）- 9 个

| ID | 状态 | 说明 |
|------|------|------|
| PW-01 | ✅ 通过 | 登录与导航正常 |
| PW-02 | ⚠️ 未执行 | 需要有效问卷 token |
| PW-03 | ⚠️ 未执行 | 需要有效服务单 |
| PW-04 | ✅ 通过 | 用户管理正常 |
| PW-05 | ✅ 通过 | 服务单详情正常 |
| PW-06 | ✅ 通过 | 审核控制台正常 |
| PW-07 | ⚠️ 未执行 | 需要检查配置保存 |
| PW-08 | ✅ 通过 | 数据看板正常 |
| PW-09 | ✅ 通过 | 诊断控制台正常 |

### E2E-01（真实 LLM 测试）- 用户手动运行

| 人设 | 状态 |
|------|------|
| 林晴 (positive_cooperator) | ✅ PASSED |
| 周宁 (anxious_questioner) | ✅ PASSED |
| 陈默 (quiet_noncompliant) | ❌ FAILED |
| 何夜 (shift_worker) | ✅ PASSED |
| 许敏 (caffeine_sensitive) | ❌ FAILED |
| 赵稳 (plateau_frustrated) | ✅ PASSED |
| 吴退 (refund_complaint) | ❌ FAILED |
| 顾安 (medical_caution) | ✅ PASSED |
| 宋续 (positive_renewal) | ❌ FAILED |
| 沈心 (low_mood_recovery) | 🔄 运行中 |
| 全链路测试 | 🔄 运行中 |

**E2E 结果**: 5 passed, 4 failed, 2 running

## 与上轮对比

| 测试 | 上轮 | 本轮 | 变化 |
|------|------|------|------|
| HC-01 | ✅ | ✅ | 新增1个测试 |
| HC-04 | ✅ | ✅ | test_graduation_report 修复 |
| CT-01 | ❌ | ✅ | BMI 不一致问题修复 |
| SPEC-02 | ❌ | ⚠️ | 超时而非失败 |

## 关键改进

1. **HC-01**: 新增 `test_document_payload_bmi_is_overwritten_by_intake_facts` 测试
2. **HC-04**: `test_graduation_report` 现在通过
3. **CT-01**: BMI 字段一致性问题已修复

## 未覆盖项

1. **PW-02, PW-03, PW-07**: 需要特定测试数据
2. **SPEC-05**: 需要安装 Playwright 浏览器
3. **E2E-01**: 用户手动运行，4 个人设失败

## 建议

1. 检查 E2E-01 失败的人设（陈默、许敏、吴退、宋续）
2. 安装 Playwright 浏览器: `cd frontend && npx playwright install`
3. 补充 PW-02, PW-03, PW-07 的测试数据
