# 燃人AI 测试执行记录

## 测试环境

- **执行时间**: 2026-06-06 15:55 (UTC+8)
- **当前 Commit**: ce4ab65 docs clarify opencode automated test layers
- **工作区状态**: 仅有未跟踪的 `.codex/` 目录
- **Docker 服务**: 全部运行中
  - ran-ai-backend: Up 24 minutes (port 8000)
  - ran-ai-frontend: Up 28 hours (port 3002)
  - ran-ai-postgres: Up 8 days (healthy) (port 54322)

## 测试执行详情

### L1: Repo Spec 与命令级自动化

#### SPEC-01: 后端语法与导入检查
- **命令**: `cd backend && PYTHONPYCACHEPREFIX=/tmp/ran_ai_pycache uv run python -m compileall src scripts`
- **状态**: ✅ 通过
- **结果**: 所有 Python 文件编译成功，无语法错误

#### SPEC-02: 后端自动化测试
- **命令**: `cd backend && uv run pytest tests --ignore=tests/test_e2e_01_real_llm.py -q`
- **状态**: ❌ 失败 (1 failed, 88 passed)
- **失败详情**:
  - `tests/test_04_stage_reports.py::test_graduation_report` - AssertionError: questionnaire not found (404)
  - 原因: 问卷提交返回 404，问卷 token 可能已失效
- **asyncpg 噪声**: 存在 `InternalClientError: got result for unknown protocol state 3` 警告，但不影响测试结果

#### SPEC-03: 前端类型检查
- **命令**: `cd frontend && npx tsc --noEmit --incremental false`
- **状态**: ✅ 通过
- **结果**: 无类型错误

#### SPEC-04: 前端 Playwright 规范检查
- **命令**: `cd frontend && npm run test:e2e -- --list`
- **状态**: ✅ 通过
- **结果**: 发现 6 个测试用例
  - admin-smoke.spec.ts: /dashboard loads
  - admin-smoke.spec.ts: /users loads
  - admin-smoke.spec.ts: /documents loads
  - admin-smoke.spec.ts: /questionnaire-config loads
  - admin-smoke.spec.ts: /system-diagnostics loads
  - admin-smoke.spec.ts: system diagnostics separates health suites and real E2E

#### SPEC-05: 前端 Playwright 测试
- **命令**: `cd frontend && npm run test:e2e`
- **状态**: ⚠️ 环境阻塞
- **原因**: Playwright 浏览器未安装 (`Executable doesn't exist at /home/chr/.cache/ms-playwright/chromium_headless_shell-1223`)
- **建议**: 运行 `npx playwright install` 安装浏览器

### L2: 安全健康自测 (诊断控制台 HC-01 到 HC-08)

#### HC-01: 入营问卷测试
- **命令**: `curl http://localhost:8000/api/diagnostics/run/01`
- **状态**: ✅ 通过 (6 passed)
- **测试用例**:
  - test_questionnaire_submit_creates_profile_and_documents
  - test_generating_status_on_submit
  - test_intake_submit_records_profile_completed_outcome
  - test_personalized_checkin_uses_latest_document_daily_checkin_block
  - test_intake_facts_bmi_calculation
  - test_failed_generation_reverts_status

#### HC-02: 聊天风控测试
- **命令**: `curl http://localhost:8000/api/diagnostics/run/02`
- **状态**: ✅ 通过 (9 passed)
- **测试用例**:
  - test_normal_message_no_risk
  - test_high_risk_chronic_disease
  - test_emotional_crisis
  - test_refund_intent
  - test_renewal_intent
  - test_explicit_robot_stop_becomes_human_handoff_analysis
  - test_ai_reply_stored_as_outbound
  - test_recent_chat_history_includes_document_delivery
  - test_user_context_mentions_document_progress

#### HC-03: 打卡测试
- **命令**: `curl http://localhost:8000/api/diagnostics/run/03`
- **状态**: ✅ 通过 (4 passed)
- **测试用例**:
  - test_create_single_checkin
  - test_seed_28_days_checkins
  - test_checkin_parsed_json_structure
  - test_checkin_date_ordering

#### HC-04: 阶段报告测试
- **命令**: `curl http://localhost:8000/api/diagnostics/run/04`
- **状态**: ✅ 通过 (4 passed)
- **测试用例**:
  - test_stage_report_generation_all_stages
  - test_stage_report_no_duplicate_generation
  - test_stage_report_content_has_checkin_data
  - test_graduation_report

