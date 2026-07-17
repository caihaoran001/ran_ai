# 档案官 Agent（Archivist）设计方案

> 状态：**设计稿，待评审**。本文件是蓝图，非实现记录。定稿并开工后，进度接力写入 `docs/wip/CURRENT_TASK.md`。

---

## 0. 一句话定位

**回复 Agent 负责"说话"，档案官负责"记录归档"。** 档案官是一个独立的、事件驱动的后台 Agent，按服务单把"对话里发生的事"沉淀成持久档案——画像、成效、打卡、自由文本记忆，并把值得沉淀的知识/案例提名成草稿送人工审核。它是目前系统里**唯一缺失的"写手"**（`profile_memory_service` 已经是记忆的读视图，存储表也齐了，就差一个统一往里写的角色）。

---

## 1. 背景：为什么要做（来自真实服务单 RAI-20260702-159E2A 的问题）

- **打卡是空的**：用户报了"168kg"、发了多张餐食图、说了运动，但 `checkins` 表一行都没有——因为**聊天没有任何路径写 Checkin**（只有问卷表单 `questionnaires.py:342` 和后台 `checkins.py:75` 两个入口）。
- **日打卡机制本身要换**（决策，见 §2.3）：现在靠 `reminder_service` 每天往群里发 H5 打卡链接（`daily_checkin` 规则 → `personalized_checkin` 模板）。**这条要下线**——群内反复发外链触发微信/企微风控，得不偿失；改为**从聊天被动抽取 + 自然语言追问补齐**，一条链接都不发。
- **画像基础数据错且无人发现**：画像写 100kg/BMI27/"暂无风险"，实际 168kg/BMI≈45。逐条消息抽取天生近视，看不到跨消息矛盾。
- **状态派生散落在热路径**：`outcome_engine` + `user_state_profile` 在 `engine._run_outcome_analysis`（engine.py:1046/1199）里逐条消息同步跑，耦合进回复链路。
- **回复 Agent 没有学习闭环**：员工纠正了 AI 的错误回答（如 7/3 绿原酸/早C/午B 时间被 Lucy 纠正），这些高质量监督信号没有被沉淀，下次还错。
- **图片单发被跳过分析**（engine.py:995），餐食图这个最主要的饮食打卡来源根本进不来。

---

## 2. 职责范围

### 2.1 In scope（档案官负责）
| 领域 | 写入目标 | 说明 |
|---|---|---|
| 打卡投影 | `checkins`（按天 UPSERT） | 从聊天/图片抽取 `CHECKIN_KEYS` 字段，合并进当天一行。**日打卡 H5 下线后，档案官是打卡的主要写手**（§2.3） |
| 成效 | `outcome_events` / `outcome_*` | **收编** 现有 `outcome_engine` 的抽取与落库 |
| 画像/结构化记忆 | `user_state_profile` | **收编** 现有 `user_state_profile` 的更新 |
| 自由文本记忆 | `case_memory`（**新建表**） | 近期对话滚动摘要 + 可召回情节片段 |
| 矛盾检测 | 人工队列 / 画像标记 | 如 100kg vs 168kg，标记送审，不静默改 |
| 知识/案例提名 | `knowledge_draft`（**只产草稿**） | 复用 `knowledge_evolution` 审核闸，发布仍由人工 |

### 2.2 Out of scope（明确不做）
- **不直接发布全局知识**：`knowledge_faqs`/`knowledge_chunks` 的发布权永远在人工审核闸后面。档案官只产草稿。
- **不改受保护字段**：`risk_level`（长期健康风险载体，须持久）、问卷权威字段——只能"补充/提议+标记"，不能推翻。
- **不生成对用户的回复**：说话是回复 Agent 的事。档案官纯后台。

### 2.3 决策：日打卡 H5 下线，打卡改由"聊天抽取 + 自然语言追问"

**背景**：现在每天往群里发 H5 打卡链接让用户填。弊端：①群内反复发外链**触发微信/企微风控**（限链/封号风险）；②本意"让用户关注这些信息"可以用更自然的方式达到。

