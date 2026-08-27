# 回复 Agent 效果评测

正式入口已经统一为 `reply-agent-core-v4`：80 道唯一题只运行一轮，其中 40 道需要知识依据检索，40 道明确跳过通用知识库。每题都执行 M0 基础交付检查，再按题目契约追加 MR（检索）、MT（工具）或 MB（行为边界）；RAG 不再作为独立的顶层产品能力。旧 RAG 50 题与 Agent 30 题的入口、报告目录仍保留，只用于读取历史批次和迁移对照。

## 统一回复 Agent 评测

固定题库为 `tests/fixtures/reply_agent_eval_cases.v4.json`，可由 `python -m eval.build_reply_agent_suite` 确定性重建；v1/v2/v3 保留用于历史报告复现。v4 继承 v3 结构，并落实 2026-08-25 业务确认口径：晚A水量改为默认330ml、冷水冲泡水温不高于85℃、倒班晚A收敛为计划睡眠前1至2小时、随餐放宽为紧邻正餐前后亦可、旅游中断不顺延28天服务期、无红旗便秘保留检索但先澄清。加载时强制校验 80/40/40 配额、`RA-001..RA-080` 连续编号、指标路由以及八类检索场景每类至少四题，避免后续改题时静默改变口径。

统一真实入口依题路由执行三条隔离路径：知识类复用生产意图改写、只读向量检索、回复 Agent 和 DeepEval RAG 指标；工具类给 Agent 生产同款工具定义但只返回题目固定结果；高风险类只跑消息安全识别和最终转人工判断，不发送消息、不进入常规回复。所有 Agent 类题显式跳过通用知识库，避免把工具任务误算成 RAG。

零费用 Mock：

```bash
docker compose -f docker-compose.reply-agent-eval.yml run \
  --name ran_ai-reply-agent-eval-mock --no-deps reply-agent-eval --mock
```

Mock 固定完成 80 题，模型调用、Token 和费用均为 0；其中保留一个忠实度反转样本和一个未授权写工具样本，用于证明两类门禁确实会失败，不能把 Mock 的 78/80 当成线上质量分。

真实评测会连接生产 RDS 只读检索并产生模型费用，必须得到当次明确授权后再运行。默认费用上限为 10 元，达到上限会保留已完成题目和账本并停止剩余题目；同一 `run_id` 不允许重复启动：

```bash
docker compose -f docker-compose.reply-agent-eval.yml run \
  --name ran_ai-reply-agent-eval-real --no-deps reply-agent-eval
```

报告写入 `diagnostics/reply-agent-evaluations/<run_id>/`。后台“回复质量”默认展示统一批次，旧 RAG/Agent 批次放在历史页签继续兼容。

40 道检索题会同时保留原问题与实际改写词的召回结果。若改写后的事实召回率比原问题低超过 0.05，该题标记为改写损伤并进入整题门禁；批次总览单独汇总损伤率。答案相关性、回答正确完整度以及 Agent 的任务完成、证据忠实、澄清和表达也会汇总展示，但在各自完成中文校准前不改变整题通过结果。

以下两节记录旧入口，供历史报告复现。

## 回复 Agent 任务质量

Agent v3 固定题库为 `tests/fixtures/agent_eval_cases.v3.json`：30 题，其中 15 题由线上高频任务脱敏抽象，15 题覆盖写操作授权、信息不足澄清、时间指代和不回复边界；每题固定运行两轮，共 60 次。v3 保留严格的计划外工具检查，但允许每题显式列出合理的可选辅助工具；隔离返回也与线上工具的关键字段对齐，避免因假返回缺信息迫使 Agent 多查一次。对检索词这类自然语言参数，v3 检查必须保留和禁止凭空增加的信息，不再要求逐字复制隐藏标准答案。v2 文件和已生成的 Mock 报告保留，不修改历史版本的尺子。

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

每次正式 RAG 评测会在报告内记录完整的 API 费用账本：意图/问题改写、Agent 回复生成、DeepEval 判断官和 Embedding 分开统计，并保留每道题的实际模型、Token、缓存命中、重试次数、耗时和人民币估算。每次判断官调用的完整请求、响应和指标阶段另存于运行目录的 `calls.jsonl`，避免把主报告撑大；DeepEval 的 reason 与 verbose evidence 保存在逐题报告。LLM 复用线上单价与高峰倍率；`text-embedding-v4` 默认按 DashScope 北京地域标准实时请求 0.5 元/百万输入 Token 计算，可用 `EMBEDDING_COST_PER_1M_TOKENS` 覆盖。只要任一请求缺 usage、缺单价或重试无法逐次定价，单题和整批都标记“费用不完整”，仅显示已知下限，不冒充 0 元或精确总价。Mock 为 0 次、0 元；历史报告明确显示“未记录”。

正式题库固定为 `tests/fixtures/rag_eval_cases.v3.json`：25 条依据生产 FAQ 改写，25 条来自一次性筛选并脱敏的生产聊天；按产品范围口径固定为 16 条应检索、34 条应跳过。只有产品规则、用量、时间、疑似不良反应及可能与产品使用相关的身体问题应检索，通用饮食、运动、睡眠和健康知识应跳过。每次运行只读取这个文件，不会重新抽题或自动替换题目。

## 首次安装或依赖版本变化

```bash
docker compose -f docker-compose.eval.yml build rag-eval
docker compose -f docker-compose.eval.yml up --no-deps rag-eval
```

`deepeval==4.1.5` 已固化在 `ran-ai-rag-eval:local` 评测镜像中，RAG 与 Agent 的新正式入口均使用 DeepEval。RAGAS 已从新入口和依赖中移除，只保留历史报告字段与旧批次读取兼容。评测结束后容器是 `Exited`，不会常驻，但镜像和依赖仍在。