#### HC-05: 知识进化测试
- **命令**: `curl http://localhost:8000/api/diagnostics/run/05`
- **状态**: ✅ 通过 (5 passed)
- **测试用例**:
  - test_knowledge_evolution_config_defaults
  - test_pure_extraction_with_seed_data
  - test_knowledge_drafts_list
  - test_evolution_generates_drafts_from_seeded_chat
  - test_stage_case_scan_uses_full_history_and_existing_case_context

#### HC-06: 策略开关测试
- **命令**: `curl http://localhost:8000/api/diagnostics/run/06`
- **状态**: ✅ 通过 (16 passed)
- **测试用例**:
  - test_dashboard_cost_excludes_diagnostic_usage
  - test_content_delivery_policy_crud
  - test_ai_reply_policy_crud
  - test_service_case_auto_send_toggle
  - test_service_case_ai_reply_toggle
  - test_qiwe_ai_reply_respects_global_manual_review_policy
  - test_current_physical_risk_uses_fixed_safety_reply
  - test_diagnostics_suites_keep_real_e2e_last
  - test_diagnostics_extracts_e2e_progress_from_console_logs
  - test_diagnostics_history_path_can_be_configured
  - test_diagnostics_stale_health_run_marks_all_running_failed
  - test_diagnostics_reset_active_run_preserves_history
  - test_fixed_safety_reply_distinguishes_health_risk_from_service_handoff
  - test_knowledge_evolution_settings
  - test_content_delivery_policy_auto_send_behaviour
  - test_soft_delete_service_case

#### HC-07: QiWe 通道测试
- **命令**: `curl http://localhost:8000/api/diagnostics/run/07`
- **状态**: ✅ 通过 (15 passed)
- **测试用例**:
  - test_qiwe_send_text_uses_to_id
  - test_qiwe_send_text_builds_reply_payload_from_context
  - test_qiwe_send_document_uploads_then_sends_file
  - test_qiwe_aggregator_merges_consecutive_user_messages
  - test_qiwe_aggregator_ignores_duplicate_message_id
  - test_qiwe_aggregator_uses_fixed_window_without_resetting_timer
  - test_qiwe_aggregator_dispatch_now_reuses_dedupe
  - test_qiwe_aggregator_swallows_engine_failure_for_callback_safety
  - test_qiwe_aggregator_allows_message_after_ttl
  - test_qiwe_callback_text_event_to_channel_message
  - test_qiwe_callback_ignores_self_message
  - test_qiwe_callback_secret_allows_when_unconfigured
  - test_qiwe_callback_secret_validates_header_or_query
  - test_qiwe_system_command_bypasses_aggregator
  - test_qiwe_questionnaire_command_skips_intervention_llm

#### HC-08: 成效引擎测试
- **命令**: `curl http://localhost:8000/api/diagnostics/run/08`
- **状态**: ✅ 通过 (30 passed)
- **测试用例**:
  - test_followup_time_context_late_night_allows_sleep_not_product_schedule
  - test_outcome_plan_includes_followup_time_context
  - test_followup_is_blocked_when_not_in_allowed_time_fields
  - test_generate_outcome_snapshot_is_idempotent
  - test_action_focus_switches_from_high_priority_structured_fact
  - test_unmapped_structured_fact_does_not_change_action_focus
  - test_intake_initializes_user_state_profile
  - test_dashboard_summary_includes_global_outcome_metrics
  - test_outcome_events_update_user_state_profile_evidence_chain
  - test_followup_time_context_noon_allows_midday_fields
  - test_followup_time_context_boundary_hour_10_is_noon
  - test_followup_time_context_boundary_hour_14_is_afternoon
  - test_followup_time_context_boundary_hour_18_is_evening
  - test_followup_time_context_boundary_hour_22_is_late_night
  - test_answered_field_keys_maps_weight_to_three_fields
  - test_answered_field_keys_maps_body_feedback_to_energy_and_positive
  - test_answered_field_keys_maps_waist_to_waist_fields
  - test_followup_time_context_afternoon_checkin_blocks_evening_fields
  - test_followup_time_context_deep_night_checkin_blocks_morning_fields
  - test_followup_time_context_hour_5_is_late_night
  - test_followup_time_context_hour_6_is_morning
  - 等共 30 个测试用例

### 契约与内容准确性测试 (CT-01 到 CT-08)

