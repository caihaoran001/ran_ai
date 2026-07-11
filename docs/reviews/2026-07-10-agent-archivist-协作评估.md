# 群聊 Agent × 档案官 Agent —— 设计与协作评估

日期:2026-07-10
性质:架构评估 / 改进建议(**仅分析,未改任何代码**),等待逐项确认后再动手。

---

## 背景:两个 Agent 各自的职责

- **群聊 Agent**(`backend/src/app/services/agent/`):群里实时跟用户对话的机器人大脑。
  带工具的 LangGraph agent,**只读业务库**(knowledge / service_case / checkin / reminder / skill / web_search),
  产出一条待发群里的回复(还要过风控/人工审核)。查不到准确数据宁可说"暂时没查到",有工具失败熔断。
- **档案官 Agent**(`backend/src/app/services/archivist/`):后台离线流水线
  `load_context → extract → classify → guardrail → commit`,由 DB 水位线派生触发,增量扫聊天记录。
  `extract` 是只上报、不落库的 Agent(LLM 自主调 `report_checkin / report_outcome / report_profile_fact
  / report_correction / report_faq`),真正写库交给 `commit` 的确定性代码。
  是**成效/画像的唯一抽取入口**(聊天热路径已不再实时抽)。

**协作方式(现状,单向)**:档案官沉淀出的画像/成效(`user_state_profiles` / `user_profiles` / `case_memory`)
经 `profile_memory_service` 作为隐性背景喂给群聊 Agent 下次回复;群聊 Agent 不回传任何东西给档案官。

**水位线(watermark)**:`archivist_progress.last_archived_message_id`,每个服务单一行的"已归档到第几条消息"书签。
作用:①增量处理不重抽 ②落库、重启不丢 ③成功才推进、失败原地重试 ④配合行锁防并发双跑。

**整体判断**:分层是对的(职责焊死、写路径确定性、重启不丢),不该动。问题都在**协作接缝**上。

---

## 改进点(按价值排序)

### 1.(最值钱,bug 级)档案官会被"毒消息"永久卡死,连累群聊 Agent 画像永远过期

- **现状**:水位线单调前进,一批抽取失败即整批回滚、水位线不动、下轮重试同一批。
  `consecutive_failures` 仅在诊断接口展示(`api/routes/diagnostics.py`),**无任何隔离/跳过/告警**——
  成功清零(`graph.py:353`)、失败累加(`trigger.py:258`),仅此而已。
- **问题**:任一条消息稳定触发 LLM 抽取异常(格式炸/超长/特定内容),该服务单归档**永久停滞**;
  群聊 Agent 的画像只由档案官更新,于是该用户"当前状态/成效/长期偏好"**再也不刷新**,越到后期越瞎。
  单点故障沿协作链条放大。
- **建议**:`consecutive_failures` 超阈值(如 3)→ **告警 + 把该批拆到单条粒度重试**,
  定位毒消息后**跳过它并推进水位线**(留痕),不让一条消息拖垮整条线。
- **优先级**:高,建议尽快修。**已请用户确认是否先出最小侵入实现方案。**

### 2. 同一批消息被 LLM 读三遍,协作单向无反馈

- **现状**:一条用户消息生命周期里 LLM 至少跑三次——①入营层风控/意图分析(顺带产出 `archive_plan`)
  ②群聊 Agent 生成回复 ③档案官 `extract` 再读原文抽结构化。协作纯单向,Agent 不回传。
- **问题**:
  - 重复读:Agent 回复时已理解过这段对话,该理解丢弃后档案官从零再理解一遍。
  - 无反馈回路:Agent 命中 `DATA_UNAVAILABLE_REPLY`、工具熔断、反问澄清等"有信息缺口/待沉淀"强信号,
    未沉淀成档案官提示;档案官每次从原文盲抽。
- **建议**:让 Agent 回复时顺带 emit 轻量归档 hint(写进 `analysis_json`),
  档案官优先采信、把 extract 降级为"校验+补全"而非"从零抽"。省一次全量 LLM + 协作变双向。
- **优先级**:中,架构级改动,单独立项讨论。

### 3. `archive_plan`(gate)与 `extract`(真抽)在"什么值得沉淀"上判两次

- **现状**:入营层已产出 `archive_plan.kinds`(判本条含 checkin/profile/outcome 哪类,
  `ai_service/client.py` + `intervention_service.py`),档案官 `extract` 又独立重判该报哪些 `report_*`。
- **问题**:两处 worthy 判断可能不一致(gate 说 worthy 但 extract 抽空,或反之),
  调试难定位是哪层看走眼——踩 CLAUDE.md "禁止软硬并行、要能定位" 那条。
- **建议**:明确 `archive_plan` 只做便宜触发闸门、`extract` 为唯一权威;
  或让 extract 显式信任复用 gate 的 kinds 作先验。边界目前隐性,写清更好。
- **优先级**:低,打磨。
- **处理结果(2026-07-11,已落地)**:核对代码后确认"方向 1(gate=闸门、extract=唯一权威)"
  **已是既成事实**——`graph._extract` 写死抽全 5 类、完全不采信 gate 的 kinds,gate 提示词也明说
  "抽不到也无妨"。故**不做行为改动**;方向 2(extract 复用 gate kinds)被否决,因为会把可靠的真抽
  绑死在不可靠的闸门上、违反"禁止软硬并行"。仅补注释把这层"故意不一致"的契约写明,防后人误当 bug 去修:
  `trigger.py::_is_worthy`、`graph._extract`、`extract_agent.run_extract_agent` 的 `enabled_kinds` 三处。

### 4. 画像新鲜度与会话节奏错配(影响有限)

- **现状**:Agent 读的画像由档案官更新,快触发也要"用户停说 ~3min"才抽,否则 24h/20条兜底
  (`trigger.py`:`KEY_INFO_SETTLE_SECONDS` / `MAX_LATENCY_SECONDS` / `MAX_DELTA_COUNT`)。
- **问题**:对话进行中,Agent 背景里的"当前状态/最近话题"基本是上一轮抽取的。
  `current_state` 这类影响不大(Agent 能直接读实时原文);但 `outcome`(当轮才出现的"今天又瘦2斤")
  Agent 当轮从画像里感知不到,只能从原文自看。属可接受取舍。
- **建议**:如需要,可对 `outcome` 类做更快旁路。否则维持现状即可。
- **优先级**:低,视需要。
- **处理结果(2026-07-11,决定维持现状)**:核对后确认影响比原估更小——群聊 Agent 当轮**直接读到
  用户原话**("今天又瘦2斤"就在其对话上下文),另有 `get_weight_trend`/`get_recent_checkins` 实时工具兜底,
  仅 JSONB 投影画像那份"背景"滞后一轮,Agent 当轮并不瞎。"outcome 快旁路"等于把成效抽取塞回**消息热
  路径实时跑**,正是整个档案官重构要消灭的东西(CLAUDE.md/AGENTS.md 反复强调"成效抽取不在消息主链路
  实时跑"),为 Agent 本就能感知的信息回退核心架构,得不偿失。**故关闭为维持现状,不改码。**

---

## 落地顺序建议

1. 先修 **第 1 点**(毒消息隔离 + 水位线推进 + 告警)—— bug 级,单点故障放大。
2. **第 2 点** 立项讨论(架构级,省成本 + 双向协作)。
3. **第 3、4 点** 顺手打磨。**(2026-07-11 已处理:第 3 点补注释写明契约、无行为改动;第 4 点决定维持现状。)**
