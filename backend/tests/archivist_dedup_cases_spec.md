# 档案官去重归档评估规范（archivist dedup cases spec）

> 本文件是**评估规范（spec），不是测试代码**：只描述输入消息与期望的 DB 效果，
> 不包含任何模型输出、不含 runner。由另一个 agent 据此编写 pytest。
>
> 术语：`MUST_ADD` = 必须新增一行；`MUST_UPDATE` = 必须原地更新已有行、不新增；
> `MUST_NOT_DUPLICATE` = 不得新增重复行；`MUST_NOT_MISS` = 不得漏报/静默丢弃。
>
> 流水线语义依据（`backend/src/app/services/archivist/`）：
>
> - `graph.py::_load_context`：水位线只推进 USER/STAFF 增量；当前批前最多补 5 条
>   连续聊天背景（`ARCHIVIST_HISTORY_CONTEXT_MAX_MESSAGES = 5`）；`allowed_dates`
>  （`delta_dates`）**只**来自本批 USER/STAFF 增量经 `_sh_date` 的业务日集合，
>   助教/系统提醒消息不制造业务日期。`_sh_date`：北京时间 03:00 分界，
>   00:00–02:59 归前一天。
> - `extract_agent.py::_SYSTEM_PROMPT`：标【历史】的消息只用于理解，**不得重复上报、
>   不得作为新业务日期或任何用户事实的证据**；`report_checkin/outcome/profile/state`
>   只能提取【用户】本人信息；打卡证据必须是同日直接支持字段的用户消息 ID。
> - `graph.py::_extract` 落桶闸门：checkin 证据 ID 必须落在
>   `user_message_date_by_id[evidence] == target_date`，否则整天候选丢弃；
>   `target_date ∉ allowed_dates` 则 `checkin_date_not_in_batch` 丢弃；
>   correction/faq 的 `staff_message_id` 必须在本批 `current_staff_ids` 内，
>   否则 `historical_staff_message` 丢弃；final_review 证据必须是本批用户消息。
> - `outcome_engine.py::apply_archived_outcome`：anchor = 本批**最后一条用户消息**
>  （`max(delta_user_message_ids)`）；`_event_exists` 去重键 =
>   `(service_case_id, field_key, anchor message_id)`。含义：**跨批同事实天然是
>   不同 anchor，DB 层不会自动去重**——去重完全依赖模型纪律（历史已归档事实不再报）。
>   因此凡 `MUST_NOT_DUPLICATE` 的 outcome/profile/state 项，都是对模型
>   “不得重报历史”的期望，而不是对确定性代码的期望。
> - `checkin_projection.py::upsert_checkin`：空→填（`filled`，MUST_ADD 语义）；
>   值相同→`skipped_same`；chat-over-chat 值不同→最新聊天值赢（MUST_UPDATE）；
>   表单来源字段值不同→`kept_form` + `contradictions`（不覆盖）；一天一行原地 UPDATE。
> - `graph.py::_commit`：memory 文本相同则不写；case 草稿仅当**本批写了正向成效**
>   （`positive_outcome_written > 0`）且 `source_message_ids` 落在
>   （本批用户 ID ∪ 历史 outcome_timeline 的 source_message_id）内才创建。
>
> 通用批处理约定（各组适用，不再重复）：
>
> - 每组内 Batch N 归档成功后水位线 `watermark_to = max(本批 USER/STAFF 消息 ID)`；
>   Batch N+1 的 `history` = Batch N 的尾部消息（最多 5 条，标【历史】），
>   `archive_context` = Batch N 已落库的结构化事实（outcome_timeline / user_summary 等，
>   无聊天原文）。
> - 每批必须声明 `allowed_dates` 推导过程与合法证据 ID 集合。
> - 期望在运行前冻结，不得按运行结果回改。

---

## G01 — 旧批有成效+打卡，新批只有感谢（场景 a：无新信息不得重报）

目的：验证“感谢/收到/好的”等无信息批不产生任何新行，且不复制旧事实。