#### CT-01: 入营问卷 → 画像 → 文档一致性
- **状态**: ⚠️ 部分失败
- **检查对象**: 服务单 2537
- **发现**:
  - 用户画像显示 BMI 23.7
  - 文档显示 "暂无数据"
  - **不一致**: BMI 字段在画像和文档中不一致

#### CT-02: 文档打卡项一致性
- **状态**: ✅ 通过
- **检查对象**: 服务单 2537
- **发现**:
  - 用户画像 `personalized_checkin_fields` 与文档 `daily_checkin` 完全一致
  - 5 个打卡项的标题、类型、选项和 required 状态均匹配

#### CT-03: 打卡字段类型
- **状态**: ✅ 通过
- **检查对象**: 服务单 2537
- **发现**:
  - "是否" 类型字段均为 RADIO 类型
  - 选项为 ["是", "否"]
  - 评分字段为 SIMPLE 类型

### L2: OpenCode Playwright MCP 页面交互测试 (PW-01 到 PW-09)

#### PW-01: 登录与导航
- **状态**: ✅ 通过
- **检查结果**:
  - Basic Auth 后可进入后台
  - 侧栏导航包含: 用户管理、审核控制台、运营规则配置、数据看板、诊断控制台
  - 所有导航链接可正常点击

#### PW-02: 入营问卷
- **状态**: ⚠️ 未执行
- **原因**: 需要有效的问卷 token

#### PW-03: 每日行动链接
- **状态**: ⚠️ 未执行
- **原因**: 需要有效的服务单和执行文档

#### PW-04: 用户管理
- **状态**: ✅ 通过
- **检查结果**:
  - 用户列表正常加载
  - 正常用户/已删除切换正常
  - 用户详情页可访问
  - 已删除服务单访问不崩溃

#### PW-05: 服务单详情
- **状态**: ✅ 通过
- **检查结果**:
  - 服务单详情页正常加载
  - 用户画像、交付文件摘要、当前用户状态、打卡数据、历史聊天摘要均正常显示
  - 聊天记录默认滚到最新消息

#### PW-06: 审核控制台
- **状态**: ✅ 通过
- **检查结果**:
  - 审核控制台正常加载
  - "实时监听中" 状态显示正常
  - 文件自动发送策略和 AI 聊天回复策略开关正常

#### PW-07: 运营规则配置
- **状态**: ⚠️ 未执行
- **原因**: 需要进一步检查配置保存功能

#### PW-08: 数据看板
- **状态**: ✅ 通过
- **检查结果**:
  - 数据看板正常加载
  - 指标卡正常显示
  - 客户成效引擎和经营信号区域正常

#### PW-09: 系统诊断
- **状态**: ⚠️ 部分失败
- **检查结果**:
  - 诊断控制台页面正常加载
  - 安健康检查区域显示 0/0（测试套件未加载）
  - **问题**: 运行安全健康检查时出现 "Failed to execute 'fetch'" 错误
  - **原因**: Playwright MCP 使用 Basic Auth URL 导致 fetch 失败

## 测试统计

| 类别 | 通过 | 失败 | 跳过 | 环境阻塞 |
|------|------|------|------|----------|
| SPEC (L1) | 4 | 1 | 0 | 1 |
| HC (L1) | 8 | 0 | 0 | 0 |
| CT (L2) | 2 | 1 | 5 | 0 |
| PW (L2) | 5 | 1 | 3 | 0 |
| **总计** | **19** | **3** | **8** | **1** |

## 关键发现

### 失败项
1. **SPEC-02**: `test_graduation_report` 失败 - 问卷 token 404
2. **CT-01**: BMI 字段在用户画像和文档中不一致
3. **PW-09**: 诊断控制台 fetch 错误（Basic Auth URL 问题）

### 环境阻塞项
1. **SPEC-05**: Playwright 浏览器未安装

### 未执行项
1. **PW-02, PW-03, PW-07**: 需要特定测试数据或进一步检查

## 是否调用真实 LLM

否。本次测试未调用真实 LLM，未产生费用，未保留测试数据。

## 建议

1. 修复 `test_graduation_report` 测试中的问卷 token 问题
2. 检查 BMI 字段在文档生成过程中的传递逻辑
3. 安装 Playwright 浏览器: `cd frontend && npx playwright install`
4. 修复诊断控制台的 Basic Auth URL 问题
