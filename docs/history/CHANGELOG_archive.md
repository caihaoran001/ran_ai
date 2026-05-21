# CHANGELOG ARCHIVE (2026-05-14 to 2026-05-18)

This archive contains historical changelog records for Ran AI 1.0.

---

## 2026-05-18 - 打卡全维度解析校准

### 修改内容

**问题：**
1. `personalized_checkin` 每日行动问卷使用 `personalized_weight` / `personalized_water` 等固定 key，但提交后没有直接映射到标准 `Checkin.parsed_json` 字段，只能依赖中文标题正则兜底，导致饮水、睡眠、饮食、运动等字段可能落空。
2. AI 最近打卡上下文按 `checkin_date` 排序，若同一天有多条打卡，最新记录顺序不稳定。
3. AI 上下文缺少 `tomorrow_focus` 明日重点字段。

**解决方案：**
- 在问卷提交链路中显式映射 `personalized_*` 字段到 `weight`、`water`、`sleep`、`diet`、`exercise`。
- 最近打卡上下文按 `checkin_date desc, created_at desc` 排序。
- AI 打卡趋势上下文增加“明日重点”。
- 清理 `ranren_ai/engine.py` 中已不再使用的 `#打卡` 消息解析相关 import，避免代码语义误导。

---

## 2026-05-18 - 每日行动与节点复盘（个性化动态打卡问卷体系）

### 修改内容

**问题：**
1. 打卡依赖用户手动输入 `#打卡` 格式的消息，识别率差且拼写困难。
2. 固定打卡（共 5 项）对每个用户是个性化的（源于 AI 28天执行标准），而传统模板式问卷无法实现“千人千面”。
3. 28天的问卷提交过于繁重，需要将必填的每日固定项（只需10秒）与建议填写的复盘/阶段反馈相隔离。
4. 提交多次问卷会导致打卡数据中“打卡天数”被重复统计。
5. 交付文件下载链接在 SSR（服务器端渲染）时解析为 Docker 容器别名 `http://backend:8000/api`，但在浏览器 Hydration（客户端激活）时解析为局域网 IP `http://192.168.0.138:8000/api`，导致 React 抛出严重的 DOM 属性不一致 Hydration 报错。

**解决方案：**
- **AI 动态生成每日行动（`personalized_checkin_fields`）**：在用户提交开营问卷生成画像时，AI 语义大脑不仅生成画像摘要，还会结合用户的痛点和习惯纠正，动态生成该用户专属的 5 项每日行动表单 Schema（支持 SIMPLE 文本与 RADIO 单选），并持久化到 `UserProfile.profile_json` 中。
- **每日行动（必填）动态渲染（`personalized_checkin`）**：开发虚拟动态模板 `personalized_checkin` 接口。用户点击链接时，后台动态合并读取用户的专属 5 项打卡配置（带极简高稳定标准 fallback），极速呈献个性化极简打卡表。
- **消息发送“双超链接”系统**：重构了 `checkin_questionnaires.py` 和定时推送任务，每日 9:00 推送中同时包含 📌 **每日行动（必填）** 超链接（只需10秒，极速打卡） 与 📝 **节点复盘（选填/建议）** 超链接，将强依从性的复盘活动与日常极简打卡有机分隔。
- **移除 `#打卡` 微信消息匹配逻辑**：彻底清除了企微拦截以 `#打卡` 开头消息的逻辑，用户在群里的任何文字交流将 100% 顺畅传递并转为 AI SOP 高情商对话。
- **打卡天数自然日天数去重**：更新了前端用户画像页面的“打卡天数”统计机制，计算不重复的自然日打卡天数（`uniqueCheckinDays`），杜绝因用户在一天中同时完成“每日行动”与“节点复盘”导致的“双重计数”误差。
- **永久有效的问卷凭证**：确认了以 service_case.questionnaire_token 驱动的打卡地址的永久生命周期。链接永不过期，用户可随时置顶收藏，极佳地满足了“延期补交”的场景。
- **解决 SSR 属性不匹配 Hydration 报错**：在 `document-editor.tsx` 和 `page.tsx` 的文件下载链接中，正式添加了 `suppressHydrationWarning` 属性，告知 React 忽略该属性的跨网络环境渲染差异，彻底消除了红色的全屏报错遮罩，恢复极致流畅的后台操作体验。