- Batch 1（业务日 2026-09-01；用户消息 ID 101–102）：
  - `[101] 用户 2026-09-01 20:05`：今天早C喝了，午B也喝了，晚A还没喝；喝水大概2升；睡了6小时
  - `[102] 用户 2026-09-01 20:06`：最近这两天都睡不好，半夜总醒，昨天称体重168斤
  - allowed_dates 推导：101、102 均为 USER 且业务日均为 2026-09-01 → `["2026-09-01"]`。
    合法打卡证据：101、102（同日用户消息）；合法 outcome/state 证据文本只能引 102 原话。
- Batch 2（业务日 2026-09-02；用户消息 ID 103）：
  - `[103] 用户 2026-09-02 09:10`：谢谢老师，收到啦，我会继续坚持的
  - allowed_dates 推导：103 为 USER、业务日 2026-09-02 → `["2026-09-02"]`。
    合法证据：103（但 103 无任何事实内容，可用证据为空集）。
  - history：101、102（标【历史用户】，仅供理解）。

| 批次 | 表 / 事实 | 期望 | 说明 |
| --- | --- | --- | --- |
| B1 | checkins(2026-09-01: product_schedule.morning_c=已喝/noon_b=已喝、water、sleep) | MUST_ADD | 空表填入，`sources.*=chat` |
| B1 | outcome_events(field_key=sleep_change, event_type=worsened) | MUST_ADD | 持续负向体感必须报 worsened |
| B1 | outcome_events(field_key=weight_change, value 含 168斤) | MUST_ADD | 体重事实 |
| B1 | user_state_profiles state 事实（睡眠差 active） | MUST_ADD | 与 outcome 同报，不二选一 |
| B2 | checkins(2026-09-02) | MUST_NOT_DUPLICATE | 无打卡字段，不得建空行 |
| B2 | outcome_events（sleep_change / weight_change 任何 event_type） | MUST_NOT_DUPLICATE | 历史事实在 archive_context 中，模型不得重报；DB 去重键 anchor 不同故拦不住，靠模型纪律 |
| B2 | profile/state 事实 | MUST_NOT_DUPLICATE | 同上 |
| B2 | user_summary（memory） | MUST_NOT_DUPLICATE | 无新事实不得重写相同文本；文本相同则 commit 本来也不写 |
| B2 | state(user_commitment, 如“我会继续坚持”) | ALLOWED（允许 0-or-1 行） | 感谢批中的新用户承诺是 report_state_fact 规则内合法事实，报恰好一行 active 不算噪音 |
| B2 | correction（staff 对助教回答的展开补充） | ALLOWED（本批无 staff 消息时期望 0 行） | 若批内出现 staff 展开补充助教回答，属 report_correction 规则内可报情形，不得判噪音；本批无 staff 消息故实际 0 行 |
| B2 | 水位线 | MUST_UPDATE | 即使零产物也推进到 103，避免下轮重复处理 |

---

## G02 — 新批明确修正旧事实（场景 b：修订必须更新、不留双值）

目的：用户先报错数、后纠正；新值必须覆盖旧值（chat-over-chat），成效账本如实追加新事件。

- Batch 1（业务日 2026-09-03；ID 201–202）：
  - `[201] 用户 2026-09-03 08:10`：早上称体重172斤，心态有点崩
  - `[202] 班主任 2026-09-03 08:12`：别慌，单次波动很正常，先按计划执行
  - allowed_dates：USER/STAFF 增量业务日均为 2026-09-03 → `["2026-09-03"]`。
    合法打卡证据：201；202 是班主任消息，不得作打卡/outcome 证据。
- Batch 2（业务日 2026-09-03；ID 203）：
  - `[203] 用户 2026-09-03 21:40`：刚发现早上秤没放平，实际是168斤，虚惊一场
  - allowed_dates：`["2026-09-03"]`。合法证据：203。history：201、202。