**决策**：
1. **停发日打卡链接**：下线 `reminder_service` 的 `daily_checkin` 规则（发 `personalized_checkin` 模板那条）。问卷建打卡路径 `_create_checkin_from_questionnaire` 随之闲置（保留代码，可作运营手动补录的兜底入口，走同一合并 upsert，字段权威）。
2. **聊天被动抽取当主力**：用户自然提到的体重/喝水/睡眠/饮食/运动/情绪/体感 → 档案官抽取落打卡。
3. **缺字段用自然语言追问补齐，不发链接**：档案官发现关键字段缺失（尤其 `product_schedule` 早C/午B/晚A 依从、或本周没量体重）→ 让回复 Agent/提醒**在群里用一句话问**（"今天晚A喝了吗？""这两天量体重了吗？"）。既保住"让用户关注"的本意，又零外链。

> **必须正视的代价**：被动抽取的**完整度天生不如表单**——用户几乎不会每天主动播报"我喝了晚A"。`product_schedule` 这类不会自然出现在聊天里的字段，**必须靠上面第 3 点的主动追问兜底**，否则阶段报告里的"7天依从率"会失真。这是本决策最大的风险点，追问机制不是可选项。

> **保留**：**入营问卷不动**（一次性、生成画像+基线体重腰围、精度要求高、非高频、风控压力小）。下线的只是**每日**打卡 H5。

> **副作用（正面）**：表单退出日常写入后，档案官成为打卡近乎唯一的写手，之前担心的"表单 vs 档案官同日撞车、谁覆盖谁"基本消失，按天 UPSERT 直接干净。

---

## 3. 触发模型：挂 inbound、事件驱动 + 去抖（带最大滞留上限），cron 只兜底

- **挂载点 = inbound 消息处理点，不是 outbound 回复（关键，必须写死）**：触发在**入站消息处理完成时** fire，而非"回复发出后"。理由见 §3.2——回复 Agent 会**缺席**，而缺席的场景恰恰最该归档。
- **fire-and-forget 入队，不是同步调用**：inbound 处理只负责"打标记 + 去抖入队"就返回，档案官（重活：全上下文、多节点图）在**回复热路径之外**异步消费。绝不能让档案官拖住热路径。
- **挂在聚合后的引擎入口，不是每条裸消息**：`qiwe_service` 已对同一用户/群做 ~8s 聚合，`RanrenAIEngine` 收到的是**聚合后的 `ChannelMessage`**。入队挂在这个单元上 → 天然"一个聚合窗口 enqueue 一次"，别退化成每条裸消息一次（否则去抖前就先放大一轮）。
- **前置闸门（必须先满足，否则一律不触发）**：水位线之后**必须有用户的新发言**。客户没说话（窗口里只有系统提醒/机器人消息、或无消息）→ **不跑**，不管过了 24h 还是 48h——没有新素材，跑了纯浪费。靠水位线天然实现。
  - 区分："用户没说话"才排除；"用户说了、机器人没回"（高风险转人工/班主任接话/图片跳过，§3.2）是**用户发言了**，闸门放行，且正是最该归档的场景。
- **三条触发条件是"或"（满足其一即跑），不是"且"**：闸门放行后——
  - **A 冷却**：最后一条用户消息后静默满 **2 小时**（正常：一波对话聊完安静下来 → 归档）。
  - **B 最大滞留时间**：距上次归档满 **24 小时**（兜底：用户零星发一整天，A 凑不满 2h → 强制跑）。
  - **C 最大滞留条数**：累积 **20 条用户发言**（兜底：话特别多的日子，别憋到 24h）。
  - 为什么"或"不"且"：若"且"，安静用户永远凑不满 20 条 → 永不归档。A 管正常收尾，B/C 管极端活跃防积压。有闸门在，B 的 24h 钟只在"存在未归档用户新发言"时才算数，用户沉默即无钟、不空转。
