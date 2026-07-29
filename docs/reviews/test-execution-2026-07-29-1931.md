# A带定性回应收口测试执行记录

时间：2026-07-29 19:31 CST  
分支：`langgraph-agent-tools`  
最终提交：`166ef0b fix(intake): enforce qualitative A-band copy`

## 1. 测试范围

本次只验证 A 带用户可见正文不复述用户填写的减重数字，也不展示内部安全减重空间数值。

未调用 QiWe、未发送真实群消息、未连接生产数据库、未永久写入业务数据。真实模型仅调用同一匿名 A 带样本两次。

## 2. 实现阶段一：只修提示词冲突

提交：

```text
4ce26d2 fix(intake): keep A-band explanations qualitative
```

改动：

- A 带不再“复述”原始数字目标，只定性概括用户希望尽快看到体重和体态变化；
- A 带管理带解释禁止展示用户原始数字和安全减重空间公斤数；
- B/C/D、四带计算和风险标记不变。

验证：

```bash
cd backend
env UV_CACHE_DIR=/tmp/uv-cache PYTHONPYCACHEPREFIX=/tmp/ran_ai_pycache \
  uv run pytest tests/test_01_intake.py -q
```

结果：`37 passed in 2.13s`。

```bash
cd backend
env UV_CACHE_DIR=/tmp/uv-cache PYTHONPYCACHEPREFIX=/tmp/ran_ai_pycache \
  uv run pytest tests -q
```

结果：`487 passed in 14.97s`。

## 3. 提示词修改后的真实模型双跑

模型：`deepseek-v4-pro`

| 样本 | finish_reason | prompt tokens | completion tokens | total tokens |
|---|---:|---:|---:|---:|
| `a_numeric_run1` | stop | 14,395 | 8,649 | 23,044 |
| `a_numeric_run2` | stop | 14,395 | 5,054 | 19,449 |
| 合计 | 2/2 正常结束 | 28,790 | 13,703 | 42,493 |

提示词单独作用时：

- run1：通过；
- run2：失败，`goal_response.content` 仍偶发复述“减重10斤”；
- 两次 `band_explanation` 均已改为定性表达；
- 两次带位、当前主线和复核标记均稳定一致。

结论：提示词冲突已经消除，但模型仍存在随机越界，不能只靠提示词保证用户可见内容。

## 4. 实现阶段二：最窄确定性收口

提交：

```text
166ef0b fix(intake): enforce qualitative A-band copy
```

只在最终管理带为 `A带｜修复塑形` 时：

- 固定用户可见的管理带解释；
- 固定用户可见的目标直接回应；
- 不做全文正则删除；
- 不改用户原始问卷；
- 不改 `shared_analysis.safe_reduction_room_kg` 和四带计算；
- B/C/D 完全不进入该分支。

新增回归用例模拟模型同时输出“减重10斤”和“安全减重空间不到2公斤”，确认最终用户可见内容被替换为定性表达。

验证：

```bash
cd backend
env UV_CACHE_DIR=/tmp/uv-cache PYTHONPYCACHEPREFIX=/tmp/ran_ai_pycache \
  uv run pytest tests/test_01_intake.py -q
```

结果：`38 passed in 2.15s`。

```bash
cd backend
env UV_CACHE_DIR=/tmp/uv-cache PYTHONPYCACHEPREFIX=/tmp/ran_ai_pycache \
  uv run pytest tests -q
```

结果：`488 passed in 15.03s`。

本地后端已重启：

```text
docker restart ran-ai-backend
curl -sS -f http://127.0.0.1:8000/health
{"status":"ok"}
```

## 5. 最终后处理复验

没有再次调用模型。复用第 3 节的两份真实模型原始输出，使用提交 `166ef0b` 的当前后处理重新生成最终 Markdown。

结果：

| 检查项 | run1 | run2 |
|---|---:|---:|
| 最终带位为 A 带 | 通过 | 通过 |
| 管理带解释不含减重数字/安全空间数字 | 通过 | 通过 |
| 目标直接回应不含用户原始斤数 | 通过 | 通过 |
| 不出现 0-2斤等替代区间 | 通过 | 通过 |
| 当前主线一致 | 节律稳定 | 节律稳定 |
| 复核标记一致 | false | false |

最终用户可见固定口径：

```text
管理带解释：当前更适合修复塑形，不设置体重下降指标，重点观察围度、体态和身体状态变化。

您希望尽快看到体重和体态变化。结合当前状态，这28天不建议设置减重数字目标，
更适合先保持体重基本稳定，把重点放在体态、睡眠、精力、恢复和执行一致性上。
当前不设置减重挑战目标；基础动作稳定完成、睡眠和精力没有变差后，再与班主任评估下一阶段。
```

证据 SHA-256：

```text
responses-a-qualitative-retest.json
8d3ca5e28523fb0fa5b331992cabb75cd625339b6193d3492fbca81a3cf44bfb

evaluation-a-qualitative-retest.json
d98f5a6c81032017643afa8f98306e372fdd6a02c1fa276cc3af5d2dc84f9902
```

## 6. 未覆盖

- 未重复测试已经通过的孕期案例；
- 未重跑前端与 Playwright；
- 沙箱内 LibreOffice 仍无法创建运行时配置，本轮未统计 PDF 页数；
- 未部署生产。