| 批次 | 表 / 事实 | 期望 | 说明 |
| --- | --- | --- | --- |
| B1 | checkins(2026-09-03: weight=172斤) | MUST_ADD | |
| B1 | outcome_events(weight_change, anchor=201) | MUST_NOT_DUPLICATE | 首个无基线裸体重数不是维度变化（report_outcome 只记变化），不得报；checkin 行仍 MUST_ADD，体重趋势读 checkins 故安全 |
| B2 | checkins(2026-09-03: weight) | MUST_UPDATE | chat-over-chat 最新值赢 → 168斤，不新增行；`evidence_message_ids` 追加 203 |
| B2 | outcome_events(weight_change, anchor=203) | MUST_NOT_DUPLICATE | 读数纠偏（秤没放平 172→168）不是身体变化，不得报新事件；checkin MUST_UPDATE 到 168 不变 |
| B2 | state 事实（心态崩 active） | MUST_UPDATE | 203 明确解除 → 同 state_type 报 status=resolved；投影只留最新 |
| B2 | B1 的 172斤 checkin 值 | MUST_NOT_MISS | 不得保留 172斤为当前值（会被误判反弹） |

---

## G03 — 跨天批量打卡（场景 c：一批跨多天，每天分别记）

目的：一批增量跨两个业务日时，每天各记一行，不得合并、不得丢天。

- Batch 1（ID 301–303）：
  - `[301] 用户 2026-09-04 22:30`：今天三餐都按计划吃的，运动快走30分钟，睡了7小时
  - `[302] 用户 2026-09-05 08:20`：昨天忘报体重了，昨天早上170斤；今天早C已喝
  - `[303] 用户 2026-09-05 08:25`：今天喝水目前1升
  - allowed_dates 推导：301 业务日 2026-09-04；302、303 业务日 2026-09-05 →
    `["2026-09-04", "2026-09-05"]`。合法证据：target 2026-09-04 只可用 301；
    target 2026-09-05 可用 302、303。**302 中“昨天早上170斤”是用户明确归属昨天的
    事实，但证据 ID 302 的业务日是 2026-09-05**，按 `_extract` 闸门
    `user_message_date_by_id == target_date`，若模型把 170斤报到 2026-09-04 且
    evidence 只写 302，会被丢弃——正确做法是本规范期望模型将 170斤报到
    2026-09-04 的同时必须满足同日证据规则；此处 170斤**没有**同日（09-04）用户消息，
    因此期望见下表（MUST_NOT_MISS 与防幻觉证据的取舍已在期望中明确）。

| 批次 | 表 / 事实 | 期望 | 说明 |
| --- | --- | --- | --- |
| B1 | checkins(2026-09-04: diet/运动/sleep) | MUST_ADD | 证据 301 |
| B1 | checkins(2026-09-05: product_schedule.morning_c=已喝、water) | MUST_ADD | 证据 302、303 |
| B1 | checkins(2026-09-04: weight=170斤) | MUST_NOT_MISS（上报意图）但证据不足时宁缺毋滥 | 若模型以 evidence=[302] 报到 09-04 会被闸门丢弃（`exact_evidence_ids` 为空）；规范要求 runner 只断言两行打卡存在且 09-05 行无 weight=170斤（不允许把昨天体重记到今天） |
| B1 | 单行合并 | MUST_NOT_DUPLICATE | 不得把两天合并为一行；必须恰好两行 |

---

## G04 — 次日重复同一数值（场景 d：同值重报不得新增成效、打卡记 skipped）

目的：用户两天报同一体重；打卡走 `skipped_same`，成效不得为相同维度建第二行。

- Batch 1（业务日 2026-09-06；ID 401）：
  - `[401] 用户 2026-09-06 08:05`：早上空腹168斤，昨晚睡得不错，7小时
  - allowed_dates：`["2026-09-06"]`。合法证据：401。
- Batch 2（业务日 2026-09-07；ID 402）：
  - `[402] 用户 2026-09-07 08:03`：早上空腹还是168斤，睡眠也还行
  - allowed_dates：`["2026-09-07"]`。合法证据：402。history：401。