- **兜底触发（cron sweep）**：挂在 `ran-ai-scheduler`（`workers/scheduler.py` 现只跑 `ReminderService`），做**日结、里程碑快照、抓漏补跑**。因为有水位线，sweep 不会重复处理；万一某条 inbound 入队异常，日结也能补。

> 铁规：档案官是事件驱动，**不是纯定时批处理**。纯 cron 会导致状态滞后（用户报完体重马上问"我瘦了吗"，回复 Agent 拿到旧数据）。
>
> **但即时字段不能等去抖**（见 §3.1）。

### 3.1 分层收编：热路径留"瘦投影"，档案官做"厚沉淀"（核心张力，必须正面定）

**问题**：现在 `_run_outcome_analysis` 在回复热路径同步跑，回复 Agent 依赖它产出的 `user_state`（当前行动焦点/最新体重）作为背景。若把它整个收编成"异步 + 去抖 5–15 min"，就会出现：用户报完体重**马上**问"我瘦了吗"，去抖还没到，投影没更新，回复 Agent 拿到旧 state → 状态滞后回归。

**决策**：**收编不是全盘搬走，而是分层**。
- **热路径保留一条轻量同步投影**：只处理"即时就要用"的少数字段——最新体重/腰围、明确的风险信号——直接、便宜、无需多步图，回复 Agent 立刻能用。
- **重活交给去抖的档案官**：成效聚合、里程碑快照、情节记忆、矛盾比对、纠正配对、知识/案例提名——这些不要求"这一秒就对"，放异步。

> 一句话：**瘦投影管"这一秒回复要用的"，厚沉淀管"长期档案的正确"。** 两者写同一批领域表，但用幂等写入（§4.3）保证不打架——瘦投影先写的最新体重，档案官厚沉淀时若发现矛盾走提议/标记，而不是盲覆盖。

> **两个触发点挂载统一**：瘦投影与档案官都挂在 §3 的 **inbound 处理点**——瘦投影**同步**执行（即时字段，就地更新），档案官**异步入队**（厚沉淀，热路径外消费）。

### 3.2 为什么挂 inbound 而不是 outbound（否掉"回复后 fire"的决定性理由）

一个诱人的省钱思路是"每条消息本来都要走回复 Agent，让回复 Agent 顺手触发档案官，连分诊都省了"。方向（搭车在必然发生的调用上、零额外 LLM）是对的，但**不能绑在 outbound 回复上**——因为回复 Agent 会**缺席**，而缺席的场景**恰恰是归档价值最高的**：

| 回复 Agent 缺席的场景 | 出处 | 但它恰恰是… |
|---|---|---|
| 高风险用户强制转人工，AI 不自动回复 | 风控规则 | **最该沉淀**的风险信号 |
| 班主任已接话，AI 回复被丢弃 | `ai-not-undercut-staff` | **最金贵的纠正/监督信号** |
| 图片单发被跳过分析 | engine.py:995 | **餐食图 = 打卡主来源**（§4 要修的正是它）|
| AI 生成失败直接丢弃（`except: return`）| engine.py | 至少要留痕 |

把触发绑在 outbound = 你想归档的重点正好在回复 Agent 沉默时发生，**全漏**。所以挂 **inbound**（所有消息必经，含不回复/被跳过的）。搭车省钱的正确形态：inbound 处理时基于链路里**已有的元数据/抽取结果** emit 一个轻量信号入队即可，零额外 LLM 调用，且不依赖回复是否产出。

> **是否需要一层"便宜 LLM 分诊"？默认不需要。** 分诊的唯一价值是挡掉"空窗口"（纯寒暄/表情/"收到"），划不划算取决于群的信噪比。你们是**一对一服务群、信噪比偏高**，大多数窗口都有实质内容，分诊大概率是多余一跳——用"元数据挡明显空窗口 + 直接抽取"就够。是否加分诊，待影子期统计"多少窗口最终产生归档写入"（P）再定，别提前上（§13.6）。

### 3.3 归档信号 `archive_plan`：搭车现有意图识别 LLM，零额外调用

