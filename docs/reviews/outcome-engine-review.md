# Outcome Engine 代码审查报告

- **审查日期**: 2026-06-01
- **审查范围**: 用户新增的 `outcome_engine` / `user_state_profile` 模块及其周边文件
- **审查方式**: 静态阅读 + 实测（5 项动态验证）+ 3 个并行 explore agent
- **审查结论**: 整体可用，发现 22 个问题，2 个 P0 / 5 个 P1 / 7 个 P2 / 8 个 P3

---

## ✅ 已被证伪 / 无问题（澄清）

| 之前的猜测 | 验证结论 | 证据 |
|---|---|---|
| 3 秒 callback 窗口 | **实际不构成 P0** | `engine.handle_message` 在 BackgroundTasks 里跑（qiwe.py:50,52 → aggregator → engine），HTTP 200 在 callback 同步段就回 |
| main.py 有 outcome 启动 bug | **干净** | main.py:138 行只有 ReminderService + Aggregator，无 outcome 特定逻辑 |
| intervention_service 与 outcome 冲突 | **不冲突** | 只操作 ServiceCase，不碰新表；且 outcome 比它更保守（会因 refund/emotion 抑制，content_policy 不会） |
| service_case_queries.py 有 N+1 | **干净** | 4 个函数都是单次查询 |
| content_policy 与 outcome 冲突 | **不冲突** | outcome 是 content_policy 的超集（更保守），互不干扰 |
| 前端 outcome-section.tsx 有 N+1 | **无 N+1** | 294 行内所有循环都是常量上限（8 事件、5 字段、7 阶段） |
| 事务半写风险 | **无风险** | 5 步写入共享同一 `SessionLocal()` 事务，异常时 `async with` 触发 rollback |
| followup_count 逻辑错 | **正确** | 三重保护（风险 → 冷却 → 递增），顺序正确 |
| 空 metrics 模板崩溃 | **安全** | 全部 `.get(..., 0)` 有默认值 |

---

## 🔴 P0：迁移层索引问题（2 个）

### P0-1: 5 个冗余索引（浪费写入和磁盘）

| # | 表 | 冗余索引 | 应保留的 | 原因 |
|---|---|---|---|---|
| 1 | outcome_events | `ix_outcome_events_field` | `ix_outcome_events_field_key` | **完全重复**，两条索引都在 `field_key` 单列（migration 0019 line 40 + line 41） |
| 2 | outcome_events | `ix_outcome_events_service_case_id` | `ix_outcome_events_case_milestone` | 复合 `(service_case_id, milestone)` 前缀已覆盖单列查询 |
| 3 | outcome_stage_states | `ix_outcome_stage_states_service_case_id` | `ux_outcome_stage_state_case_milestone` | 唯一约束 `(service_case_id, milestone)` 前缀覆盖 |
| 4 | outcome_snapshots | `ix_outcome_snapshots_service_case_id` | `ux_outcome_snapshot_case_milestone` | 同上 |
| 5 | user_state_profiles | `ix_user_state_profiles_service_case_id` | `ux_user_state_profiles_service_case_id` | **完全重复**（唯一约束自动建同列索引） |

### P0-2: 缺 1 个高频热路径索引

**查询位置**: `outcome_engine.py:674-677` `_event_exists` —— **每条消息都会执行的去重检查**

```python
WHERE service_case_id = ? AND field_key = ? AND evidence_text = ? AND message_id = ?
```

**当前**: 只能用 `ix_outcome_events_service_case_id` 过滤前缀，然后做字段比较 + LIMIT 1。
**应有**: `CREATE INDEX ix_outcome_events_case_field_evidence ON outcome_events(service_case_id, field_key, evidence_text)` —— 让 dedup 查询走 index 直接命中。

---

## 🟠 P1: 架构与可靠性（5 个）

### P1-1: handle_message 失败 = 消息丢失

- **位置**: `aggregator.py:141` `await self.engine.handle_message(_merge_messages(batch.messages))`
- **风险**: handle_message 抛异常（DB 断 / LLM 5xx / JSON 解析失败）→ batch 已弹出 → 200 已回 → QiWe 不重发 → **用户消息永远丢失**
- **当前覆盖**: ❌ 没有任何 try/except 兜底
- **修复**: 包裹 `try/except Exception: logger.exception(...)`（最简方案 5 行）

### P1-2: 聚合窗口边界竞态