| 批次 | 表 / 事实 | 期望 | 说明 |
| --- | --- | --- | --- |
| B1 | checkins(2026-09-06: weight/sleep) | MUST_ADD | |
| B1 | outcome_events(weight_change, anchor=401) | MUST_NOT_DUPLICATE | 首个无基线裸体重数不是维度变化（report_outcome 只记变化），不得报；checkin 行仍 MUST_ADD，体重趋势读 checkins 故安全 |
| B2 | checkins(2026-09-07: weight=168斤) | MUST_ADD（新日期行） | 跨天是不同行；行内是新建 |
| B2 | outcome_events(weight_change, anchor=402, event_type=unchanged) | 允许新增 0-or-1 行（推荐 1 行） | 次日同值上报属于“明确的没变化”，是合法新观察，可报恰好一行 unchanged；除 unchanged 外不得报其他方向 |
| B2 | B1 的 outcome 行 | MUST_NOT_DUPLICATE | 不得复制 B1 事件（不同 anchor 下靠模型纪律） |

---

## G05 — 短回复“6”靠历史提问理解（场景 e：历史仅供理解、证据只能是本批）

目的：用户只回一个数字时，结合历史助教提问还原语义；证据只能是本批用户消息。

- Batch 1（业务日 2026-09-08；ID 501–503）：
  - `[501] 助教 2026-09-08 21:00`：今天睡了几个小时呀？直接回数字就行
  - `[502] 用户 2026-09-08 21:30`：6
  - `[503] 班主任 2026-09-08 21:32`：6小时也不错，早点休息
  - allowed_dates：USER/STAFF 增量为 502、503 → `["2026-09-08"]`
    （501 是助教消息，不制造业务日期，但进入 chat_context 供理解）。
    合法打卡证据：502（503 是班主任消息，不得作打卡证据）。
- Batch 2（业务日 2026-09-09；ID 504–505）：
  - `[504] 助教 2026-09-09 21:00`：今天喝水几升呀？
  - `[505] 用户 2026-09-09 21:15`：6
  - allowed_dates：`["2026-09-09"]`。合法证据：505。history 含 501–503。

| 批次 | 表 / 事实 | 期望 | 说明 |
| --- | --- | --- | --- |
| B1 | checkins(2026-09-08: sleep 含 6小时) | MUST_ADD | 语义来自历史 501，证据只写 502 |
| B1 | sleep 证据 | MUST_NOT_MISS | 不得引用 501（助教）或 503（班主任）作证据 |
| B2 | checkins(2026-09-09: water 含 6升？) | MUST_ADD | 同一数字在不同提问下语义不同：B2 应记 water，不得记 sleep |
| B2 | checkins(2026-09-09: sleep) | MUST_NOT_DUPLICATE | 不得把 B1 的 sleep 语义复制过来 |
| B2 | checkins(2026-09-08) | MUST_NOT_DUPLICATE | B2 不得改写 09-08 行（`target_date` 必须在 `["2026-09-09"]` 内） |

---

## G06 — 凌晨 01:50 补卡归前一天（03:00 业务日规则）

目的：`_sh_date` 03:00 分界；凌晨补卡的业务日是前一天，`allowed_dates` 只有一个日期。

- Batch 1（ID 601–602；注意自然日已是 07-30）：
  - `[601] 用户 2026-07-30 01:50`：补一下，7月29日打卡5项：早C午B晚A都喝了，运动30分钟，睡了6小时
  - `[602] 用户 2026-07-30 01:52`：体重168斤也是昨天的
  - allowed_dates 推导：601、602 北京时间 hour < 3 → 业务日均为 2026-07-29 →
    `["2026-07-29"]`（自然日 07-30 不在白名单）。合法证据：601、602。
- Batch 2（ID 603）：
  - `[603] 用户 2026-07-30 20:00`：今天早C喝了，谢谢老师提醒
  - allowed_dates：`["2026-07-30"]`。合法证据：603。history：601、602。