现有意图识别 LLM（`ai_service/client.py:extract_intervention_insight`）**每条 inbound 都会跑**，已输出 `agent_context_plan`（`message_scene`/`memory_scopes`，给回复 Agent 选背景）。归档信号**搭在这同一次调用**上，不新开调用、不新起模型。

在其输出 JSON 里加一个**独立**字段（与 `agent_context_plan` 平级，语义不混）：
```jsonc
"archive_plan": {   // 给档案官用；与风险/转人工/续购/退款完全独立
  "worthy": boolean,
  "kinds": ["checkin | profile_fact | outcome | correction | knowledge_qa，可空"],
  "reason": "一句话依据"
}
```
提示词判定要点（新加一段 `【归档信号 archive_plan 判定】`）：
- `checkin`：报告了任何可量化/结构化的当日执行数据（体重/围度/喝水/睡眠/饮食/运动/排便/饥饿/情绪/体感/明日重点），**哪怕只一项**。
- `profile_fact`：透露相对稳定的个人事实/偏好/习惯/长期约束。
- `outcome`：报告阶段性进展/变化。
- `correction`（营养师/班主任纠正上一轮 AI）、`knowledge_qa`（可沉淀通用问答）——**〔P2 启用，P0 先标"后续启用"〕**。
- `worthy=false`：纯寒暄/表情/无信息量确认/无关闲聊。
- **偏向与风控相反（关键）**：拿不准但**可能含数据/事实**就 `worthy=true`——归档误报只是白跑一次抽取（便宜），漏报就是打卡继续空着（要命）。

落库：`archive_plan` 跟 `analysis_json` 一起存进每条 message（复用 `_store_intervention_analysis`/`_update_inbound_analysis`）。触发闸门据此收紧：**delta 里有 ≥1 条 `worthy=true` 才触发档案官**，纯噪声窗口连档案官的抽取调用都省掉。

> 注意：这次是**安全攸关的风控调用**。`archive_plan` 只做**轻量打标（worthy+kinds），不抽具体值**，真正取值留在档案官 `extract`。上线后盯风控识别质量，若因这段下降就把 `archive_plan` 拆成独立调用。P0 实际只用到 `checkin` 一类。

---

## 4. 输入模型：水位线 + 只读上下文 + 幂等写入

**问题**：一天跑多次，每次输入是什么？第二次会不会重复第一次？
**答案**：不会重复。用水位线区分"要写的增量"和"只读的上下文"。

1. **水位线（watermark）**：每个服务单存 `last_archived_message_id`。**落独立进度表 `archivist_progress`，不给宽表 `service_cases` 加列**——将来可能有多个后台 agent 各自持有水位，独立表更干净、可扩展。每次只把水位线之后的**新消息**当"要抽取/沉淀"的增量；跑完推进水位线。
   - **复用现成机制**：`knowledge_evolution.py` 的 `_is_processed / _mark_processed / _has_draft(content_hash)` 就是这套，直接复用。
2. **只读上下文**：喂给 Agent 的除了增量，还有 ①当前**画像+摘要**（= 之前所有轮次压缩后的记忆，来自 `profile_memory_service`）②最近 K 条原始消息。**上下文只用于理解增量，不产生新写入。**
   - 好处：不管服务跑到第几天，每次输入**有界、便宜**（读压缩记忆而非全部历史）。
3. **幂等写入（双保险）**：
   - 打卡：`(service_case_id, checkin_date)` **UPSERT**（需新增条件唯一索引，配软删除）。
   - 成效事件：`(service_case_id, message_id, field_key)` 去重（约束已存在）。
   - 知识/案例草稿：`content_hash` 去重（`_has_draft` 已存在）。

---

## 5. 运行结构：LangGraph 多节点流水线（不是 ReAct 循环）

