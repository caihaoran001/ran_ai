# 打卡简化改动审查报告

**审查日期**：2026-06-03
**审查范围**：打卡问卷模板简化、预填逻辑、系统计算
**审查结论**：核心改动正确，有 5 个优化点需要处理

---

## ✅ 已完成的改动（正确）

| 改动 | 状态 | 说明 |
|------|------|------|
| 去掉 6 个补字段 | ✅ | body_feeling_other、tomorrow_focus_other、diet_issue、3个 time_or_reason |
| day_no 改系统算 | ✅ | 从 service_case.started_at 自动计算 |
| 预填上次体重/腰围 | ✅ | 从最新 checkin 记录取 |
| 阶段复盘历史数据改系统算 | ✅ | initial_weight/initial_waist 从第一条 checkin 取，产品完成天数从最近7天统计 |
| 提交流程不变 | ✅ | questionnaires.py 的打卡逻辑保留 |
| 保留有下游消费者的字段 | ✅ | diet_risk、appetite、today_feedback 等保留 |

---

## ⚠️ 需要优化的点

### 1. 前端显示 day_no（优先级：高）

**问题**：当前 GET 响应已经返回了 `day_no`，但前端还没有用它。用户看不到"今天是第X天"的信息。

**改动位置**：`frontend/src/app/q/[token]/questionnaire-form.tsx`

**改动内容**：
- 从 `default_values.day_no` 取值
- 在表单上方显示"今天是第 {day_no} 天"

**示例**：
```tsx
{default_values.day_no && (
  <div className="text-sm text-zinc-500 mb-4">
    今天是第 {default_values.day_no} 天
  </div>
)}
```

---

### 2. 前端展示阶段复盘的参考信息（优先级：高）

**问题**：阶段复盘的 `initial_weight`、`initial_waist`、`morning_c_days`、`noon_b_days`、`night_a_days` 已从模板中删除，但这些值仍然在 `default_values` 里返回。前端需要以只读信息的方式展示，让用户看到对比基准。

**改动位置**：`frontend/src/app/q/[token]/questionnaire-form.tsx`

**改动内容**：
- 阶段复盘表单上方显示参考信息
- 只读展示，不可编辑

**示例**：
```tsx
{template === "stage_review" && (
  <div className="bg-zinc-50 rounded-lg p-4 mb-6 space-y-2">
    <h3 className="text-sm font-medium text-zinc-700">参考信息</h3>
    {default_values.initial_weight && (
      <p className="text-sm text-zinc-600">
        初始体重：{default_values.initial_weight}kg
        {default_values.current_weight && ` → 当前体重：${default_values.current_weight}kg`}
      </p>
    )}
    {default_values.initial_waist && (
      <p className="text-sm text-zinc-600">
        初始腰围：{default_values.initial_waist}cm
        {default_values.current_waist && ` → 当前腰围：${default_values.current_waist}cm`}
      </p>
    )}
    {(default_values.morning_c_days || default_values.noon_b_days || default_values.night_a_days) && (
      <p className="text-sm text-zinc-600">
        过去7天产品完成：早C {default_values.morning_c_days || "0/7天"}，午B {default_values.noon_b_days || "0/7天"}，晚A {default_values.night_a_days || "0/7天"}
      </p>
    )}
  </div>
)}
```

---

### 3. 阶段复盘的 initial_weight/initial_waist 优先级（优先级：中）

**问题**：当前从第一条 checkin 取 `initial_weight`/`initial_waist`，但入营问卷里也有初始体重/腰围。应该优先从入营问卷取，如果没有再从第一条 checkin 取。

**改动位置**：`backend/src/app/api/routes/questionnaires.py` 的 `_build_checkin_default_values`