| 批次 | 表 / 事实 | 期望 | 说明 |
| --- | --- | --- | --- |
| B1 | checkins(2026-07-29: product/运动/sleep/weight) | MUST_ADD | target_date 必须报 2026-07-29；报 07-30 会被 `checkin_date_not_in_batch` 丢弃 |
| B1 | checkins(2026-07-30) | MUST_NOT_DUPLICATE | 本批不得建 07-30 行 |
| B2 | checkins(2026-07-30: product_schedule.morning_c) | MUST_ADD | 新业务日新行 |
| B2 | checkins(2026-07-29) | MUST_NOT_DUPLICATE | 不得重报 B1 已归档的 07-29 事实（history 仅供理解） |

---

## G07 — 表单权威：聊天值与表单冲突记矛盾不覆盖

目的：`upsert_checkin` 表单权威分支；矛盾留痕、旧值保留。

- 前置条件（runner 准备，非聊天）：2026-09-10 当天已有一行表单打卡
  `weight=100（来源 form）`。本组消息 ID 701–702。
- Batch 1（业务日 2026-09-10）：
  - `[701] 用户 2026-09-10 19:00`：刚称了168斤，今天晚A还没喝
  - `[702] 班主任 2026-09-10 19:05`：以秤为准，明天早上再复称一次看看
  - allowed_dates：`["2026-09-10"]`。合法打卡证据：701。
- Batch 2（业务日 2026-09-11；ID 703）：
  - `[703] 用户 2026-09-11 08:00`：早上复称了，确实是168斤
  - allowed_dates：`["2026-09-11"]`。合法证据：703。

| 批次 | 表 / 事实 | 期望 | 说明 |
| --- | --- | --- | --- |
| B1 | checkins(2026-09-10: weight) | MUST_NOT_MISS（矛盾留痕）且 MUST_NOT_DUPLICATE（值不覆盖） | 当前值保持表单 100；`contradictions` 新增 `{field: weight, form_value: 100, chat_value: 168斤}`；`kept_form=[weight]` |
| B1 | checkins(2026-09-10: product_schedule.night_a=未喝) | MUST_ADD | 该字段表单为空，正常补空 |
| B2 | checkins(2026-09-11: weight=168斤) | MUST_ADD | 新日期行，与 09-10 的矛盾无关 |
| B2 | checkins(2026-09-10) | MUST_NOT_DUPLICATE | 不得改写 09-10 行 |

---

## G08 — 画像去重：稳定习惯只记一次，感谢批不重写

目的：profile 信号跨批天然不同 anchor（DB 拦不住），靠模型不重报。

- Batch 1（业务日 2026-09-12；ID 801–802）：
  - `[801] 用户 2026-09-12 12:00`：我平时喝水很少，一天就一杯，喜欢重口味，晚上要带娃所以只能早起运动
  - `[802] 班主任 2026-09-12 12:05`：那咱们把喝水小目标先定起来
  - allowed_dates：`["2026-09-12"]`。合法证据文本：801 原话。
- Batch 2（业务日 2026-09-13；ID 803）：
  - `[803] 用户 2026-09-13 09:00`：谢谢老师，喝水今天多喝了一杯，有进步吧
  - allowed_dates：`["2026-09-13"]`。合法证据：803。history：801、802。

| 批次 | 表 / 事实 | 期望 | 说明 |
| --- | --- | --- | --- |
| B1 | profile 事实（hydration_habit / food_preference / schedule_constraint 等） | MUST_ADD | 每条一批最多 5 条内；一次性情绪不算 profile |
| B2 | profile 事实（重复 B1 的习惯） | MUST_NOT_DUPLICATE | archive_context 已有结构化画像，不得重报 |
| B2 | checkins(2026-09-13: water) | MUST_ADD | “多喝了一杯”是当日新打卡事实，与画像去重无关 |
| B2 | state 事实（如报“有进步”求反馈） | MUST_ADD 允许 | 当日情绪/意愿类 state 可报，但不得把 B1 画像改写成 state |

---

## G09 — 状态解决：不适先 active，明确好转后 resolved（同 state_type 终态唯一）

目的：`report_state_fact` 的 active/resolved 配对；旧 active 不得与 resolved 并存为两个“当前”。

