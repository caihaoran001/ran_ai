# RAG 与 Agent 评测

评测只在 ECS 上连接生产 RDS，进程启动后同时启用 PostgreSQL 只读会话和 SQL 写入拦截；意图模型原有的用量日志写入会改为进程内收集。不会调用消息渠道，也不会创建提醒或修改服务单。

正式题库固定为 `tests/fixtures/rag_eval_cases.v2.json`：25 条依据生产 FAQ 改写，25 条来自一次性筛选并脱敏的生产聊天。v2 为两道省略问法补齐了能确定所指对象的前文；每次运行只读取这个文件，不会重新抽 FAQ、聊天或自动替换题目。

## 首次安装或依赖版本变化

```bash
docker compose -f docker-compose.eval.yml build rag-eval
docker compose -f docker-compose.eval.yml up --no-deps rag-eval
```

`ragas==0.4.3` 已固化在 `ran-ai-rag-eval:local` 镜像中。评测结束后容器是 `Exited`，不会常驻，但镜像和依赖仍在。

RAGAS 判官的最大输出默认是 50,000 tokens，由 `RAG_EVAL_JUDGE_MAX_TOKENS` 控制。它只放宽离线判官生成结构化评分的上限，不改变线上回复长度，也不会固定消耗 50,000 tokens；计费仍以实际生成量为准。

## 回复相关性中文校准

Answer Relevancy 不再使用未经中文校准的固定 0.80 门槛。v3 固定 30 组中文正负对照：前 20 组只用于选门槛，后 10 组锁定验证，全部独立判分两轮。每轮正确回答识别率和错误回答拒绝率都至少 90% 时，取两轮共同可用范围的中点；没有共同范围或锁定验证失败时，该指标只展示、不参与正式判定。题目和回答来自固定 RAG 基准集，不读取线上数据。该入口只调用 RAGAS 判官与 Embedding，不检索生产数据库、不生成用户回复：

```bash
docker compose -f docker-compose.eval.yml run --name ran_ai-rag-relevancy-calibration --no-deps \
  --entrypoint /app/.venv/bin/python \
  rag-eval /app/eval/calibrate_answer_relevancy.py
```

报告写入 `diagnostics/rag-evaluations/calibrations/`，同时原子更新 `diagnostics/rag-evaluations/answer-relevancy-calibration.json`。报告会保存可用范围、取中的门槛、两阶段判分和每次反向生成的问题、回避判断、向量相似度。旧 v2 报告仍可在前端查看，但不能启用该指标。

## 回复忠实度中文校准

Faithfulness 不使用固定门槛，也不在多个候选值都通过时偏向默认值。v4 从正式 50 题的召回事实中固定选取 30 组不重复来源：前 20 组选择门槛，后 10 组不参与选择、只做锁定验证；全部题独立判分两轮。每一轮的正确回答识别率和错误回答拒绝率都至少达到 90% 时，取两轮共同可用范围的中点；没有共同范围或锁定验证失败时，该指标只展示、不参与正式判定。反例覆盖数字或单位错误、否定反转、条件遗漏、事实方向反转和无依据建议。近期对话只用于理解问题和生成回复，不参与忠实度计算。该入口不检索 RDS、不生成用户回复：

```bash
docker compose -f docker-compose.eval.yml run --name ran_ai-rag-faithfulness-calibration --no-deps \
  --entrypoint /app/.venv/bin/python \
  rag-eval /app/eval/calibrate_faithfulness.py
```

报告写入同一 `calibrations/` 目录，并原子更新 `diagnostics/rag-evaluations/faithfulness-calibration.json`。报告会保存可用范围、取中的门槛和锁定验证的两轮结果；校准未通过时，正式评测仍保留 Faithfulness 分数，但不会用该项单独判定失败。

如果只是修正选线算法，可以零模型调用复用一份完整的 v4 原始判分。重新分析会核对固定题集、两轮题目、判官模型和 RAGAS 版本，生成一份明确标记“复用既有分数”的新报告：

```bash
docker compose -f docker-compose.eval.yml run --name ran_ai-rag-faithfulness-reanalysis --no-deps \
  --entrypoint /app/.venv/bin/python \
  rag-eval /app/eval/reanalyze_faithfulness.py \
  --source-report /app/diagnostics/rag-evaluations/calibrations/<原运行ID>/report.json
```

正式评测不再为数字、单位、否定或比较关系额外调用第二个事实判官；这些关系统一由已校准的 Faithfulness 评价，避免同一回复被两次 LLM 判定和重复判罚。历史报告中的独立告警字段继续保留兼容。

新报告会在不增加模型调用的前提下保留 RAGAS 原始中间判断：Context Recall 的参考事实覆盖、Context Precision 的逐条召回判定、Faithfulness 的回复陈述与依据、Answer Relevancy 的三次反向问题/回避标记/相似度。历史报告没有这些明细时，API 和前端返回空列表并明确标注为历史缺失。

以上校准容器均不使用 `--rm`：跑完后保留为 `Exited`，下次可用 `docker start -a <容器名>` 重新执行。依赖本身固化在镜像中，不会重复下载。

## 再次运行

```bash
docker compose -f docker-compose.eval.yml start rag-eval
docker compose -f docker-compose.eval.yml logs --tail 100 rag-eval
```

脚本和用例通过只读挂载更新，不需要重建镜像；只有 `requirements.txt` 或基础后端镜像变化时才重新 build/up。正式运行会产生真实模型费用，执行前仍需当次授权。

## 无费用自检

```bash
docker compose \
  -f docker-compose.eval.yml \
  -f docker-compose.eval.mock.yml \
  up --no-deps rag-eval
```

Mock 覆盖文件只改变启动参数，仍使用同一个评测镜像。运行结束后容器保留为 `Exited`，可在前端查看演示报告；它不连接数据库，也不调用模型。

每份新报告都明确记录 `mock` 或 `real` 运行类型。Mock 会生成与题库权威事实一致的模拟召回证据和模拟分数；它只用于验收数据链路，不能作为真实模型质量结论。

报告写入 `diagnostics/rag-evaluations/<run_id>/`，后台页面只读这些文件，不持有 Docker 权限。