> 取舍说明：回复 Agent 是"直线活"，用 `agent↔tools` 循环够了。档案官写的是**业务事实**，要的是**可控**——顺序不能交给大模型临场决定（否则可能没过护栏就把个人处方当全局知识写进 FAQ）。所以做成**顺序焊死的多节点图**：走不到"提交"节点，除非先过"护栏"节点。
>
> **LLM 只用在"判断"节点，落库用确定性代码**（呼应项目纪律：判断优先大模型结构化输出，落库确定性兜底）。
>
> **决策 A（多类沉淀的形态，2026-07-09 定）**：`extract` 节点做成**一个真 Agent**——单次 LLM 挂 `report_checkin / report_profile_fact / report_outcome / report_correction / report_knowledge_qa` 若干工具，由 LLM **自主决定调哪几个、调几次**（一条消息可同时是打卡+成效）。关键约束：**这些工具"只上报、不落库"**——工具返回值仅是"抽到的结构化意图"，真正的 DB 写（按天 UPSERT、表单权威、补空、矛盾留痕、幂等去重、水位线推进）全部留在 `commit` 节点的确定性代码里。即"LLM 当记录员、代码当档案柜管理员"。**否掉**"工具直接写库的纯 agent"（与重启安全/幂等/合并精细度冲突）和"代码硬路由每类一次抽取"的纯 pipeline（放弃了 agent 的跨类协同）。**P0 只有打卡一类，仍用单抽取器；此形态在落地第 2 类(成效/画像)时才重构 `extract`，届时有真实数据佐证。**

```
[load_context] → [extract(Agent: LLM + report-only 工具)] → [classify(LLM)] → [guardrail(code)] → [commit(code)]
```

| 节点 | 类型 | 职责 |
|---|---|---|
| `load_context` | 代码 | 读水位线、拉增量消息、载入画像/摘要/最近打卡（只读视图） |
| `extract` | **LLM 抽取（1–2 跳）** | 从增量抽结构化候选：打卡字段(`CHECKIN_KEYS`)、成效事件、画像信号、值得记的情节、"AI答错→员工纠正"配对。**不把"一次调用搞定"当卖点**——把 5 个抽取任务压进一个 prompt 要么臃肿、要么单任务质量下降；宁可承认可能是"抽取 + 分类"两跳，output schema 设计克制些反而更稳。收编成效后，这里能吃掉原 outcome 抽取那跳，净增调用有限 |
| `classify` | LLM/规则 | 给沉淀候选打标：**全局知识 vs 个人处方**；检测矛盾（如体重 100 vs 168） |
| `guardrail` | 代码 | 受保护字段拦截（`risk_level`/问卷权威字段只提议不覆盖）；要求 evidence；决定"自动写 / 提议送人工 / 产草稿送审" |
| `commit` | 代码 | 幂等落库；产知识/案例草稿（不发布）；写 `case_memory`；推进水位线 |

### in-run state（TypedDict，仅本次运行内流动，不跨运行、不做 system-of-record）
```
watermark_from / watermark_to
delta_message_ids
context: {profile, state_summary, recent_checkins, recent_messages}
checkin_candidates / outcome_events / profile_signals / memory_episodes / correction_pairs
classified: [{candidate, scope: global|personal, contradiction?}]
guardrail_verdicts: [{target, action: write|propose|draft, reason}]
committed / drafts_created   # 供 trace/审计
```

> **持久档案 = 领域表；LangGraph state = 本次运行的脚手架。** 不挂 checkpointer 作为记忆归属地（你们现在 `graph.compile()` 也没挂，基线是对的）。checkpointer 顶多用于"断点续跑"，且因为有水位线+幂等，重跑本就安全，属可选优化。

---

## 6. 读 / 写工具清单

**读（复用现有 `service_case_tools.py`）**：`get_service_progress` / `get_recent_checkins` / `get_weight_trend` / `list_group_files` / `read_file` 等——回复 Agent 已在用，档案官共享。

**LLM 上报工具（决策 A，供 `extract` Agent 自主调；纯上报、无副作用、不碰 DB）**：`report_checkin` / `report_profile_fact` / `report_outcome` / `report_correction` / `report_knowledge_qa`——各自的入参 schema 对应下面一个领域，返回值只是"收到这条意图"，由 `commit` 节点汇总后交给确定性写函数。