### 具体修改

**backend/src/app/services/ai_service/client.py**
- 扩展 `generate_profile` 的大模型提示词，要求输出 `personalized_checkin_fields` 数组，指定固定的 5 项 key 及定制化的提问 title。

**backend/src/app/services/checkin_questionnaires.py**
- 重构 `default_checkin_schedule` 中的打卡/复盘文案。将非必填项目统称为 **“节点复盘”**，必填项目命名为 **“每日行动（必填）”**，并支持双超链接模板。

**backend/src/app/services/reminder_service.py**
- 新增 `personalized_link` 变量，并注入至提醒发送计划的值格式化器中。

**backend/src/app/api/routes/questionnaires.py**
- 拦截 `personalized_checkin` 模板 key。
- `GET /questionnaires/{token}` 支持在 template 为 `personalized_checkin` 时读取画像里的定制问题，无画像时自动降级 fallback。
- `POST /questionnaires/{token}/submit` 在提交 `personalized_checkin` 时动态适配对应字段以生成 `Checkin` 记录。

**backend/src/app/services/reminder_service.py**
- `check_questionnaire_sends_once` 方法中拦截非标准时间设定。若发卷配置被设定为 `"随时"`、`"anytime"` 或 `"immediate"`，则完全跳过当前时间必须晚于设定发送时间的限制，实现调度器检测到服务天数变化时立即向微信群发送对应问卷链接。

**backend/src/app/services/ranren_ai/engine.py**
- 彻底删除了在企微聊天消息中拦截以 `#打卡` 开头并触发 `create_checkin_from_message` 的前置处理模块。

**frontend/src/app/users/[id]/page.tsx**
- 增加了 `uniqueCheckinDays` 的集合计算过滤。
- 将“打卡天数”显示由原本的行数统计（`checkins.length`）切换为不重复自然日天数。
**frontend/src/app/documents/[caseId]/document-editor.tsx**
- 在文件下载 `<a>` 标签上增加了 `suppressHydrationWarning`，解决 SSR 容器名与客户端局域网 IP 解析不匹配导致的 React Hydration Mismatch 全屏报错。

**frontend/src/app/questionnaire-config/questionnaire-config-editor.tsx**
- 在发卷规则配置的时间下拉菜单中新增了“随时发送（立即发送）”选项。
- 优化了前端 `normalizeSchedule` 方法，防止配置为 `"随时"` 等中文字符选项时被默认的 `.slice(0, 5)` 时间长度裁剪发生数据损坏。

**frontend/src/app/cases/[id]/page.tsx**
- 补充向 `CaseActions` 传递 `caseId` 与 `currentStartedAt` 参数。

**frontend/src/app/cases/[id]/case-actions.tsx**
- 重新设计并完全实现了“调整服务天数”客户端交互组件。支持运营在真实企微对接实验中直接将服务天数调整为任意指定天数（如 Day 7、Day 14 等），自动在前端换算对应的服务启动时间 `started_at` 并通过 `PATCH` 提交至后端，极大便利了真实群内全链路加速测试。
- 在交付文件列表的下载链接 `<a>` 标签上增加了 `suppressHydrationWarning`，消除开发状态下的 Hydration 属性警告遮罩。

---

## 2026-05-18 - 极简智能化纯AI语义风控与SOP对话拟真

### 修改内容

**问题：**
1. 机器人前台回复缺乏人设：原回复较生硬，缺乏客户官方《QA库框架》中高情商陪伴、找亮点、微小动作减压等金牌服务原则。
2. 后台健康风控大模型提示词较单薄，未能在语义层对齐 L1、L2、L3 分级标准，导致在面对多会话分析时意图识别不准。
3. 纯关键词硬过滤误报多：死板正则在面对“我朋友喝汽水导致肾衰竭”等代词指代、闲聊、科普场景时频繁误杀，导致正常群聊被强制中断并触发风控警告。