DeepEval 判官最大输出默认 8,000 tokens，由 `DEEPEVAL_JUDGE_MAX_TOKENS` 控制（兼容读取旧的 `RAG_EVAL_JUDGE_MAX_TOKENS`）。它只影响隔离评测判官，不改变线上回复长度；计费仍以实际生成量为准。

## 回复相关性中文校准

DeepEval 原生 Answer Relevancy 已用固定中文正负样本对照验证，但负例大量满分且轮间不稳定，无法选出可信门槛。因此它只展示诊断分数和理由，不参与通过判定。负例满分是双因，成因与逐对原始分数见 `docs/reviews/2026-08-26_答案相关性校准结论更正.md`：一半是 `answer-relevancy-zh-v3` 用"轮转取下一题答案"生成负例、而题库按主题排序导致负例真能回答问题（样本集校验没有任何一条管主题距离），一半是判官在同域材料上相关性门槛偏松。重做必须同时处理这两条，只修样本仍会失败；因此**不要**据"校准失败"直接退役该指标（该建议已于 2026-08-26 撤销）。正式的回答质量观察项使用逐题注入“必须事实、禁止事实、预期回复方式和参考答案”的回答正确与完整度 GEval；该自定义指标完成独立校准前同样只观察、不作发布门槛。

## 上下文召回中文校准

召回门槛与精确率门槛 `0.70` 原本都是 RAGAS 时代沿用下来的产品默认值，从未按数据选线；
召回已于 2026-08-26 按批次 `recall-calib-v2-f8e0352-20260826T014102Z` 从 `0.80` 改为选线得出的 `0.75`
（证据 `docs/reviews/2026-08-26_上下文召回门槛校准证据归档.md`），精确率 `0.70` 仍未校准；
忠实度 `0.8333` 则由真实批次 `deepeval-calibration-1011620-20260822T0725Z` 选出，
证据已归档在 `docs/reviews/2026-08-25_忠实度门槛校准证据归档.md`。

召回校准集 `tests/fixtures/rag_context_recall_calibration.v2.json` 补上其中一条：
30 组（20 组选线 + 10 组独立验证），权威事实由生产 FAQ 正文浓缩改写，
正例上下文是该事实所出自的真实检索块，负例上下文是同一次真实评测里话题相邻、
但缺少核心必要子句的其他真实检索块（缺失类型分为口径冲突、部分覆盖、相邻话题）。
真值逐条人工核对，不采用判官分数，避免用判官验证判官。

v1 首轮真实校准（`recall-calib-real-8918652-20260825T1524Z`）未通过，原因是样本缺陷而非判官噪声，
两条规则已写进样本集契约，违反即在加载时报错：

- **权威事实只写可检索的正面知识断言**，不写禁令（如「不应解释为代谢启动」）。
  知识块里查不到一句禁令，判官对这类子句给不出可复现判定：v1 实测同一样本两轮判定互相矛盾，
  一轮明写「这一点并未被直接覆盖，但核心归因是在的」仍判通过。禁令属于忠实度与回答正确完整度的考核范围。
- **一句一事，且负例必须真的缺核心子句**。v1 有权威事实整句捆了两三个要求，
  判官只能对「对了一半」整句给一个是否，全凭当次裁量；也有负例已覆盖主要求、只差一个边角，
  被判满分其实是正确判定。

每对样本的判官逐条判据与理由都会随分数落进报告，不必再翻原始调用日志定位不稳来源。

同一条禁令规则同时约束正式题库（`retrieval-reference` 策略版本 `atomic-clause-v3`）：
权威事实的 `statement` 原样保留禁令供忠实度与回答正确完整度考核，走召回门禁的事实另外用
`retrieval_clauses` 列出可检索的正面断言，只有这份子句进入召回与精确率评分；不填则回退按句拆分
`statement`。加载时若某条召回事实的子句仍含禁令即报错。历史 `rag-fixed-50-v2` 题库是被取代口径的
冻结留档，不回改，也不受该契约约束。

校准只调判官，不跑回复 Agent、检索和 Embedding，一批 120 次判断官调用。零费用自检：

```bash
docker compose -f docker-compose.eval.yml run --rm --no-deps \
  rag-recall-calibration --mock --run-id recall-calib-mock
```

真实校准会产生模型费用，必须得到当次明确授权后再运行（同一 `run_id` 不允许重复启动）：

```bash
docker compose -f docker-compose.eval.yml run --rm --no-deps \
  rag-recall-calibration --budget-yuan 3
```

选出的门槛不会自动生效：需要人工确认选线与验证两阶段都通过后，
再修改 `deepeval_eval.py` 的 `GATE_CONTEXT_RECALL` 并记录校准批次号。

## 回复忠实度中文校准

DeepEval Faithfulness 已使用固定中文正负样本完成两轮校准，正式门槛为 `0.8333`；Context Recall 门槛为按数据选出的 `0.75`（批次
`recall-calib-v2-f8e0352-20260826T014102Z`），Context Precision 门槛 `0.70` 仍未校准。忠实度只比较最终回复与本轮召回内容，不把聊天前文当作事实依据。数字、单位、否定和条件方向由忠实度与回答正确完整度语义判断，不再从整段参考事实自动抽数字做逐字门禁。

新报告保存 DeepEval 各指标的原始分数、reason、verbose evidence 和逐次 API 调用证据。历史 RAGAS 报告仍可读取；历史批次缺少新字段时按空值展示，不能伪造成新框架证据。

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