**确定性写函数（供 `commit` 节点调；不做成让 LLM 自由调的写工具）**：
- `upsert_checkin(case_id, date, fields, evidence_msg_ids)`
- `apply_outcome_events(...)`（复用 `apply_outcome_analysis_plan` / `apply_outcome_events_to_user_state`）
- `apply_profile_signals(...)`（复用 `apply_profile_signals_to_user_state`）
- `upsert_case_memory(case_id, rolling_summary, episodes)`（新）
- `create_knowledge_draft / create_case_draft(...)`（复用 `knowledge_evolution` 建草稿逻辑，`content_hash` 去重，**不发布**）
- `propose_profile_change / flag_contradiction(...)`（受保护字段/矛盾 → 人工队列）
- `advance_watermark(case_id, to_message_id)`

---

## 7. 护栏规则（Guardrails）

1. **受保护字段不许静默覆盖**：`risk_level`、问卷权威字段（体重/身高/BMI 等 intake 来源）——档案官只能"补充空缺"或"提议修正+标矛盾"，人工确认后才改。
2. **全局知识必过审核闸**：任何进 `knowledge_faqs`/`knowledge_chunks` 的内容只能是草稿，走 `approve_draft`/`reject_draft`。
3. **每条写入带 evidence**：引用来源 message_id，落 trace，可审计（方便排查"是提示词问题还是工具问题"）。
4. **软删除**：所有写入遵循 `deleted_at` + 条件唯一索引，绝不物理删。
5. **QA 表四入库限制**：产知识草稿时，检索正文只放"问法/参考回复/回复结构"，分类/风险/转人工放 metadata。

---

## 8. 纠正学习闭环（员工纠正 → 沉淀）

**触发信号**：`Message` 里"bot 回复 → staff 追加纠正/补充"的序列。当前 QiWe 员工入站消息没有稳定指向机器人回复的 `trigger_id`，因此档案官读取水位线后的有界有序消息，模型只上报输入中真实存在的 bot/staff 消息 ID，commit 再校验同服务单、角色和先后顺序。若渠道后续提供引用关系，再用引用 ID 收紧配对，不用时间窗口冒充精确关联。

**关键分流（`classify` 节点，务必拆开）**：
- **全局产品知识**（"绿原酸是什么/含咖啡因/别叠奶茶/一般怎么喝"、"钥匙是什么产品"）→ `knowledge_draft`（走审核）→ 反馈给**所有用户**的回复 Agent。**复用现有 `knowledge_faqs`/`knowledge_chunks`，不新建产品库。**
- **个人处方/个性化安排**（如"阿杰喝 5 次""下午绿原酸睡不好就取消"）→ 写进**该用户**的 `user_state_profile` / 执行单 / `case_memory`，只影响他一个人。

> 反例警示：Lucy 补的"一天喝 5 次"原话是"这是你这边特别补充的"——是个人处方，误当全局知识入库会污染别人的回复。

---

## 9. 与现有系统的接线（复用 / 收编 / 新建）

**复用（不动）**：`profile_memory_service`（读视图）、`user_state_profile` 各 domain、`checkins` 表、`outcome_*`、`knowledge_evolution`（审核闸 + 去重原语）、`case_library`、`service_case_tools`（读工具）、`factory.py` 工具装配。

**收编（迁移，P0.5，分层）**：`engine._run_outcome_analysis` 里逐条消息的 outcome/state 写入 → **分层迁移**（§3.1）：即时字段（最新体重/风险信号）在热路径留一条瘦投影，重活迁到档案官。**过渡期用开关灰度**：先并行/影子运行验证一致，再摘掉热路径里的旧调用。

**新建**：
- `case_memory` 表（自由文本记忆）。
- **独立进度表 `archivist_progress`**（存水位线 `last_archived_message_id`，不给 `service_cases` 加列，§4/§10.3）。
- 档案官图/节点（`services/agent/` 下新增，复用 state/graph/tools 模式）。
- 触发接线：挂 **inbound 处理点**、**fire-and-forget 入队**（去抖 + 最大滞留上限）+ scheduler sweep 兜底（§3）。