- **位置**: `aggregator.py:125-141` `flush()` 到 `engine.handle_message` 之间的窗口
- **机制**:
  1. `_flush_later` 醒了，弹 batch（line 128）→ 释放 lock
  2. 此时新消息到达 `enqueue()` → 创建新 batch + 新 task
  3. 两次 `handle_message` 并发执行，**共享同一个 `OutcomeStageState` 行**
  4. PostgreSQL READ COMMITTED 隔离下，两者都读到 `followup_count=0`，都写入 `followup_count=1` → **后提交覆盖前提交**
- **影响**: 极低概率（毫秒级窗口），业务影响小（最多少收集一次成效证据）
- **修复建议**: 把 lock 保持到 handle_message 完成，或在 score_action_focus 用 `SELECT ... FOR UPDATE` 锁 stage_state

### P1-3: 4 个新表查询函数没有 `deleted_at` 防御

- **位置**:
  - `outcome_engine.py:222` `get_or_create_stage_state`
  - `user_state_profile.py:211` `get_or_create_user_state_profile`
  - `outcome_engine.py:668` `_event_exists`

- **当前**: 所有路径都"信任调用方"已过滤 `deleted_at`。**当前调用链无泄漏**（已验证），但**新调用方很容易绕过守卫**。
- **修复建议**: 在 `get_or_create_*` 函数中加 service_case 活跃检查（或至少加注释 + 类型提示）

### P1-4: ServiceCase 模型缺 4 个新表的 relationship

- **位置**: `models/service_case.py:70-94` 定义了 6 个 relationship（questionnaire_submissions、user_profiles 等），**4 个新表不在其中**
- **影响**:
  - 硬删除 service_case（虽项目规则禁止）会触发 FK 违规
  - 软删除后无 ORM 级联清理路径
  - 无便捷反向查询（`service_case.outcome_events` 不可用）
- **修复**: 补 4 个 relationship 定义

### P1-5: 前端 page.tsx 串行请求瀑布

- **位置**: `frontend/src/app/cases/[id]/page.tsx:29-36`
- **当前**: 8 个 `await api()` 串行执行
- **问题**: 总延迟 = 各请求延迟之和
- **建议**: 除 `serviceCase` 外的 6 个并行（`Promise.all`），预计节省 200-500ms

---

## 🟡 P2: 代码质量与边界（7 个）

### P2-1: apply_outcome_events_to_user_state 和 score_action_focus 双重 flush

- **位置**: `user_state_profile.py:207` 和 `outcome_engine.py:559` 都对同一 `state_profile.state_json` 做 flush
- **问题**: 第一次 flush 写部分更新（body_feedback），第二次 flush 写完整（body_feedback + action_focus）。**第一次 flush 是浪费的 UPDATE**
- **修复**: 移除 `user_state_profile.py:207` 的 flush，统一在 `apply_outcome_analysis` 末尾 flush

### P2-2: `_event_exists` 缺唯一约束

- **位置**: `outcome_engine.py:674-682`
- **问题**: SELECT-then-INSERT 模式在并发下不可靠（虽然在当前 aggregator 8 秒窗口下不会触发）
- **修复**: 在 `outcome_events` 加 `UniqueConstraint("service_case_id", "field_key", "evidence_text", "message_id", name="ux_outcome_events_dedup")` —— 数据库层兜底

### P2-3: UserStateProfile.state_json 整段 JSONB 写放大

- **位置**: `user_state_profile.py:203-204` 每次 outcome 都重写整段
- **实测**: 50 条消息后 ~30-100KB。**高活跃用户**（每天 50 条 × 28 天 = 1400 条）累积可达 MB 级
- **修复**（长期）: 把高频小更新（action focus 评分、history 追加）拆到独立列

### P2-4: Dashboard day 边界与 service_day 边界不一致

- **位置**:
  - `dashboard.py:_eligible_case_count`: `started_at <= now - timedelta(days=day - 1)`（连续 24h × N）
  - `outcome_engine.py:service_day`: `delta.days + 1`（自然天数差）
- **风险**: 6.99 天的服务单在两处可能分别算 day6/day7
- **修复**: 统一用 `started_at.astimezone(tz).date()` 算自然日数

### P2-5: Mock LLM field_key fallback 不稳定

- **位置**: `tests/conftest.py:258-271`
- **问题**: 二次调用时 `energy_change` 已收，mock fallback 到 `missing[0]` → field_key 漂移
- **影响**: 暴露了"同 message 二次 outcome 重复插行"现象（虽然根因是 mock，不是 engine）
- **修复**: 把 mock 的 field_key 锁死，不依赖 missing