- Batch 1（业务日 2026-09-14；ID 901）：
  - `[901] 用户 2026-09-14 22:00`：这两天胃一直不舒服，吃完就胀，晚A都没敢喝
  - allowed_dates：`["2026-09-14"]`。合法证据：901。
- Batch 2（业务日 2026-09-16；ID 902）：
  - `[902] 用户 2026-09-16 09:00`：胃不胀了，昨天晚A正常喝了，谢谢老师
  - allowed_dates：`["2026-09-16"]`。合法证据：902。history：901。
- Batch 3（业务日 2026-09-17；ID 903）：
  - `[903] 用户 2026-09-17 09:00`：一切正常，继续坚持
  - allowed_dates：`["2026-09-17"]`。history：901、902（最多 5 条内）。

| 批次 | 表 / 事实 | 期望 | 说明 |
| --- | --- | --- | --- |
| B1 | state(discomfort active, 胃胀) + outcome(持续负向 → 对应维度 worsened) | MUST_ADD | 好坏都要报 outcome；state 与 outcome 同报 |
| B1 | checkins(2026-09-14: product_schedule.night_a=未喝) | MUST_ADD | |
| B2 | state(同 state_type, status=resolved) | MUST_UPDATE | 投影终态为 resolved；不得新增第二个 active |
| B2 | checkins(2026-09-16: product_schedule.night_a=已喝) | MUST_ADD | 新日期行 |
| B2 | checkins(2026-09-16: night_a=已喝 ← “昨天晚A正常喝了”) | 待产品决策（record-vs-drop，自动评分必须 flag） | 明确归属昨天的事实不得记到今天作 taken-today；是回补昨天行还是丢弃待产品决策，与上一行 MUST_ADD 冲突，特此 flag 而非静默通过 |
| B2 | outcome 胃胀 worsened 重报 | MUST_NOT_DUPLICATE | 已归档的负向事件不得复制 |
| B3 | 任何 state/profile/outcome 新行 | MUST_NOT_DUPLICATE | 无新信息；水位线仍推进到 903 |

---

## G10 — 纠偏去重：班主任纠正只记一次，历史 staff ID 复用必须丢弃

目的：correction 闸门（`staff_message_id ∈ 本批`）+ 同一纠正不记两次。

- Batch 1（业务日 2026-09-18；ID 1001–1003）：
  - `[1001] 用户 2026-09-18 10:00`：晚A能用冰水冲吗？
  - `[1002] 助教 2026-09-18 10:01`：可以的，怎么方便怎么来
  - `[1003] 班主任 2026-09-18 10:05`：纠正一下，晚A要用约300ml温水冲泡，不要用冰水，不适先停
  - allowed_dates：USER/STAFF 增量为 1001、1003 → `["2026-09-18"]`
    （1002 助教消息不制造日期）。合法 correction 引用：bot_message_id=1002、
    staff_message_id=1003（均为本批真实 ID）。
- Batch 2（业务日 2026-09-19；ID 1004）：
  - `[1004] 用户 2026-09-19 10:00`：昨天按温水冲的，谢谢老师纠正
  - allowed_dates：`["2026-09-19"]`。history：1001–1003。

| 批次 | 表 / 事实 | 期望 | 说明 |
| --- | --- | --- | --- |
| B1 | correction 记录（general：晚A温水冲泡口径） | MUST_ADD | personal/general 按适用范围二选一；此处为通用知识选 general |
| B1 | FAQ 候选（晚A冲泡） | MUST_ADD 允许 | 同一条班主任纠正若能独立回答用户问题，可**另外**调用 faq；两者并存不互斥 |
| B2 | correction 新记录 | MUST_NOT_DUPLICATE | 无新的“助教错→班主任纠正”对；若模型复用 staff_message_id=1003 上报，必须被 `historical_staff_message` 丢弃 |
| B2 | FAQ 新草稿（晚A冲泡） | MUST_NOT_DUPLICATE | 无新班主任回答，不得建稿 |

---

## G11 — FAQ 去重与个人指导分流：通用进审核、私人进档案