---

## 10. 数据模型改动

1. **`case_memory`（新表）**：`id / service_case_id / rolling_summary(Text) / episodes(JSON,可召回片段) / updated_at / deleted_at`。
2. **`checkins` 加条件唯一索引** `(service_case_id, checkin_date) WHERE deleted_at IS NULL`，支撑 UPSERT。
3. **水位线（独立进度表 `archivist_progress`）**：`service_case_id / last_archived_message_id / updated_at`——**不给宽表 `service_cases` 加列**，将来多个后台 agent 各持水位更干净（§4）。
4. **放开图片单发跳过分析**（engine.py:995）：至少让餐食图走 diet 抽取/投影。

> Alembic：新增迁移，**严禁改已运行的旧迁移**。枚举新增值按大写成员名。

---

## 11. 分期落地

> 原则：**P0 不碰成效/画像热路径（`_run_outcome_analysis` 一行不动）**，最快补上最痛的洞；高风险的"收编"（热路径迁移）单列 P0.5，影子对账一致后再摘旧路径。
> 注：P0 **不是"零改动"**——它要下线日打卡链接、改打卡写入走合并 upsert（§2.3）。只是这些都**不碰回复/成效/画像那条线**，风险仍然低。

- **P0（补洞 + 日打卡换血）**：
  - 档案官骨架（load→extract→classify→guardrail→commit），P0 只接**打卡投影**一件事；
  - `extract` 搭车现有意图识别 LLM 的 `archive_plan` 标记（§3.3），触发闸门收紧成"delta 含 worthy=true"；
  - 放开图片单发跳过（`engine.py:995`），让餐食图进 diet 抽取；
  - `checkins` 按天 UPSERT + 条件唯一索引；`archivist_progress` 水位线；
  - **下线日打卡 H5**：停 `reminder_service` 的 `daily_checkin` 发链接规则（§2.3）；
  - **自然语言追问补齐**（不发链接）：至少覆盖 `product_schedule`（早C/午B/晚A）与体重的缺失检测→群内一句话追问。**这条不是可选项**，否则完整度回归。
  - → 修好"打卡为空/168 无人记录"，同时消除群内发外链的风控风险，且**完全不动 `_run_outcome_analysis`**。
- **P0.5（收编 outcome/state）**：把热路径的 outcome/state 写入迁到档案官，**分层**（§3.1：热路径留瘦投影，重活进档案官）；**影子并行跑对账**，与旧路径产出一致后再摘旧调用。单独一期，出问题不拖累 P0。
- **P1（自由文本记忆）**：`case_memory` 表 + 滚动摘要/情节沉淀；`profile_memory_service` 扩展把它喂给回复 Agent。
- **P2（纠正学习闭环，已落地员工纠正主链路）**：员工消息触发档案官 → `report_correction` 只记录“助教哪里答错了”，其中个人安排直接进入用户动态背景；能独立复用的班主任纠正需另外调用 `report_faq_candidate`，并由该唯一入口生成待审核 FAQ 草稿。单条群聊不自动产生全局“回答方法”规则；回答方式改进保留纠正证据，后续由人工批量复盘提示词或 Skill。标准案例提名仍由既有阶段扫描负责。

---

## 12. 风险与权衡

| 风险 | 缓解 |
|---|---|
| 每次运行成本（全上下文+多步） | 水位线 + 窗口触发 + 读压缩记忆；按对话窗口去抖，绝不退化成每条消息一跑 |
| 非确定性（Agent 写坏事实） | LLM 只做判断、代码做落库；护栏节点结构性拦截；evidence + 幂等 |
| 状态滞后 | 事件驱动 + 去抖为主，cron 仅兜底；**即时字段走热路径瘦投影不等去抖**（§3.1）；去抖带最大滞留上限防活跃群永不冷却（§3） |
| 收编迁移搞坏线上 | 拆成独立 P0.5；灰度开关 + 影子运行对账，验证一致后再摘旧路径 |
| 矛盾检测有边界，非根治 | 画像已错成 100、新消息说 168 时能比对出来；但若矛盾两条都在增量里、且压缩摘要没保留数字精度，仍可能漏——是显著提升非根治，关键数字字段在摘要里保精度可缓解 |
| `case_memory` 滚动摘要漂移/膨胀 | 每次 LLM 重写有累积误差和遗忘；P1 先简单，但需版本化/可纠错留口子，关键结构化事实以领域表为准而非摘要 |