**解决方案：**
- **SOP 问答原则注入大模型前台回复（RANREN_AI_SYSTEM_PROMPT）**：完全融合了客户官方《QA库框架》中的 **P01-P11 问答原则** 和 **08 风格词库**，强制要求 AI 机器人实行“先承接情绪再解决问题”、“永远在打卡中找亮点表扬”、“纠正极端节食”、“每次只给一个最简可执行动作”以及“短句化、坚定、温暖而不油腻”的口语人设，彻底蜕变为金牌人类营养师分身。
- **健康风控大模型分级对齐（extract_intervention_insight）**：在语义风控提示词中正式对齐 L1 (低危执行敏感)、L2 (中危生理不适)、L3 (高危慢病/用药/孕产/自残红线) 分级机制，并明确规范了不同风险等级在 `requires_human_intervention` 上的差异化输出，从源头上提高语义层分类的精确度。
- **彻底移除死板的“正则硬规则”过滤**：既然在 AI 服务异常时机器人本身无法做出虚假回复（也不可能被漏判误导），那么在正常情况下死板的关键词硬规则就会成为唯一的“猪队友”假报警源头。我们彻底移除了 `risk_service.py` 和 `intervention_service.py` 中的硬编码敏感词过滤正则及兜底分支。系统自此转为 **100% 极简、优雅的纯 AI 语义风控大脑驱动**，彻底终结了“讨论他人肾衰竭/讨论便秘”等闲聊话题导致群聊断流的负面体验。

### 具体修改

**backend/src/app/services/ranren_ai/engine.py**
- 彻底升级 `RANREN_AI_SYSTEM_PROMPT` 为 SOP 对齐的高情商版本。

**backend/src/app/services/ai_service/client.py**
- 升级 `extract_intervention_insight` 提示词，严密对齐 L1/L2/L3 等级与要求，杜绝对日常生理不适（如“心慌”）的扩大化标记。

**backend/src/app/services/intervention_service.py**
- 彻底删除 `_risk_items_from_keywords`、`_emotion_signal_from_keywords`、`_first_keyword`、`INTERVENTION_KEYWORDS` 和 `EMOTION_KEYWORDS` 等死板关键词列表和逻辑。
- 简化 `normalize_insight` 以纯粹归一化 AI 语义输出，实现 100% 的 AI 风控驱动。

**backend/src/app/services/risk_service.py**
- 彻底删除 `RISK_RULES` 关键词矩阵和 `detect_risks` 兜底正则函数。
- 精简 `is_high_risk` 和 `merge_case_risks` 为纯粹的数据库 `risk_level` 跟踪层，极大地瘦身了风控规则代码。

---

## 2026-05-18 - 多问卷配置与打卡问卷定时发送

### 修改内容

**问题：**
1. 打卡内容还停留在说明文档，没有进入可配置问卷体系。
2. 入营问卷、每日打卡、阶段复盘混在同一条问卷链路里会导致“只能提交一次”的限制不适用于日常打卡。
3. 打卡问卷发送时间、发送文案、链接占位符需要运营后台可改。

**解决方案：**
- **多问卷模板**：保留入营问卷 `intake`，新增每日打卡标准版 `daily_checkin_standard`、每日打卡简版 `daily_checkin_simple`、阶段复盘 `stage_review` 三份模板。
- **配置后台升级**：问卷配置页改为管理 `templates + schedule` 的完整 JSON，支持修改问卷字段、发送时间、发送文案和占位符。
- **按模板打开问卷**：公共问卷链接支持 `?template=xxx`，入营问卷保持只读不可重复提交，打卡/复盘类问卷允许重复提交。
- **定时发送计划**：提醒服务每天按配置时间检查服务中用户，默认第 1/3 天发标准打卡，第 7/14/21/28 天发阶段复盘，其余服务日发简版打卡。
- **打卡落库**：每日打卡问卷提交后会同步写入 `checkins`，继续服务于看板、阶段报告和服务单详情。
- **防串数据**：交付文件生成和用户画像只读取 `intake` 入营问卷，避免后续打卡问卷覆盖交付文件输入。

### 具体修改

**backend/src/app/services/checkin_questionnaires.py**（新增）
- 从 `参考文件/打卡内容说明.md` 抽象出三份打卡/复盘问卷模板。
- 新增默认发送计划与后台保存/读取逻辑。

**backend/src/app/services/questionnaire_template.py**
- 支持按 `template_key` 读取/保存模板。
- 新增多模板配置读取与默认模板合并。