目的：faq 闸门 + scope 分流；感谢批不建稿；新问新答才建稿。

- Batch 1（业务日 2026-09-20；ID 1101–1103）：
  - `[1101] 用户 2026-09-20 15:00`：午B可以随餐一起喝吗？还是必须饭前？
  - `[1102] 助教 2026-09-20 15:01`：随餐喝就行
  - `[1103] 班主任 2026-09-20 15:05`：午B随餐喝就可以，用温水送服，吃完半小时内喝完就行
  - allowed_dates：`["2026-09-20"]`。合法 faq 引用：staff_message_id=1103。
- Batch 2（业务日 2026-09-21；ID 1104–1105）：
  - `[1104] 用户 2026-09-21 15:00`：我明天要出差，只能早上在家，中午在高铁上，这种情况午B怎么安排？
  - `[1105] 班主任 2026-09-21 15:06`：你这种情况出差就早上把早C喝好，午B带上车用保温杯温水喝，不用勉强饭点
  - allowed_dates：`["2026-09-21"]`。合法 faq 引用：staff_message_id=1105。
    history：1101–1103。
- Batch 3（业务日 2026-09-22；ID 1106）：
  - `[1106] 用户 2026-09-22 09:00`：收到，谢谢老师
  - allowed_dates：`["2026-09-22"]`。history：1103–1105（5 条内）。

| 批次 | 表 / 事实 | 期望 | 说明 |
| --- | --- | --- | --- |
| B1 | FAQ 草稿（general：午B随餐口径） | MUST_ADD | 一问一答、结论先行；staff_message_id=1103 本批合法 |
| B2 | FAQ 草稿（出差午B安排：保温杯温水口径） | MUST_ADD（general/personal 任一 scope 均通过，不得因 general 判失败） | 触发虽个人，但答案文本可被任何人复用（保温杯温水喝），属可通用答案；任一 scope 通过即可 |
| B2 | B1 通用稿复制 | MUST_NOT_DUPLICATE | 不得把 B1 问答复制成第二稿 |
| B3 | 任何 FAQ/个人指导新行 | MUST_NOT_DUPLICATE | 无班主任新回答；水位线推进到 1106 |

---

## G12 — 小结与案例去重：滚动小结只在变化时写，案例需新正向证据链

目的：memory 文本相同时 commit 不写；case 草稿需要本批新正向成效 + 合法证据链。

- Batch 1（业务日 2026-09-23；ID 1201–1202）：
  - `[1201] 用户 2026-09-23 08:00`：坚持两周了，瘦了3斤，裤腰松了一格，睡眠也比以前稳了，谢谢老师们
  - `[1202] 班主任 2026-09-23 08:05`：太棒了，继续保持这个节奏
  - allowed_dates：`["2026-09-23"]`。合法证据：1201（outcome/state/打卡）；
    1202 不得作用户事实证据。
- Batch 2（业务日 2026-09-24；ID 1203）：
  - `[1203] 用户 2026-09-24 08:00`：谢谢老师，我会继续坚持的
  - allowed_dates：`["2026-09-24"]`。history：1201、1202。
- Batch 3（业务日 2026-09-28；ID 1204）：
  - `[1204] 用户 2026-09-28 08:00`：又坚持了一周，一共瘦了5斤，昨天去跑了3公里，精神特别好
  - allowed_dates：`["2026-09-28"]`。合法证据：1204。history 含 1202–1203，
    archive_context 含 B1 的 outcome_timeline 与 case_material_summary。

