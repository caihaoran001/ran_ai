# 交付文档事实与一致性修复：测试执行记录

- 执行时间：2026-08-03 19:45（Asia/Shanghai）
- 代码提交：`f0c5902 fix(documents): enforce visible intake facts`
- 执行方式：repo-spec + real-e2e（已获用户明确授权）+ ECS deployment verification

## 范围

本次验证三项用户确认的交付规则：

1. 未填写的特殊生理阶段不得仅凭年龄推测。
2. 两份文件中重复出现的蔬菜总量、上床时间不得相互冲突。
3. 腰围缺失且体脂率已填写时，必须分别、带单位展示。

血糖、胰岛素、瘦素等机制化解释按用户确认的当前口径允许出现；但不得写成疾病诊断、用药指令或疗效保证。

## 环境与工作区

- 分支：`main`
- 部署提交：`f0c5902`
- 工作区保留了用户既有的未跟踪资料与 `.impeccable/` 文件，未修改或纳入提交。

## 执行结果

| 项目 | 命令 / 方式 | 状态 | 关键证据 |
| --- | --- | --- | --- |
| 定向入营回归 | `cd backend && env UV_CACHE_DIR=/tmp/uv-cache PYTHONPYCACHEPREFIX=/tmp/ran_ai_pycache uv run pytest tests/test_01_intake.py -q` | 通过 | 74 passed in 2.73s |
| 后端编译 | `cd backend && ... uv run python -m compileall src scripts` | 通过 | `src` 与 `scripts` 编译完成 |
| 全量后端回归 | `cd backend && ... uv run pytest tests -q` | 通过 | 547 passed in 16.15s |
| 前端生产构建 | `cd frontend && npm run build` | 通过 | Next.js production build 完成 |
| 真实模型全文生成 | 6 份本机只读问卷，最多并发 3 | 通过 | 6/6 最终文件通过复核隔离、目标一致及新增三项规则；含 2 份运营复核样本 |
| 真实模型重试 | 同上 | 通过 | 1 份样本首稿触发一致性拦截后自动重生成，最终腰围/体脂率显示正确 |
| 生产部署 | `scratch/local_prebuilt_deploy.py` | 通过 | ECS backend/frontend 均已重建为新容器 |
| 容器内运行时 | ECS `get_settings()` + Python health probe | 通过 | 模型超时 `300.0`；健康 HTTP 200 |
| 公网健康 | `curl -sS -f https://admin.humanburning.ai/health` | 通过 | `{"status":"ok"}` |

## 真实模型审计细节

真实生成不创建服务单、不保存交付文件、不发送消息；结果仅保留在本机 `/tmp` 临时文件。调用会产生模型费用与本机诊断用量记录。

- 6 份最终文档均未出现无来源的围绝经/更年期/绝经判断。
- 未发现“每餐 X 拳蔬菜”的下发口径。
- 未发现同一文件集合内两个不同的上床目标时间。
- 腰围缺失、体脂率为 30 的样本最终显示为“腰围未提供 / 体脂率30%”。
- 其中一份样本首稿被拦截后进行了第 2 次文档生成；这是新增保护的预期行为，不是下发失败。

## 中间失败与处理

首次加入上床时间校验后，旧 Mock 文档示例本身同时写了 `23:00 前关灯` 和 `23:30 前上床`，被新校验正确拒绝。该示例已统一为同一时间后重跑，定向和全量回归均通过。没有放宽生产校验。

