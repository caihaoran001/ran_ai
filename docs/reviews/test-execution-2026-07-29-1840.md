# 真实模型定向复测执行记录（充值后）

时间：2026-07-29 18:40 CST  
分支：`langgraph-agent-tools`  
当前提交：`cb54ee5 fix(intake): derive management band from safe room`

## 1. 范围

本次是客户充值后的最小定向复测，不是全项目完整测试。只覆盖上次受模型服务状态影响或仍有争议的 3 次真实模型调用：

1. 同一份 A 带问卷重复生成两次；
2. 一份孕期高风险问卷；
3. 复跑入营聚焦 Mock 回归。

未调用 QiWe、未向真实群发送消息、未连接生产数据库、未永久写入业务数据。真实模型调用会产生费用，具体金额以供应商账单为准。

## 2. 环境与工作区

```text
git log -1 --oneline
cb54ee5 fix(intake): derive management band from safe room

docker compose ps
ran-ai-backend   Up
ran-ai-frontend  Up
ran-ai-postgres  Up (healthy)
```

执行前已有且未改动的工作区内容：

```text
 M AGENTS.md
?? .impeccable/config.json
?? .impeccable/critique/2026-07-16T16-20-36Z__frontend-src-app-users-id-page-tsx.md
?? 客户调研/
?? 文字记录/
?? 第三次参考文件/
```

## 3. 实际执行

### 3.1 重建当前提交的匿名提示词

```bash
cd backend
env UV_CACHE_DIR=/tmp/uv-cache \
  PYTHONPATH=src \
  PYTHONPYCACHEPREFIX=/tmp/ran_ai_pycache \
  uv run python /tmp/real-intake-20260729/build_prompts.py
```

状态：通过。确定性预期为：

- A 重复样本：BMI 20.6，安全空间 1.55kg，A带｜修复塑形；
- 孕期样本：BMI 29.1，安全空间 21.95kg，D带｜系统减重。

### 3.2 真实 DeepSeek 调用

```bash
cd backend
env UV_CACHE_DIR=/tmp/uv-cache \
  PYTHONPATH=src \
  PYTHONPYCACHEPREFIX=/tmp/ran_ai_pycache \
  uv run python /tmp/real-intake-20260729/call_model.py
```

模型：`deepseek-v4-pro`

| 样本 | finish_reason | prompt tokens | completion tokens | total tokens |
|---|---:|---:|---:|---:|
| `a_numeric_run1` | stop | 14,174 | 6,155 | 20,329 |
| `a_numeric_run2` | stop | 14,174 | 4,643 | 18,817 |
| `pregnancy_high_risk` | stop | 13,992 | 6,108 | 20,100 |
| 合计 | 3/3 正常结束 | 42,340 | 16,906 | 59,246 |

状态：通过。没有出现余额不足、鉴权、HTTP 或截断错误；三份内容都能解析为完整 package JSON。

### 3.3 归一化、确定性后处理与内容检查

```bash
cd backend
env UV_CACHE_DIR=/tmp/uv-cache \
  PYTHONPATH=src \
  PYTHONPYCACHEPREFIX=/tmp/ran_ai_pycache \
  uv run python /tmp/real-intake-20260729/evaluate.py
```

| 检查项 | 结果 | 证据 |
|---|---|---|
| A 带确定性判带 | 通过 | 两次均为 `A带｜修复塑形` |
| A 带系统目标不设置减重数字 | 通过 | 两次 `base_goal/day28_goal` 均为体重稳定和修复目标，`challenge_goal` 均为“不设置减重挑战目标” |
| 不出现“建议减重0-2斤” | 通过 | 两次均未出现 0-2斤、1-2斤等替代建议 |
| A 带用户可见文本完全不出现减重数字 | 失败（严格口径） | 两次都复述了用户填写的“减10斤”；run1 还解释“安全减重空间只有不到2公斤” |
| A 重复样本带位稳定 | 通过 | A → A |
| A 重复样本复核标记稳定 | 通过 | false → false |
| A 重复样本当前主线逐字稳定 | 失败 | run1 为“睡眠修复”，run2 为“节律稳定”；两者均由同一睡眠/晚间节律证据支持，且各自两份文件内部一致 |
| 孕期照常判 A-D | 通过 | 最终 `D带｜系统减重`，没有清空带位或强制改 A |
| 孕期重点人工复核 | 通过 | `manual_review_required=true`，带有孕期复核原因 |
| 孕期不自动给减重数字 | 通过 | 基础目标为稳定体重，挑战目标为“不设置减重挑战目标” |
| 两份文件共享结论 | 通过 | 管理带、28天目标、当前主线均由 `shared_analysis` 后处理统一 |
| 本次 PDF 页数复验 | 环境阻塞 | 沙箱内 LibreOffice 无法创建 `/run/user/1000/dconf/user`；Markdown/DOCX 已正常生成 |

A 带的失败是两个不同口径之间的区别：

- “系统不建议数字减重目标”：已经满足；
- “A 带任何用户可见文字都不得出现减重数字”：仍未满足。

实际文案是在拒绝用户的 10 斤目标，不是在建议 10 斤；但 run1 的“安全减重空间不到2公斤”仍属于用户可见的数字区间。

### 3.4 入营聚焦 Mock 回归

```bash
cd backend
env UV_CACHE_DIR=/tmp/uv-cache \
  PYTHONPYCACHEPREFIX=/tmp/ran_ai_pycache \
  uv run pytest tests/test_01_intake.py -q
```

状态：通过，`37 passed in 2.08s`。

## 4. 证据文件

临时证据位于 `/tmp/real-intake-20260729/`：

- `responses-retest-after-recharge.json`
- `evaluation-retest-after-recharge.json`
- `documents/a_numeric_run1_*.md`
- `documents/a_numeric_run2_*.md`
- `documents/pregnancy_high_risk_*.md`

SHA-256：

```text
prompts.json
3b9d3d336b9302c0d71d79fdc93bd017269957171e543a7057472076af96cdd0

responses-retest-after-recharge.json
96578765ccefeb7f1e11c67162c1d8fd6ee5ce3837ea86041e271efa8aeee588

evaluation-retest-after-recharge.json
018c9f997fed3111585e26c6a1f13aceacb3690393c96720e01c7b28f4f22c42
```

## 5. 未覆盖

- 未重跑后端全量、前端类型检查和 Playwright；当前提交未变化，本轮只验证充值后真实模型输出。
- 未测试真实服务单入库、生产审核页和真实文件发送。
- 未向真实消息通道发送任何内容。
- 未做本轮 PDF 页数统计。