**改动内容**：
```python
if template == "stage_review":
    # 先从入营问卷取
    intake_submission = await session.scalar(
        select(QuestionnaireSubmission)
        .where(QuestionnaireSubmission.service_case_id == service_case.id)
        .order_by(QuestionnaireSubmission.created_at.asc())
        .limit(1)
    )
    if intake_submission and isinstance(intake_submission.answers_json, dict):
        fw = intake_submission.answers_json.get("weight") or intake_submission.answers_json.get("current_weight")
        if fw:
            defaults["initial_weight"] = str(fw)
        fwaist = intake_submission.answers_json.get("waist") or intake_submission.answers_json.get("current_waist")
        if fwaist:
            defaults["initial_waist"] = str(fwaist)

    # 如果入营问卷没有，再从第一条 checkin 取
    if not defaults.get("initial_weight"):
        first_checkin = await session.scalar(
            select(Checkin)
            .where(Checkin.service_case_id == service_case.id)
            .order_by(Checkin.checkin_date.asc())
            .limit(1)
        )
        if first_checkin and isinstance(first_checkin.parsed_json, dict):
            fw = first_checkin.parsed_json.get("weight")
            if fw:
                defaults["initial_weight"] = str(fw)
            fwaist = first_checkin.parsed_json.get("waist")
            if fwaist:
                defaults["initial_waist"] = str(fwaist)
```

---

### 4. 预填逻辑的兜底（优先级：中）

**问题**：如果用户没有历史 checkin 记录，预填数据为空。可以从入营问卷取兜底数据。

**改动位置**：`backend/src/app/api/routes/questionnaires.py` 的 `_build_checkin_default_values`

**改动内容**：
```python
# 先从最新 checkin 取
latest_checkin = await session.scalar(
    select(Checkin)
    .where(Checkin.service_case_id == service_case.id)
    .order_by(Checkin.checkin_date.desc())
    .limit(1)
)
if latest_checkin:
    pj = latest_checkin.parsed_json
    w = pj.get("weight")
    if w:
        defaults["morning_weight"] = str(w)
    waist = pj.get("waist")
    if waist:
        defaults["waist"] = str(waist)

# 如果没有，从入营问卷取兜底
if not defaults.get("morning_weight"):
    intake_submission = await session.scalar(
        select(QuestionnaireSubmission)
        .where(QuestionnaireSubmission.service_case_id == service_case.id)
        .order_by(QuestionnaireSubmission.created_at.asc())
        .limit(1)
    )
    if intake_submission and isinstance(intake_submission.answers_json, dict):
        w = intake_submission.answers_json.get("weight") or intake_submission.answers_json.get("current_weight")
        if w:
            defaults["morning_weight"] = str(w)
        waist = intake_submission.answers_json.get("waist") or intake_submission.answers_json.get("current_waist")
        if waist and not defaults.get("waist"):
            defaults["waist"] = str(waist)
```

---

### 5. 合并 DB 查询（优先级：低）

**问题**：`_build_checkin_default_values` 对于阶段复盘模板，一次 GET 请求会执行 3 次独立查询：
1. 查最新 checkin（取体重/腰围）
2. 查第一条 checkin（取初始体重/腰围）
3. 查最近7天 checkin（统计产品完成天数）

**当前阶段不是瓶颈**，但如果并发量大可以考虑合并。

**改动位置**：`backend/src/app/api/routes/questionnaires.py` 的 `_build_checkin_default_values`

**改动内容**：
```python
# 一次查询取所有 checkin 记录
all_checkins = list(
    await session.scalars(
        select(Checkin)
        .where(Checkin.service_case_id == service_case.id)
        .order_by(Checkin.checkin_date.desc())
    )
)

# 从所有记录里取需要的数据
latest_checkin = all_checkins[0] if all_checkins else None
first_checkin = all_checkins[-1] if all_checkins else None
seven_days_ago = now.date() - timedelta(days=7)
recent_checkins = [ci for ci in all_checkins if ci.checkin_date >= seven_days_ago]
```

---

## 总结

| # | 问题 | 优先级 | 改动量 | 位置 |
|---|------|--------|--------|------|
| 1 | 前端显示 day_no | 高 | 5 行 | frontend/src/app/q/[token]/questionnaire-form.tsx |
| 2 | 前端展示阶段复盘参考信息 | 高 | 20 行 | frontend/src/app/q/[token]/questionnaire-form.tsx |
| 3 | 阶段复盘 initial_weight/initial_waist 优先级 | 中 | 15 行 | backend/src/app/api/routes/questionnaires.py |
| 4 | 预填逻辑的兜底 | 中 | 15 行 | backend/src/app/api/routes/questionnaires.py |
| 5 | 合并 DB 查询 | 低 | 10 行 | backend/src/app/api/routes/questionnaires.py |

**建议执行顺序**：
1. 先做 #1 和 #2（前端改动，用户直接感知）
2. 再做 #3 和 #4（后端改动，数据准确性）
3. 最后做 #5（性能优化，当前阶段不是瓶颈）