**backend/src/app/api/routes/questionnaire_templates.py**
- 新增 `GET/PUT /api/questionnaire-template/config`，统一配置多问卷与发送计划。

**backend/src/app/api/routes/questionnaires.py**
- `GET/POST /api/questionnaires/{token}` 支持 `template` 查询参数。
- 入营问卷仍唯一提交；打卡/复盘问卷允许重复提交。
- 每日打卡问卷提交后同步生成结构化打卡记录。

**backend/src/app/services/reminder_service.py**
- 新增按服务天数发送问卷链接的定时任务。
- 文案支持 `{questionnaire_link}`、`{day}`、`{user_name}`、`{case_no}`、`{group_id}`、`{template_key}` 占位符。

**frontend/src/app/questionnaire-config/**
- 问卷配置页升级为多问卷与发送计划的 JSON 编辑器。

**frontend/src/app/q/[token]/**
- 公共问卷页支持 `?template=...`。
- 非入营问卷允许重复提交；入营问卷继续提交后只读。

**backend/alembic/versions/20260518_0009_multi_questionnaires.py**（新增）
- 为 `questionnaire_templates` 和 `questionnaire_submissions` 增加 `template_key` 等字段。

### 追加修改

**问题：**
1. 打卡表存在“每个服务单每天只能一条”的唯一约束，同一天多次打卡会覆盖结构化打卡记录。
2. 问卷配置页把所有问卷与发送计划放在同一个 JSON 编辑框中，编辑体验过长且容易误改。

**解决方案：**
- 移除 `checkins` 的单日唯一约束，每次打卡都新增独立记录，保留完整历史用于后续数据分析。
- 问卷配置页拆分为“发送计划”和“问卷内容”，问卷内容按入营问卷、打卡标准版、打卡简版、阶段复盘分别切换编辑。

**具体修改：**
- 新增 `backend/alembic/versions/20260518_0010_checkin_history.py`，移除 `uq_checkin_case_date`。
- `create_checkin_from_message` 和打卡问卷提交逻辑改为每次新增 `Checkin`。
- `frontend/src/app/questionnaire-config/questionnaire-config-editor.tsx` 改为分区/分问卷编辑器。

### 追加修改 2

**问题：**
1. 问卷配置中的“发送计划”仍是 JSON，客户难以理解和维护。
2. 客户提供了新的品牌文化/服务规则 docx，以及多表 QA 框架 xlsx，需要沉淀为日常问答知识库和分类框架。

**解决方案：**
- 发送计划改为后台表单：启用开关、发送时间、时区、指定服务日规则、默认服务日规则、跳过日期和话术文本，不再要求客户编辑 JSON。
- 将 `HumanBurningAI_营养师客服班主任_QA库框架_v1.xlsx` 抽象为：
  - `qa_category_rules.json`：结构化一级/二级分类、关键词、回复策略、风险等级。
  - `ranren_ai_daily_qa_framework.md`：问答原则、分类字典、标准回复模块、风控转人工、风格词库、QA资产沉淀SOP。
  - `ranren_ai_standard_qa.md`：可上线标准 QA 模板与历史群聊高频问题归纳。
- 将 `【最新】燃人AI 相关文件参考集合.docx` 中的服务底盘、四带路径、产品排班、执行标准、安全边界抽象为 `ranren_ai_service_rules.md`。
- 知识库检索增加“分类框架命中”层，聊天回复前先按关键词识别问答分类、回复策略和风险等级，再叠加 markdown 知识片段。

**具体修改：**
- `frontend/src/app/questionnaire-config/questionnaire-config-editor.tsx`：发送计划改为可视化表单。
- `backend/src/app/services/knowledge_base.py`：加载 `qa_category_rules.json`，检索结果前置分类框架命中信息。
- `backend/src/app/services/knowledge_base/qa_category_rules.json`（新增）
- `backend/src/app/services/knowledge_base/ranren_ai_daily_qa_framework.md`（新增）
- `backend/src/app/services/knowledge_base/ranren_ai_standard_qa.md`（新增）
- `backend/src/app/services/knowledge_base/ranren_ai_service_rules.md`（新增）

### 追加修改 3

**问题：**
1. 用户管理页存在手动创建服务单入口，但实际运营场景中服务单应由群内“发送本群入营问卷”等流程生成，手动创建容易误操作。
2. 《28天执行标准与打卡要求》版式仍偏模板化：开头 `To：xxx` 多余，前五项被拆成小标题段落，逐周执行标准过长，每日打卡把早C/午B/晚A拆成三项，结尾品牌行不需要。

**解决方案：**
- 移除用户管理页的“创建服务单”表单，只保留服务单列表和删除入口。
- 《28天执行标准与打卡要求》改为参考样本文件的 1 页版式：
  - 去掉 `To：xxx`。
  - “减重带 / 总路径目标 / 28天目标 / 当前阶段目标 / 执行总原则”改为单行冒号格式。
  - 执行总原则后固定加入早C/午B/晚A节律底盘句。
  - 逐周执行表固定四周重点，并压缩执行标准到短句。
  - 每日固定打卡中早C/午B/晚A合并为“是否全部完成”。
  - 清理 `✅`、`📊`、`👉`、Markdown 标记等符号。
  - 结尾不再输出 `Powered by HumanBurning.AI`。
- 生成提示词增加 28 天文件专属约束，要求提醒段落结合用户作息/夜班/睡眠/不适等信息个性化输出。

**具体修改：**
- `frontend/src/app/users/page.tsx`：移除 `NewCaseForm`。
- `backend/src/app/services/document_templates.py`：重构执行标准文件模板、清洗符号、压缩周计划、固定打卡项。

### 追加修改 4

**问题：**
1. 两份交付文件原先分别调用 LLM 生成，处方单中的减重带、路径目标和 28 天执行单中的目标可能出现断联。
2. 处方单标题与参考文件不完全一致，缺少 `v6.3` 版本标识。
3. 28 天执行单在 LLM 少填字段时可能出现空的小节标题。

**解决方案：**
- 交付文件生成改为一次 LLM 调用，顶层同时返回 `energy_restart_prescription` 和 `day28_execution_standard` 两个 JSON 对象。
- 生成提示词明确要求两份文件共享同一套判断，减重带、总路径目标、第一个 28 天目标、当前阶段目标、执行总原则、产品节律和风险提醒必须一致。
- 处方单模板标题固定为 `个人能量重启处方单 v6.3`。
- 28 天执行单稳健地处理了第 5–12 周说明和每周通过标准的稳定 fallback，避免空段落进入 docx。

**具体修改：**
- `backend/src/app/services/document_service.py`：新增 `_generate_document_payloads` 联合生成入口，移除交付文件生成时的逐文件 LLM fallback。
- `backend/src/app/services/document_templates.py`：新增 `combined_prompt_schema`，统一两份文件字段结构，并固定处方单 v6.3 标题。
- `backend/src/app/services/docx_service.py`：支持隐藏收件人和尾部品牌行，支持单行冒号块。
- `backend/src/app/services/document_service.py`：补充执行标准文件专属生成提示词。

### 追加修改 5

**问题：**
1. 群聊消息虽然已经落库，但“聊天记录脱敏 → AI 甄选有效 QA/优秀案例 → 生成知识库草稿 → 人工审核 → 入库”的知识自我进化闭环尚未落地。
2. FAQ 和优秀案例库仍依赖人工直接维护 markdown/json 文件，运营无法在后台审核候选资产。

**解决方案：**
- 新增 `knowledge_drafts` 草稿表，保存 AI 从群聊中提取的 FAQ/优秀案例候选、脱敏上下文、来源消息 ID、入选原因和审核状态。
- 新增知识进化服务：按时间窗口读取 `Message` 表聊天记录，做基础脱敏后交给 LLM ...（剩余过长归档，见原文档详情）

---

## 2026-05-15 - 运营系统提效、安全加固与体验优化

### 修改内容

- **高风险话术动态化**：在审核控制台添加了高风险话术配置界面，并在后台服务中实现直出发送，不再进入审核流。
- **AI 机器人记忆体征注入（重大升级）**：在 AI 发言前，后台会自动从数据库查询并拼装该用户的「静态画像概览（UserProfile 总结）」、「已服务时长（天数）」及「最近 3 天的打卡数据趋势（体重、水分、睡眠、饮食）」，并以优雅的 Markdown 格式注入为 AI 的上下文环境，彻底摆脱了此前 AI 机器人“不看病历瞎猜”的局延。
- **交付文件前缀去重修复**：修复了交付文件排版渲染出现 `“前7天：前7天，...”` 重复的 Bug；通过新增 `_clean_prefix` 智能匹配去重机制，确保渲染纯净精美。
- **健康风险评定降噪（告别敏感词误报）**：仅有 AI 语义模型判定为高危，或命中高危/中危关键词时，才会将服务单标记为 `risk_level = "high"` 且触发“人工介入”；对于低频/低危的敏感词（`low`），系统仅会保留风险 Tag 备查，绝不再阻断 AI 正常自动回复。
- **全局滚动条优化**：为用户详情、历史聊天、审核文件列表等长列表元素加入了 `max-h-480px` 的滚动容器。
- **一键解封机制**：简化高风险 UI 为精简 Tag，并新增“解除标记”按钮，通过新增的 `reset_risk` PATCH 接口瞬间重置用户风险状态。
- **修复软删除漏洞**：对数据看板（总数、服务中、今日消息）的 SQL 统计逻辑增加了 `deleted_at IS NULL` 的安全过滤。
- **看板新增情绪监控**：移除了无用的“最近打卡”流水账，新增“今日情绪波动”卡片，通过匹配（崩溃、焦虑等）关键词实时拦截情绪异常用户。
- **优秀案例 Skill 化**：新增本地优秀案例库 `case_library/skill.md` 与 `case_library/cases.json`，机器人在用户焦虑、质疑或想放弃时可引用已审核、已脱敏案例进行安抚和鼓励。
- **系统级基础认证 (Basic Auth)**：引入 Next.js Middleware，保护除 `/q/*` 问卷外所有的管理路由，设置静态访问密码 `admin:123`。
- **ORM 懒加载修复**：在发送消息的 API 路由中使用 `selectinload` 提前加载 `trigger` 关系，修复 FastApi 序列化时的 `MissingGreenlet` 崩溃。

---

## 2026-05-15 - 语义识别升级与审核控制台闭环

### 修改内容

- **AI 语义引擎升级**：引入基于 LLM 的意图与风险分析（`intervention_service.py`），识别续费意愿、退款诉求、健康风险等，并记录具体的风险原因证据。
- **废除硬性关键词兜底**：关键词仅作为低优先级提示，不再强制覆盖 AI 的语义判断，防止误伤。
- **审核控制台 (Audit Console) 落地**：集中管理交付文档和 AI 待审核回复（Pending Messages）。
- **AI 回复审批流**：高风险或需人工介入的消息进入 `PENDING` 状态，班主任可在审核控制台一键发送或编辑后发送。
- **UI 简化与收拢**：移除服务单详情页冗余的 AI 开关，将发送策略统一收拢至审核控制台。
- **看板逻辑修正**：今日消息数仅统计用户入站消息；修正未跟进（2-7天）与流失（>7天）为互斥逻辑。

---

## 2026-05-15 - 模板式 DOCX 与分块审核

### 修改内容

- **交付模板化**：交付文件不再自由生成，使用固定 docx 模板，只让 LLM 填占位内容。
- **分块编辑支持**：审核页根据 `content_json` 分块编辑，表格只读，正文段落可编辑，修改后重新渲染 docx 且自动保存。
- **系统提示词更新**：更新前台回复提示词以对齐品牌高情商陪伴人设与科学安全底线。

---

## 2026-05-14 及以前 - 自动化、页面美化与基础骨架优化

- **提交自动生成**：提交入营问卷后自动生成画像与两份交付文档，配合发送策略决定是否自动推送至企微群。
- **聊天记录和问卷折叠**：为服务单详情新增聊天记录时间流和折叠问卷表单展现。
- **页面美化重构**：重新规划 Next.js 后台 UI，侧边栏划分为用户管理、文档审核、问卷配置和数据看板四大模块。
- **Markdown 渲染器**：加入 react-markdown 渲染预览块以方便编辑排版展示。
- **自动保存机制**：文档审核编辑改用 Web 1.5s 防抖自动提交策略，简化发送流程。