### P2-6: intervention_service.risk_level 永不降级

- **位置**: `intervention_service.py:merge_case_insight` 只追加 risk_flags，不重置 risk_level
- **影响**: 一旦触发 high risk，永远 high → outcome `_must_suppress_followup` 永远 True
- **判断**: 是 design choice（安全保守），但需确认。如果想"风险解除"，需要补降级逻辑

### P2-7: content_policy 不处理 refund/emotion 信号

- **位置**: `engine.py:_should_send_ai_reply` 不检查 `refund_intent` 和 `emotion_signal`
- **判断**: 当前 outcome 抑制 followup 但不影响 AI reply。如果期望"退款意向时也停止 AI 自动回复"，需补充

---

## 🟢 P3: 小问题（8 个）

| # | 位置 | 问题 |
|---|---|---|
| 3.1 | `engine.py:146-147` | `if hasattr(session, "flush")` 死代码（AsyncSession 必有 flush） |
| 3.2 | `dashboard.py:_build_outcome_summary` | 11+ SQL，可合并到 1-2 个 CTE |
| 3.3 | `dashboard.py:_day28_change_averages` | Python 内存 `mean()`，可下推 SQL `AVG()` |
| 3.4 | `tests/test_10_outcome_engine.py:163` | 断言 "冷却期" in reason 耦合了文案 |
| 3.5 | `engine.py:131` | `purpose = MessagePurpose.CHAT` 硬编码 |
| 3.6 | `outcome_engine.py:914-916` | `_string`/`_confidence` helper 可内联 |
| 3.7 | `user_state_profile.py:_append_history` | marker 用 `(field_key, evidence, message_id)`，None 时漏去重 |
| 3.8 | `outcome_engine.py:_is_non_empty_block` | 操作符优先级可读性差 |

---

## 📊 汇总

| 等级 | 数量 | 建议处理 |
|---|---|---|
| 🔴 P0 | 2 | **必做**（1 个迁移修索引 + 1 个业务层 dedup 索引） |
| 🟠 P1 | 5 | **建议**（1 个 5 行 try/except + 1 个 race 锁 + 1 个防御 + 1 个 relationship + 1 个前端并行） |
| 🟡 P2 | 7 | **可选**（大多是设计决策和长期优化） |
| 🟢 P3 | 8 | 散点清理 |

---

## 🛠 最高 ROI 修复路径（按用户"极简"原则）

1. **P0 索引修复**: 1 个新 migration（删 5 加 1），纯数据库层
2. **P1-1 try/except**: 5 行
3. **P1-5 前端 Promise.all**: 3 行
4. **P1-2 加锁 / P1-3 防御**: 各 5-10 行

---

## 关键文件清单

- `backend/src/app/api/routes/qiwe.py` — callback 路由（3 秒返回，outcome 在 BackgroundTasks）
- `backend/src/app/services/qiwe_service/aggregator.py` — 8 秒防抖 + TTL 去重 + `aggregator.flush:141` 调 `engine.handle_message` 无 try/except
- `backend/src/app/services/ranren_ai/engine.py` — `handle_message` 串行 LLM + `_reply_with_ai` 读 `analysis["outcome"]` 注入 followup
- `backend/src/app/services/outcome_engine.py` (923 行) — 主体逻辑
- `backend/src/app/services/user_state_profile.py` (389 行) — 状态投影 + `OUTCOME_FIELD_DOMAIN_MAP`
- `backend/src/app/models/outcome.py` — `OutcomeEvent` / `OutcomeSnapshot` / `OutcomeStageState`
- `backend/src/app/models/user_state_profile.py` — `UserStateProfile`
- `backend/src/app/api/routes/outcomes.py` — 只读 `GET /service-cases/{id}/outcomes`
- `backend/src/app/api/routes/dashboard.py` — `_build_outcome_summary` 11+ SQL
- `backend/alembic/versions/20260529_0019_outcome_engine.py` — outcome 表 migration
- `backend/alembic/versions/20260601_0020_user_state_profile.py` — state profile migration
- `backend/tests/test_10_outcome_engine.py` — 9 passed
- `backend/tests/conftest.py` — mock LLM field_key fallback
- `frontend/src/app/cases/[id]/outcome-section.tsx` (294 行) — 前端组件
- `frontend/src/app/cases/[id]/page.tsx` — 8 个串行 API 调用
