# RAG 与 Agent 评测

RAG 与回复 Agent 使用两套固定题库和报告目录。两类结果统一展示在后台“回复质量”页面，但运行时彼此独立。

## 回复 Agent 任务质量

Agent v2 固定题库为 `tests/fixtures/agent_eval_cases.v2.json`：30 题，其中 15 题由线上高频任务脱敏抽象，15 题覆盖写操作授权、信息不足澄清、时间指代和不回复边界；每题固定运行两轮，共 60 次。v2 保留严格的计划外工具检查，但允许每题显式列出合理的可选辅助工具；隔离返回也与线上工具的关键字段对齐，避免因假返回缺信息迫使 Agent 多查一次。

真实模式会让当前回复 Agent 看到生产同款工具定义，但所有工具执行都在隔离层返回题目预置结果。它不会连接生产数据库、发送企微消息、创建真实提醒或修改任何业务数据；写操作只保留“模型打算怎么调用”的证据。每题先由代码硬检查工具选择、关键参数、禁止动作和明确事实，再由 `DeepEval==4.1.5` 执行工具调用精确匹配、任务完成、证据忠实、澄清合理和表达质量五项指标。日期、对象、关键参数和越权动作仍由业务硬规则判定，不能被通用框架的高分抵消。DeepEval 语义指标完成固定中文正反样本校准前只展示诊断，不参与发布硬门槛。

评测容器强制关闭 DeepEval 匿名遥测、Confident AI 上传、自动打开浏览器、隐式 `.env` 和旧式 key 文件读取；指标直接调用项目现有的 OpenAI 兼容模型地址，不需要 Confident AI 账号或密钥。报告明确记录 DeepEval 版本和每项框架原始分数，旧自研判断官报告继续兼容读取。

每次真实评测还会把模型费用写入独立报告账本：Agent 生成与 DeepEval 判断官分开统计调用次数、输入/输出/缓存 Token、耗时和人民币估算费用，判断官调用会关联到具体指标。计价复用线上统一的人民币 Token 单价和高峰倍率，并优先使用 API 响应中的实际模型名。只要有一次调用没有返回完整 usage，该题和整批总价都会标记为“不完整”，同时保留可核对的已知费用，不用部分费用冒充完整成本。Mock 不调用模型，账本为 0 次、0 元。

无费用 Mock 会完整生成 60 次执行和一条故意注入的未授权写操作，用来证明失败规则、API 和前端证据链有效：

```bash
docker compose -f docker-compose.agent-eval.yml build agent-eval
docker compose -f docker-compose.agent-eval.yml run \
  --name ran_ai-agent-eval-mock --no-deps agent-eval --mock
```

真实评测会产生费用，必须得到当次明确授权后再运行：

```bash
docker compose -f docker-compose.agent-eval.yml run \
  --name ran_ai-agent-eval-real --no-deps agent-eval
```

每批最多 60 ×（5 次 Agent 模型调用 + 5 次 DeepEval 判断调用）= 600 次模型调用：Task Completion 使用两次模型调用，另外三个语义指标各一次；工具精确匹配不调用模型。实际调用数取决于每题工具循环是否提前结束。报告写入 `diagnostics/agent-evaluations/<run_id>/`；每题保存前序对话、预期、工具参数、固定返回、最终回复、代码检查、DeepEval 原始指标、判断理由和逐次 API 成本账本。

下面各节为 RAG 评测说明。

评测只在 ECS 上连接生产 RDS，进程启动后同时启用 PostgreSQL 只读会话和 SQL 写入拦截；意图模型原有的用量日志写入会改为进程内收集。不会调用消息渠道，也不会创建提醒或修改服务单。

每次正式 RAG 评测会在报告内记录完整的 API 费用账本：意图/问题改写、Agent 回复生成、RAGAS 判断官和 Embedding 分开统计，并保留每道题的实际模型、Token、缓存命中、重试次数、耗时和人民币估算。LLM 复用线上单价与高峰倍率；`text-embedding-v4` 默认按 DashScope 北京地域标准实时请求 0.5 元/百万输入 Token 计算，可用 `EMBEDDING_COST_PER_1M_TOKENS` 覆盖。只要任一请求缺 usage、缺单价或重试无法逐次定价，单题和整批都标记“费用不完整”，仅显示已知下限，不冒充 0 元或精确总价。Mock 为 0 次、0 元；历史报告明确显示“未记录”。

正式题库固定为 `tests/fixtures/rag_eval_cases.v2.json`：25 条依据生产 FAQ 改写，25 条来自一次性筛选并脱敏的生产聊天。v2 为两道省略问法补齐了能确定所指对象的前文；每次运行只读取这个文件，不会重新抽 FAQ、聊天或自动替换题目。

## 首次安装或依赖版本变化

```bash
docker compose -f docker-compose.eval.yml build rag-eval
docker compose -f docker-compose.eval.yml up --no-deps rag-eval
```

`ragas==0.4.3` 与 `deepeval==4.1.5` 已固化在共享的 `ran-ai-rag-eval:local` 评测镜像中：RAG 当前继续使用 RAGAS，Agent 使用 DeepEval。评测结束后容器是 `Exited`，不会常驻，但镜像和依赖仍在。

RAGAS 判官的最大输出默认是 50,000 tokens，由 `RAG_EVAL_JUDGE_MAX_TOKENS` 控制。它只放宽离线判官生成结构化评分的上限，不改变线上回复长度，也不会固定消耗 50,000 tokens；计费仍以实际生成量为准。

## 回复相关性中文校准

Answer Relevancy 不再使用未经中文校准的固定 0.80 门槛。v3 固定 30 组中文正负对照：前 20 组只用于选门槛，后 10 组锁定验证，全部独立判分两轮。优先取两轮中能把全部正负样本分开的区间中点；只有不存在这种区间时，才取每轮正确回答识别率和错误回答拒绝率都至少 90% 的共同范围中点。没有共同范围或锁定验证失败时，该指标只展示、不参与正式判定。题目和回答来自固定 RAG 基准集，不读取线上数据。该入口只调用 RAGAS 判官与 Embedding，不检索生产数据库、不生成用户回复：

```bash
docker compose -f docker-compose.eval.yml run --name ran_ai-rag-relevancy-calibration --no-deps \
  --entrypoint /app/.venv/bin/python \
  rag-eval /app/eval/calibrate_answer_relevancy.py
```

报告写入 `diagnostics/rag-evaluations/calibrations/`，同时原子更新 `diagnostics/rag-evaluations/answer-relevancy-calibration.json`。报告会保存可用范围、取中的门槛、两阶段判分和每次反向生成的问题、回避判断、向量相似度。旧 v2 报告仍可在前端查看，但不能启用该指标。

## 回复忠实度中文校准

Faithfulness 不使用固定门槛，也不在多个候选值都通过时偏向默认值。v4 从正式 50 题的召回事实中固定选取 30 组不重复来源：前 20 组选择门槛，后 10 组不参与选择、只做锁定验证；全部题独立判分两轮。优先取两轮中能把全部正负样本分开的区间中点；只有不存在这种区间时，才取每轮正确回答识别率和错误回答拒绝率都至少达到 90% 的共同范围中点。没有共同范围或锁定验证失败时，该指标只展示、不参与正式判定。反例覆盖数字或单位错误、否定反转、条件遗漏、事实方向反转和无依据建议。近期对话只用于理解问题和生成回复，不参与忠实度计算。该入口不检索 RDS、不生成用户回复：

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