---

## 13. 决策记录（原开放问题，已定）

1. **触发阈值（已定，边跑边观察）**：前置闸门=水位线后有用户新发言（无发言不触发）；三条"或"关系——A 冷却 **2h** / B 最大滞留 **24h** / C 累积 **20 条用户发言**（§3）。先按此上线，重点观察真实群里这组值合不合适，再微调。
2. **收编方式**：**影子并行跑对账**，不直切；单列 P0.5（§11）。
3. **打卡矛盾如何处理（已更新，2026-07-09）**：走**追问用户本人确认**，不丢给运营（尽量减少运营参与）。表单值≠聊天值→记矛盾→晚间自然语言追问核对（复用 `followup.py`，不发链接，`asked` 去重）→**用户答复的新值可覆盖原表单值**（追问时把该字段来源降级为 `user_confirmed`，走既有"聊天最新值赢"合并分支）。原"画像页高亮 + 事件级待审队列"方案取消。若将来仍需人工出口，注意"人工介入"是**事件级**（`pending_review_case_ids_subquery` 派生），不要写已停写的 `service_cases.needs_human_intervention` 列。
   - **已知局限**：矛盾行与用户答复行若跨自然日（答复落到新一天的 checkin 行），原矛盾行保留旧表单值不回改，仅"当前最新读数"正确；H5 下线后 form 来源已罕见，此边缘可接受。
4. **情节片段召回**：先**纯文本/关键词**，不急上 pgvector（与项目现状一致，默认库无 vector 扩展）。
5. **纠正配对判定（按当前渠道能力修订）**：员工入站没有稳定 reply/trigger 关联；先用有界有序上下文让模型提名 bot/staff 消息 ID，再由代码校验同服务单、角色和先后顺序。渠道有引用 ID 后直接升级为精确配对（§8）。
6. **触发挂载点**：挂 **inbound 处理点**、**fire-and-forget 入队**，不挂 outbound 回复（§3.2 论证：回复 Agent 缺席场景恰是归档重点）。**分诊层默认不加**——一对一服务群信噪比高，分诊大概率多余；先"元数据挡空窗口 + 直接抽取"，是否加分诊待影子期统计 P（产生归档写入的窗口占比）再定。
7. **归档信号 `archive_plan`**：搭车现有意图识别 LLM，加一个独立字段做轻量打标（worthy+kinds），不新开调用、不抽具体值；偏向与风控相反（可能有数据就标）；P0 只用 `checkin` 类（§3.3）。
8. **日打卡 H5 下线**（已定）：停 `reminder_service` 发链接，打卡改由"聊天抽取 + 自然语言追问补齐（不发链接）"；入营问卷保留；`product_schedule`/体重的追问补齐**不是可选项**（§2.3）。
9. **触发状态抗重启**：去抖不用内存 timer，全部**从 DB（messages + archivist_progress）派生**，`SELECT ... FOR UPDATE SKIP LOCKED` 防双跑；呼应"重启丢在途"教训。
10. **多类沉淀的运行形态（决策 A，2026-07-09 定）**：`extract` 做成"一个 Agent 挂多个 `report_*` 工具，LLM 自主调；工具只上报不落库，写库留给 `commit` 确定性代码"（详见 §5、§6）。否掉"工具直接写库的纯 agent"和"代码硬路由的纯 pipeline"。P0 只有打卡、仍单抽取器；此形态在落地第 2 类时才动手重构。