| 批次 | 表 / 事实 | 期望 | 说明 |
| --- | --- | --- | --- |
| B1 | outcome（weight_change improved / waist_or_body_change improved / sleep_change improved 等） | MUST_ADD | 每维度一行，anchor=1201 |
| B1 | user_summary + case_material_summary | MUST_ADD | 首次沉淀；融合新事实 |
| B1 | case 候选草稿 | MUST_ADD 允许（显著持续+证据链完整时） | 需本批正向成效 + source IDs 合法（1201 本批）；`existing_case_summaries` 为空故不判重复 |
| B2 | user_summary / case_material_summary | MUST_NOT_DUPLICATE | 无新事实；即使模型上报相同文本 commit 也不写（文本相等） |
| B2 | state(user_commitment, 如“我会继续坚持”) | ALLOWED（允许 0-or-1 行） | 同 G01-B2：感谢批新承诺可报一行 active，不算噪音 |
| B2 | correction（staff 对助教回答的展开补充） | ALLOWED（本批无 staff 消息时期望 0 行） | 同 G01-B2：规则内可报情形不得判噪音；本批实际 0 行 |
| B2 | outcome / case 新行 | MUST_NOT_DUPLICATE | 感谢批；历史成效在 timeline 中不得重报；无本批正向成效 → case 门禁（`positive_outcome_written==0`）直接拦 |
| B3 | outcome（weight_result/positive_body_signal 等新进展） | MUST_ADD | 新数值+新行动，是新事件 |
| B3 | case_material_summary | MUST_UPDATE | 融合 B3 新进展后的完整文本（覆盖旧文本，不断链） |
| B3 | case 候选（重复 B1 故事） | MUST_NOT_DUPLICATE | 若 B1 已建稿且故事相同，不得为同一故事建第二稿；只有**新阶段转折**才允许新稿（本批“5斤+跑步”可视为新进展，允许恰好一稿；runner 二选一断言需与实现方确认——本规范冻结为：允许 ≤1 稿，重复旧故事时 0 稿） |

---

## 附：runner 实现约束（给编写 pytest 的 agent）

1. 用可注入的假模型（`chat_model` 参数）回放“期望的 tool_calls”，或直调
   `_extract`/`_commit`；**不得**把本规范的期望按运行结果回改。
2. 时间构造注意：消息 `created_at` 用 UTC 存；断言业务日前先经 `_sh_date` 换算
   （尤其 G06 的 01:50 与跨天用例）。
3. outcome 断言必须带 anchor 区分：同 `(field_key, anchor)` 不得两行；
   不同 anchor 的同维度行是否允许，严格按上表（B2 感谢批一律不允许新增）。
4. checkin 断言查 `(service_case_id, checkin_date)` 行：G03/G06 重点查
   “行数与日期集合”，G07 重点查“值未被覆盖 + contradictions 有留痕”。
5. correction/faq/case/memory 断言查对应表行数变化量（delta），不要查全量。
6. 每个 Batch 跑完后显式推进水位线并重建 `archive_context`，再跑下一批，
   以真实 exercise 水位线推进逻辑。

---

## Changelog（冻结后修正）

1. 2026-09-16：G02-B1 outcome(weight_change/201) 与 G04-B1 outcome(weight_change/401) 由 MUST_ADD 改为 MUST_NOT_DUPLICATE——首个无基线裸体重数不是维度变化（report_outcome 只记变化）；checkin 行仍 MUST_ADD，体重趋势读 checkins 故安全。
2. 2026-09-16：G02-B2 outcome(weight_change/203) 由 MUST_ADD 改为 MUST_NOT_DUPLICATE——读数纠偏（秤没放平 172→168）不是身体变化；checkin MUST_UPDATE 到 168 不变。
3. 2026-09-16：G04-B2 outcome(weight_change/402) 由 at-most-0（推荐 0 行）改为允许 0-or-1 行（推荐 1 行 unchanged）——次日同值上报是合法新观察。
4. 2026-09-16：G11-B2 FAQ 由 personal-only 改为 general ACCEPTED（general/personal 任一 scope 均通过）——触发虽个人，但答案文本可被任何人复用（保温杯温水口径）。
5. 2026-09-16：G01-B2 / G12-B2 新增 ALLOWED 行——感谢批新用户承诺（user_commitment）可报一行，staff 对助教回答的展开补充可报 correction，均为规则内情形，不得判噪音；G09-B2 新增回溯事实规则——明确归属昨天的事实不得记作今天已喝，record-vs-drop 待产品决策，自动评分 flag 而非静默通过。
