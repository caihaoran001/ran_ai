# CHANGELOG

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

---

## 2026-05-15 - 运营系统提效、安全加固与体验优化

### 修改内容

**问题：**
1. 高风险固定话术硬编码，无法通过管理后台动态修改，且固定话术错误地进入了审核队列。
2. 页面元素过多导致无限拉长，阅读体验差（问卷结果、历史聊天过长）。
3. 风险标记过于冗长，且发生误判时无法通过界面进行一键重置。
4. 看板统计数据出现“幽灵”数据（软删除的用户依然被统计进去）。
5. 数据看板功能繁杂，缺乏对用户情绪失控的监控。
6. 安全漏洞：局域网或公网用户可通过修改问卷 URL 直接进入系统后台。
7. 后台发送/编辑审核消息时出现 SQLAlchemy `MissingGreenlet` 的异步加载崩溃。

**解决方案：**
- **高风险话术动态化**：在审核控制台添加了高风险话术配置界面，并在后台服务中实现直出发送，不再进入审核流。
- **AI 机器人记忆体征注入（重大升级）**：在 AI 发言前，后台会自动从数据库查询并拼装该用户的「静态画像概览（UserProfile 总结）」、「已服务时长（天数）」及「最近 3 天的打卡数据趋势（体重、水分、睡眠、饮食）」，并以优雅的 Markdown 格式注入为 AI 的上下文环境，彻底摆脱了此前 AI 机器人“不看病历瞎猜”的局延。
- **交付文件前缀去重修复**：修复了交付文件（《个人能量重启处方单》等）中由于 AI 生成字段时自带“前7天”、“早C”等前缀，与模版自带前缀叠加导致排版渲染出现 `“前7天：前7天，...”` 重复的 Bug；通过新增 `_clean_prefix` 智能匹配去重机制，确保渲染纯净精美。
- **健康风险评定降噪（告别敏感词误报）**：修复了此前问卷或日常消息中，用户一旦提到“稍微容易心慌”等非病理性词汇，就会因命中“心慌”敏感词而被粗暴误判为全局“高风险”并切断 AI 自动回复的 Bug。现在系统采用了**分级风控合并机制**：仅有 AI 语义模型判定为高危，或命中高危/中危关键词时，才会将服务单标记为 `risk_level = "high"` 且触发“人工介入”；对于低频/低危的敏感词（`low`），系统仅会保留风险 Tag 备查，绝不再阻断 AI 正常自动回复。
- **全局滚动条优化**：为用户详情、历史聊天、审核文件列表等长列表元素加入了 `max-h-480px` 的滚动容器。
- **一键解封机制**：简化高风险 UI 为精简 Tag，并新增“解除标记”按钮，通过新增的 `reset_risk` PATCH 接口瞬间重置用户风险状态。
- **修复软删除漏洞**：对数据看板（总数、服务中、今日消息）的 SQL 统计逻辑增加了 `deleted_at IS NULL` 的安全过滤。
- **看板新增情绪监控**：移除了无用的“最近打卡”流水账，新增“今日情绪波动”卡片，通过匹配（崩溃、焦虑等）关键词实时拦截情绪异常用户。
- **情绪预警语义化**：将“今日情绪波动”优化为“今日情绪预警”，优先读取 `messages.analysis_json.emotion_signal` 的 LLM 识别结果；仅对旧消息保留关键词兜底。
- **优秀案例 Skill 化**：新增本地优秀案例库 `case_library/skill.md` 与 `case_library/cases.json`，机器人在用户焦虑、质疑或想放弃时可引用已审核、已脱敏案例进行安抚和鼓励。
- **多句上下文识别**：入站消息意图/情绪识别会带上最近 5 条用户消息，支持识别“体重没动 → 不知道有没有用 → 算了吧”这类多句累积情绪。
- **系统级基础认证 (Basic Auth)**：引入 Next.js Middleware，保护除 `/q/*` 问卷外所有的管理路由，设置静态访问密码 `admin:123` 解决“被偷家”的安全隐患。
- **ORM 懒加载修复**：在发送消息的 API 路由中使用 `selectinload` 提前加载 `trigger` 关系，修复 FastApi 序列化时的 `MissingGreenlet` 崩溃。

### 具体修改

**backend/src/app/services/ranren_ai/engine.py & content_policy.py**
- 修改了高风险拦截策略，读取配置库并在触发时自动赋值 `should_send = True` 直接发出回复。

**backend/src/app/api/routes/dashboard.py**
- 增加 `deleted_at IS NULL` 数据隔离。
- 加入 `emotional_users_today` 查询分析统计。
- `emotional_users_today` 改为基于结构化 `emotion_signal` 统计，避免看板单独维护一套硬编码情绪判断。

**backend/src/app/services/intervention_service.py**
- 新增 `emotion_signal` 归一化输出，区分 low / medium / high 情绪信号；高强度情绪信号会进入人工介入原因。
- LLM 失败或历史消息场景下，规则兜底也会结合 recent_user_messages。

**backend/src/app/services/ai_service/client.py**
- 意图识别提示词增加 `emotion_signal` 输出要求。
- 意图识别提示词明确要求结合 `recent_user_messages` 判断多句累积表达。

**backend/src/app/services/case_library.py**（新增）
- 读取本地 Skill 文档和已审核案例，按当前消息、语义识别结果和最近用户消息挑选可引用案例。

**backend/src/app/services/case_library/skill.md**（新增）
- 定义优秀案例引用原则：先共情、再鼓励、只引用已审核脱敏案例、不承诺疗效、不编造案例。

**backend/src/app/services/case_library/cases.json**（新增）
- 存放可人工维护的优秀案例素材。

**backend/src/app/services/ranren_ai/engine.py**
- 意图识别和 AI 回复生成均接入最近用户消息上下文。
- AI 回复生成接入优秀案例库素材，焦虑/质疑/想放弃时可用案例安抚。

**backend/src/app/api/routes/service_cases.py**
- 扩展 `ServiceCaseUpdate`，支持传入 `reset_risk: True` 来清空高风险和人工介入标志。

**backend/src/app/api/routes/messages.py**
- 将 `session.get()` 替换为带 `options(selectinload(Message.trigger))` 的联合查询。

**frontend/src/middleware.ts**
- 新增安全拦截器，利用 HTTP Basic Auth 实现全局保护并白名单放行 `/q/` 问卷体系。

**frontend/src/app/cases/[id]/...**
- 引入了 `CaseRiskReset` 按钮组件。
- 大面积清理界面冗余信息，引入 `max-h` 和 `overflow-y-auto` 容器。

**frontend/src/app/dashboard/page.tsx**
- 移除了底部打卡流水列表。
- 新增了“今日情绪波动”统计卡片，修正了所有 StatCard 的跳转链接。

---

## 2026-05-15 - 语义识别升级与审核控制台闭环

### 修改内容

**问题：**
1. 风险识别过于依赖死板的关键词匹配，导致误报（如：夸奖产品好却因包含“退款”被标为高风险）。
2. AI 机器人的聊天回复缺乏人工审核出口，高风险回答无法拦截。
3. 填表信息的风险识别与聊天逻辑不统一。
4. 数据看板消息统计包含机器人回复，不符合运营实际关注指标；且未跟进/流失统计存在包含关系，逻辑不互斥。
5. 前端界面中 AI 回复开关冗余，审核入口分散。

**解决方案：**
- **AI 语义引擎升级**：引入基于 LLM 的意图与风险分析（`intervention_service.py`），识别续费意愿、退款诉求、健康风险等，并记录具体的风险原因证据。
- **废除硬性关键词兜底**：关键词仅作为低优先级提示，不再强制覆盖 AI 的语义判断，防止误伤。
- **审核控制台 (Audit Console) 落地**：将“文档审核”更名为“审核控制台”，集中管理交付文档和 AI 待审核回复（Pending Messages）。
- **AI 回复审批流**：高风险或需人工介入的消息进入 `PENDING` 状态，班主任可在审核控制台一键发送或编辑后发送。
- **UI 简化与收拢**：移除服务单详情页冗余的 AI 开关，将发送策略统一收拢至审核控制台。
- **看板逻辑修正**：今日消息数仅统计用户入站消息；修正未跟进（2-7天）与流失（>7天）为互斥逻辑。
- **用户画像增强**：在详情页展示 AI 识别的具体风险原因和续购/退款意图证据。

### 具体修改

**backend/src/app/services/intervention_service.py**
- 重构识别逻辑，优先使用 AI 语义分析结果。
- 修正关键词逻辑，仅在 AI 未能给出明确结论且命中高危词时作为低优先级提示。

**backend/src/app/api/routes/dashboard.py**
- `messages_today` 修正为仅统计 `direction=INBOUND` 且 `sender_type=USER` 的消息。
- 修正 `unfollowed_users` 和 `lost_users` 的时间区间，确保数据不重叠。

**backend/src/app/models/message.py**
- 新增 `analysis_json` 字段用于持久化存储 AI 对每条消息的语义识别结果。

**frontend/src/app/documents/page.tsx**
- 页面正式更名为“审核控制台”。
- 新增“AI 回复待审核”管理区，支持对拦截回复的一键审批、修改和发送。

**frontend/src/components/layout-shell.tsx**
- 侧边栏导航标签由“文档审核”更新为“审核控制台”，图标更新。

**frontend/src/app/cases/[id]/page.tsx**
- 移除详情页冗余的 AI 回复控制开关和状态卡片，统一由全局策略驱动。

---

### 修改内容

**问题：**
1. 参考文件中的 docx 顶部品牌文字和标题需要居中加粗。
2. 打卡内容需要按早C午B晚A、饮水、睡眠、情绪、运动、体重结构化记录。
3. 需要 3/7/14/21/28 天阶段报告和毕业报告。
4. 除实时问答外的 AI 生成内容需要可配置人工审核或自动发送，高风险用户必须人工审核。
5. 首页看板和用户详情页缺少风险、未跟进、复购、流失、结构化打卡等运营信息。

**解决方案：**
- 修正模板 docx 的品牌头、标题、收尾品牌文字样式：居中、加粗。
- `#打卡` 消息自动解析 6 项结构化字段，并保留原文。
- 新增阶段报告/毕业报告文档类型，复用现有 `content_json` 模板、审核、下载和发送链路。
- 新增内容发送策略接口：`manual_review` / `auto_send`；高风险服务单不自动发送。
- 新增风险识别规则，命中孕哺期、慢病、抑郁、激素类药物、肝肾异常、结石、严重失眠、胸痛、心悸、过敏、医疗承诺等会标记服务单高风险。
- 看板新增今日新增用户、风险用户、未跟进用户、即将复购用户、流失用户。
- 看板指标卡片可点击跳转，并补充统计口径说明；“即将复购用户”改为更准确的“续购跟进窗口”，新增“明确续购意向”“退款/退出意向”。
- 待审核文件统计改为只统计每个服务单当前有效的最新文档，历史版本不再计入。
- 新增全局打卡列表接口，看板“今日打卡”和“最近打卡”统一按未删除服务单统计。
- 高风险服务单展示具体命中原因，例如孕期、慢病、胸痛心悸等风险标签和命中词。
- 入站聊天、打卡和问卷提交新增 LLM 意图识别/信息提取：判断是否需要人工介入、风险原因、明确续购意向、退款/退出意向；关键词规则保留为兜底。
- `service_cases` 汇总保存人工介入、续购意向、退款/退出意向和 AI 自动回复开关；`messages` 保存单条消息识别结果。
- AI 实时回复新增发送策略：低风险可自动发送，高风险/人工介入/手动审核模式会生成待审核回复，不直接发群。
- 审核控制台新增“AI 回复待审核”，支持编辑回复、保存修改、标记人工介入和确认发送。
- 用户详情页补充画像运营视角：风险识别、减重带判断、标签、人工介入、续购意向、退款/退出意向、AI 自动回复状态。
- 未跟进用户与流失用户统计改为互斥：未跟进为 2-7 天无入站消息，流失为超过 7 天无入站消息。
- 用户详情页展示基本信息、方案、结构化打卡、体重/睡眠/情绪变化、购买/复购占位、AI 总结、聊天摘要和最近聊天记录。

### 具体修改

**backend/alembic/versions/20260515_0006_mvp_loop_fields.py**（新增）
- `document_type` 增加 `STAGE_REPORT` / `GRADUATION_REPORT`。
- `service_cases` 增加 `risk_level` / `risk_flags`。

**backend/alembic/versions/20260515_0007_app_settings.py**（新增）
- 新增 `app_settings` 表，持久化内容发送策略。

**backend/src/app/services/checkin_service.py**
- 新增打卡解析：`product_schedule`、`water`、`sleep`、`mood`、`exercise`、`weight`。
- 入站打卡自动写入 `parsed_json`。

**backend/src/app/services/document_templates.py**
- 新增阶段报告和毕业报告模板。

**backend/src/app/services/document_service.py**
- 新增 `generate_stage_report`。
- 阶段报告按固定模板生成 `content_json` 和 docx。
- 自动发送前检查内容发送策略和服务单风险状态。

**backend/src/app/services/risk_service.py**（新增）
- 新增 MVP 风险关键词识别和服务单风险合并。

**backend/src/app/api/routes/documents.py**
- 新增 `POST /api/service-cases/{id}/stage-reports/generate?stage=...`。

**backend/src/app/api/routes/settings.py**
- 新增 `GET/POST /api/settings/content-delivery-policy`。
- 内容发送策略写入数据库，后端重启后不会丢失。

**backend/src/app/services/reminder_service.py**
- 定时检查服务中用户，到达 3/7/14/21/28 天和毕业节点时生成报告；已有报告不重复生成。

**frontend/src/app/cases/[id]/page.tsx**
- 展示高风险提示。
- 增加阶段报告生成按钮。
- 最近打卡改为展示结构化字段。

**frontend/src/app/checkins/page.tsx**
- 打卡列表展示结构化字段。

**frontend/src/app/dashboard/page.tsx**
- 新增 5 个运营指标。
- 指标卡片改为可点击，并在卡片上展示统计定义。
- 最近打卡改为读取全局打卡接口；去掉含义不清的“查看全部”，列表项点击进入对应服务单。
- “即将复购用户”改名为“续购跟进窗口”，并新增“明确续购意向”“退款/退出意向”。

**backend/src/app/api/routes/checkins.py**
- 新增 `GET /api/checkins`，用于看板展示最近打卡。

**backend/src/app/api/routes/dashboard.py**
- 待审核文件只统计当前有效最新文档，历史版本不计入。
- 今日新增、今日消息、今日打卡按 Asia/Shanghai 日期口径统计。
- 新增续购意向和退款/退出意向关键词统计。
- 未跟进和流失统计互斥，并新增待人工介入用户数。

**backend/alembic/versions/20260515_0008_intervention_and_ai_review.py**（新增）
- `messages` 增加 `analysis_json`。
- `service_cases` 增加 `ai_auto_reply_enabled`、`needs_human_intervention`、`intervention_reasons`、`renewal_intent_flags`、`refund_intent_flags`。

**backend/src/app/services/intervention_service.py**（新增）
- 新增 LLM 意图识别/信息提取统一服务，覆盖聊天、打卡、问卷。
- 关键词规则保留为风控兜底。

**backend/src/app/services/ranren_ai/engine.py**
- 用户消息进入日常回复 LLM 之前先做意图识别。
- 高风险或需要人工介入时，AI 回复进入待审核，不直接发送。

**backend/src/app/api/routes/messages.py**
- 新增待审核 AI 回复列表、编辑和确认发送接口。

**backend/src/app/api/routes/settings.py**
- 新增 AI 回复发送策略接口：`GET/POST /api/settings/ai-reply-delivery-policy`。

**frontend/src/app/cases/[id]/page.tsx**
- 高风险提示展示具体风险原因和命中关键词。
- 增加 AI 自动回复开关、人工介入原因、续购意向、退款/退出意向展示。

**frontend/src/app/documents/page.tsx**
- 审核控制台新增 AI 回复发送策略和 AI 回复待审核区。

**frontend/src/app/users/[id]/page.tsx**
- 增强用户详情页，展示基本信息、方案、打卡变化、AI 总结、购买复购占位和历史聊天。
- 用户画像展示风险识别、减重带判断、标签和人工介入/意向信息。

### 校验

- 数据库迁移已执行到 `20260515_0007`。
- 内容发送策略接口已验证可持久化写入 `app_settings`。
- 后端 Python 编译通过。
- 前端 `next build` 通过。
- 打卡样例可正确解析早C/午B/晚A、饮水、睡眠、情绪、运动、体重。
- 阶段报告 docx 样例生成成功，顶部品牌文字和标题均为居中加粗。
- 服务单详情页阶段报告按钮会根据历史文档的 `content_json.stage` 判断已生成报告，并将对应按钮置灰禁用。

## 2026-05-15 - 模板式 DOCX 与分块审核

### 修改内容

**问题：**
1. 文档审核列表没有区分待审核和已审核文件。
2. 交付文件不应由 LLM 生成整篇自由文本后直接渲染，而应使用固定 docx 模板，只让 LLM 填占位内容。
3. 审核页整篇内容放在一个大输入框里，不利于运营按模块微调；固定标题和表格框架也不应被误改。
4. 机器人日常交流提示词需要明确“燃人AI”的语气和医疗边界。

**解决方案：**
- 文档审核页拆成“待审核 / 已审核”两栏。
- 参考 `参考文件` 里的两份 docx，抽象出两个固定模板：
  - 《个人能量重启处方单》：用户信息表 + 14 个固定模块。
  - 《28天执行标准与打卡要求》：目标说明 + 4周执行表 + 打卡/提醒模块。
- 新生成文档保存结构化 `content_json`，LLM 只填字段，python-docx 按固定模板生成 docx。
- 审核页根据 `content_json` 分块编辑：标题只读，表格标签只读，正文和表格内容可改。
- 机器人提示词改为燃人AI定位：积极阳光、给正反馈、正直可爱、不欺骗、不做医疗诊断/治疗承诺。

### 具体修改

**backend/alembic/versions/20260515_0005_document_content_json.py**（新增）
- `user_documents` 新增 `content_json` 字段，用于保存模板化文档块。

**backend/src/app/models/user_document.py**
- `UserDocument` 新增 `content_json`。

**backend/src/app/schemas/document.py**
- 文档读取和更新接口支持 `content_json`。

**backend/src/app/services/document_templates.py**（新增）
- 定义两份交付文件的模板字段、固定模块、固定表格结构。
- 将 LLM 字段 JSON 转成审核页可编辑的 blocks。
- 将 blocks 渲染为 Markdown 预览文本。

**backend/src/app/services/docx_service.py**
- 新增 `write_template_docx`，按模板 blocks 生成 docx。
- 固定表格框架，标签单元格加粗，内容单元格由 LLM/运营填充。

**backend/src/app/services/document_service.py**
- 文档生成改为：LLM 输出字段 JSON → 后端模板组装 `content_json` → python-docx 生成 docx。
- 运营修改 `content_json` 后自动重写 docx 文件。
- 保留旧 Markdown 文档兼容逻辑。

**frontend/src/app/documents/page.tsx**
- 文档审核页分为“待审核”和“已审核”两组展示。

**frontend/src/app/documents/[caseId]/document-editor.tsx**
- 支持结构化分块审核。
- 文本块、列表块、表格内容可编辑。
- 标题和表格标签不可编辑。
- 修改后自动保存并重新生成 docx。

**frontend/src/lib/api.ts**
- `UserDocument` 类型新增 `content_json`。

**backend/src/app/services/ranren_ai/engine.py**
- 新增燃人AI机器人系统提示词，明确鼓励风格和医疗边界。

### 校验

- 数据库迁移已执行到 `20260515_0005`。
- 后端 Python 编译通过。
- 前端 `next build` 通过。
- 已用样例字段生成模板 docx，文件可正常写出。

## 2026-05-15 - 服务单状态与聊天记录修复

### 修改内容

**问题：**
1. 分别确认发送两份文件后，服务单仍停留在“审核中”。
2. 服务单详情缺少“已服务时长”指标。
3. 聊天记录新旧顺序反了，先发的消息没有在上方。
4. 后续 @ 机器人的消息、机器人发送文件记录没有出现在服务单聊天记录里。
5. 同一群被误删/重建后，机器人可能拿不到最新有效服务单上下文。

**解决方案：**
- 单份文件发送成功后，检查该服务单最新两份交付文件是否都已发送；若都已发送，将服务单推进到“服务中”，并写入 `started_at`。
- 批量发送和单份发送都会把文件发送动作写入 `messages` 表。
- 服务单聊天记录改为按当前服务单所在群和服务单创建时间查询，并按时间升序返回。
- 前端聊天记录折叠时显示最近 10 条，但仍保持旧消息在上、新消息在下。
- 机器人处理群消息时选择该群最新的未删除服务单，避免旧服务单或删除态服务单干扰。

### 具体修改

**backend/src/app/services/document_service.py**
- `send_one_document` 发送成功后记录一条 `DOCUMENT_DELIVERY` 消息。
- 最新两份交付文件均为 `SENT` 时，服务单状态自动变为 `IN_SERVICE`，并设置 `started_at`。
- `send_case_documents` 批量发送时同步记录开场说明和每份文件发送记录。

**backend/src/app/api/routes/messages.py**
- 服务单聊天记录从“只查绑定 service_case_id 的消息”改为“查同群且晚于服务单创建时间的消息”。
- 返回顺序改为 `created_at asc`。

**backend/src/app/services/ranren_ai/engine.py**
- 入站消息和 AI 回复落库时，统一绑定该群最新未删除服务单。

**frontend/src/app/cases/[id]/page.tsx**
- 服务单详情新增“已服务时长”，精确到小时。

**frontend/src/app/cases/[id]/chat-messages.tsx**
- 聊天记录折叠态显示最近 10 条。
- 文件发送类消息显示“文件”标签。

**frontend/src/lib/api.ts**
- `ServiceCase` 类型补充 `started_at` / `ended_at`。

### 数据修复

- 已将服务单 `RAI-20260515-962FE2` 从 `REVIEWING` 修正为 `IN_SERVICE`。
- 已按两份文件的发送时间补写服务开始时间。
- 已把用户后续消息“在？”绑定回该服务单。
- 已补写两条文件发送聊天记录。

## 2026-05-15 - 服务单删除独立性修复

### 修改内容

**问题：** 用户管理页虽然按服务单展示，但删除按钮仍调用用户删除接口；企微自动建单也会按 `wecom_user_id` 复用同一个用户记录，导致删除其中一个服务单时，同一用户下的多个服务单被一起软删除。

**解决方案：**
- 删除操作改为删除单个服务单/客户空间，不再删除整个用户及其所有服务单。
- 企微自动建单改为每次创建独立用户记录，不再复用同一个 `wecom_user_id` 用户。
- 数据库移除 `users.phone` / `users.wecom_user_id` 的活跃唯一约束，允许试验阶段同一手机号或企微 ID 生成多个独立客户空间。

### 具体修改

**backend/alembic/versions/20260515_0004_independent_service_case_users.py**（新增）
- 删除 `ux_users_phone_active` 和 `ux_users_wecom_user_id_active` 唯一索引。
- 改为普通索引 `ix_users_phone` / `ix_users_wecom_user_id`。

**backend/src/app/models/user.py**
- `phone` / `wecom_user_id` 改为普通索引字段。

**backend/src/app/api/routes/service_cases.py**
- 新增 `DELETE /api/service-cases/{service_case_id}`。
- 只软删除当前服务单。
- 若该用户没有其他活跃服务单，才同步软删除该用户。

**backend/src/app/services/ranren_ai/engine.py**
- 创建入营问卷服务单时，每个服务单都新建独立用户记录。

**frontend/src/app/users/user-actions.tsx**
- 删除按钮改为调用服务单删除接口。

**frontend/src/app/users/page.tsx**
- 正常用户页的删除按钮传入 `service_case.id`，避免误删同一用户下其他服务单。

### 数据修复

- 恢复误删的 `RAI-20260514-7FFCD6`、`RAI-20260515-587716`、`RAI-20260515-962FE2`。
- 将这三个服务单拆成三个独立用户记录：user 5 / 7 / 8。
- 已验证删除临时服务单时，另一个同名同手机号服务单仍保持活跃。

## 2026-05-15 - 用户/服务单展示、时间与文档审核修复

### 修改内容

**问题：**
1. 用户管理页按用户聚合多个服务单，不符合“一个用户只对应一个服务单/画像”的实际业务。
2. 前端时间按浏览器默认解析 UTC，显示比北京时间少 8 小时。
3. 服务单详情中点击某一份交付文件，会进入按服务单聚合的旧审核页，导致两份文件一起显示。
4. 个别 LLM 返回的文档内容为空或把 JSON 原文塞进正文，导致《个人能量重启处方单》只剩标题壳。

**解决方案：**
- 用户管理正常列表改成按服务单展示：一张卡就是一个客户服务空间。
- 新增统一北京时间格式化工具，所有关键时间使用 `Asia/Shanghai` 展示。
- 文档入口改成 `/documents/{document_id}`，点击哪份文件只查看哪份文件。
- 文档生成增加 JSON 嵌套解析、内容质量校验和 Markdown 兜底生成；内容不足时不再静默落库为空壳。

### 具体修改

**frontend/src/app/users/page.tsx**
- 正常用户页改为读取 `/service-cases`，不再把同一用户下的服务单聚拢显示。
- 已删除用户页保留历史用户视图。

**frontend/src/lib/format.ts**（新增）
- 新增北京时间格式化工具：
  - `formatChinaDateTime`
  - `formatChinaShortDateTime`
  - `formatChinaMonthDayTime`

**frontend/src/app/cases/[id]/page.tsx**
- 交付文件链接从服务单 ID 改为文档 ID。
- 服务单详情时间改为北京时间。

**frontend/src/app/documents/page.tsx**
- 文档审核列表链接改为 `/documents/{document_id}`。

**frontend/src/app/documents/[caseId]/page.tsx**
- 路由保留现有文件名，但参数语义改为 documentId。
- 页面只加载单份文档。

**backend/src/app/services/document_service.py**
- 增强 LLM JSON 解析：支持 code fence、嵌套 JSON 字符串、summary/body/content 中包 JSON。
- 增加文档内容质量校验：过短或仍包含原始 JSON 结构时判定失败。
- 结构化生成失败时 fallback 到 Markdown 生成。

**backend/src/app/api/routes/documents.py**
- 删除服务单保持只读，禁止批量发送接口绕过只读限制。

### 数据修复

- 已重新生成服务单 `RAI-20260515-962FE2` 的两份交付文件。
- 新版《个人能量重启处方单》内容长度约 2700 字符，不再是空壳。
- 新版《28天执行标准与打卡要求》不再显示原始 JSON。

## 2026-05-15 - 问卷试验样例填充

### 修改内容

**问题：** 试验阶段每次跑完整问卷需要手动填写 61 个问题，链路验证成本太高。

**解决方案：** 在未提交的 H5 问卷页增加试验辅助按钮，一键填入“蔡浩然”样例问卷答案。

### 具体修改

**frontend/src/app/q/[token]/sample-answers.ts**（新增）
- 固化蔡浩然样例问卷答案。
- 覆盖当前正式问卷 61 个填写项。
- 补齐体脂率、腰围、体检报告说明等可选项。

**frontend/src/app/q/[token]/questionnaire-form.tsx**
- 新增“一键选择蔡浩然版”按钮。
- 点击后只填充当前问卷中存在的字段。
- 填充后仍需手动点击“提交问卷”，避免误提交。

### 校验

- 样例答案数量：61 项。
- 必填题覆盖：无遗漏。
- 单选/多选选项：均匹配当前问卷配置。

## 版本信息

**接手版本：** `3d01d84` - feat: 燃AI 1.0 初始版本
**修改完成版本：** 本次提交（2026-05-14 21:30）

**接手时状态：**
- 初始版本，基础功能已实现
- 问卷提交后需要手动触发生成文档
- 前端页面布局粗糙，功能分散
- 文档审核页面404

**修改完成后状态：**
- 问卷提交后自动生成画像和文档
- 全局自动发送配置（文档审核页面）
- 前端页面美化重构（侧边栏、用户管理、数据看板）
- 文档编辑器简化（自动保存、所见即所得）
- 聊天记录和问卷折叠组件
- Markdown预览渲染

---

## 2026-05-14 18:30 - 自动化流程优化

### 修改内容

**问题：** 用户提交问卷后需要运营人员手动点击多个按钮才能生成和发送文档，流程繁琐。

**解决方案：** 问卷提交后自动生成画像和文档，通过服务单级别的配置控制是否自动发送。

### 具体修改

#### 1. 后端修改

**backend/src/app/models/service_case.py**
- 新增字段：`auto_send_documents` (Boolean, 默认 False)
- 每个服务单独立控制是否自动发送文档

**backend/src/app/api/routes/questionnaires.py**
- 修改问卷提交逻辑，提交后自动触发：
  1. 生成用户画像
  2. 生成两份交付文件
- 根据服务单的 `auto_send_documents` 字段决定是否自动发送

**backend/alembic/versions/**
- 新增迁移：添加 `auto_send_documents` 字段到 `service_cases` 表

#### 2. 前端修改

**frontend/src/app/cases/[id]/case-actions.tsx**
- 添加勾选框：控制文档生成后是否自动发送
- 保留"确认发送两份文件"按钮（仅在未勾选自动发送时显示）

**frontend/src/lib/api.ts**
- ServiceCase 类型添加 `auto_send_documents` 字段

#### 3. 使用说明

**操作流程：**
1. 创建服务单后，在服务单详情页勾选"文档生成后自动发送到群"
2. 用户提交问卷后，系统自动生成画像和文档
3. 如果勾选了自动发送，文档会直接发送到群
4. 如果未勾选，需要手动点击"确认发送两份文件"

### 新流程

**自动发送模式（勾选自动发送）：**
```
用户提交问卷
    ↓
自动生成画像 + 生成文档 + 自动发送
    ↓
用户直接在群里收到文档
```

**人工审核模式（未勾选自动发送）：**
```
用户提交问卷
    ↓
自动生成画像 + 生成文档
    ↓
运营点击"确认发送两份文件"
```

### 待 Codex 接力

1. **测试新流程**：验证勾选框是否正常工作
2. **优化错误处理**：如果自动生成失败，需要有重试机制
3. **添加状态提示**：在前端显示文档生成状态（生成中/已完成/失败）
4. **审核页面优化**：在服务单详情页显示文档审核状态
5. **消息通知**：文档生成完成后通知运营人员

## 2026-05-14 19:00 - 聊天记录和问卷折叠

### 修改内容

#### 1. 新增功能

**聊天记录显示**
- 新增组件：`frontend/src/app/cases/[id]/chat-messages.tsx`
- 显示用户和机器人的聊天记录
- 区分消息方向（用户/机器人/运营）
- 支持展开/收起（默认显示最近 10 条）
- 标记打卡消息

**问卷结果折叠**
- 新增组件：`frontend/src/app/cases/[id]/questionnaire-section.tsx`
- 问卷结果默认折叠，点击展开
- 显示提交时间和所有答案

#### 2. 前端修改

**frontend/src/lib/api.ts**
- 新增 Message 类型定义

**frontend/src/app/cases/[id]/page.tsx**
- 集成聊天记录和问卷折叠组件
- 获取聊天记录数据

#### 3. 布局优化

- 问卷结果：折叠显示，点击展开
- 交付文件：保持原样
- 最近打卡：保持原样
- 聊天记录：新增，显示在最后

## 2026-05-14 19:30 - 前端页面美化重构

### 修改内容

**问题：** 前端页面布局粗糙，功能分散，用户体验不佳。

**解决方案：** 重新设计页面布局和导航结构，合并相关功能，美化 UI。

### 具体修改

#### 1. 侧边栏导航重构

**frontend/src/components/layout-shell.tsx**
- 重新设计侧边栏：更宽、带图标、激活状态高亮
- 导航项精简为 4 个：用户管理、文档审核、问卷配置、数据看板
- 移除独立的服务单、打卡记录导航（合并到其他页面）
- 添加底部版权信息

#### 2. 用户管理页面重构

**frontend/src/app/users/page.tsx**
- 卡片式布局展示用户
- 每个用户卡片内嵌服务单列表
- 服务单显示状态标签（彩色）
- 点击服务单直接进入详情页
- 合并了原"/cases"列表功能

#### 3. 数据看板重构

**frontend/src/app/dashboard/page.tsx**
- 统计卡片使用彩色背景区分
- 添加图标和更好的视觉层次
- 内嵌最近打卡记录列表
- 合并了原"/checkins"功能
- 移除独立打卡记录页面

#### 4. 服务单详情页美化

**frontend/src/app/cases/[id]/page.tsx**
- 状态标签彩色显示
- 信息卡片添加图标
- 交付文件区域添加空状态提示
- 打卡记录使用头像+内容布局
- 整体使用圆角卡片和阴影

### 导航结构变化

**之前：**
- 用户 / 服务单 / 问卷配置 / 打卡记录 / 基础看板

**现在：**
- 用户管理（含服务单）
- 文档审核
- 问卷配置
- 数据看板（含打卡记录）

### 待 Codex 接力

1. **响应式设计**：确保移动端显示正常
2. **加载状态**：添加骨架屏或加载动画
3. **空状态优化**：更多页面的空状态提示
4. **动画效果**：添加页面切换和交互动画
5. **深色模式**：支持深色主题切换

## 2026-05-14 20:00 - UI优化和问题修复

### 修复内容

#### 1. 文档发送按钮移至文档审核页面

**frontend/src/app/cases/[id]/case-actions.tsx**
- 移除"确认发送两份文件"按钮
- 仅保留"自动发送"勾选框配置

**frontend/src/app/documents/[caseId]/document-editor.tsx**
- 添加"确认发送"按钮
- 添加"审核通过"按钮
- 添加预览/编辑切换功能
- 预览模式使用 react-markdown 渲染 HTML

**frontend/src/app/documents/page.tsx**（新增）
- 创建文档审核列表页
- 显示所有服务单的交付文件
- 按状态筛选（待审核/已批准/已发送）
- 修复 404 问题

#### 2. 问卷结果默认显示第一个问题

**frontend/src/app/cases/[id]/questionnaire-section.tsx**
- 折叠状态显示第一个问题摘要
- 显示"还有 N 项..."提示
- 展开按钮显示"展开全部 N 项"

#### 3. 打卡空状态图标修复

**frontend/src/app/cases/[id]/page.tsx**
- 空状态图标从 ✅ 改为 📋
- 文字改为"暂无打卡记录"

#### 4. Markdown 渲染支持

**frontend/src/components/markdown-preview.tsx**（新增）
- 使用 react-markdown + remark-gfm 渲染
- 自动将 Markdown 语法转换为 HTML 展示
- 文档预览模式使用此组件

### 文档生成逻辑说明

当前 docx 生成逻辑（backend/src/app/services/docx_service.py）：
1. AI 生成结构化 JSON（包含标题、段落、列表等）
2. 后端将 JSON 转换为 Markdown 格式
3. Markdown 通过 python-docx 库直接渲染为 docx
4. 使用微软雅黑字体，支持中文

注意：当前不是模板填充，而是动态生成完整文档内容。

## 2026-05-14 20:30 - 全局自动发送设置

### 修改内容

**问题：** "自动发送"设置放在单个服务单中不合理，应该是全局配置。

**解决方案：** 将自动发送改为全局设置，放到文档审核页面。

### 具体修改

#### 1. 后端修改

**backend/src/app/api/routes/questionnaires.py**
- 问卷提交后使用 `get_settings().auto_send_documents`（全局配置）

**backend/src/app/api/routes/settings.py**（新增）
- 新增 API：`GET/POST /api/settings/auto-send`
- 获取/设置全局自动发送配置

**backend/src/app/api/router.py**
- 注册 settings 路由

#### 2. 前端修改

**frontend/src/app/cases/[id]/case-actions.tsx**
- 移除勾选框（不再是个体配置）
- 简化为空组件

**frontend/src/app/documents/page.tsx**
- 添加全局"自动发送"开关（切换按钮样式）
- 显示当前模式说明

**frontend/src/app/documents/auto-send-toggle.tsx**（新增）
- 切换按钮组件
- 调用 `/api/settings/auto-send` API

#### 3. 配置说明

```bash
# 环境变量控制（重启生效）
AUTO_SEND_DOCUMENTS=false  # 默认，需要人工审核
AUTO_SEND_DOCUMENTS=true   # 自动发送到群
```

## 2026-05-14 21:00 - 文档审核页面优化

### 修改内容

**问题：** 
1. 文档审核列表页缺少用户姓名和手机号，难以区分
2. 点击文档进入详情页时显示该服务单的所有文档，而不是单个文档
3. 文档编辑器按钮太多，流程复杂

**解决方案：** 
1. 列表页显示用户姓名、手机号
2. 路由改为 `/documents/[documentId]`，只显示单个文档
3. 简化编辑器，移除多余按钮，改为自动保存

### 具体修改

#### 1. 文档审核列表页优化

**frontend/src/app/documents/page.tsx**
- 显示用户姓名和手机号：`{user_name} · {user_phone} · {case_no}`
- 链接改为 `/documents/${doc.id}`（文档ID而非服务单ID）

#### 2. 文档详情页路由重构

**frontend/src/app/documents/[documentId]/page.tsx**（新增）
- 新路由：`/documents/[documentId]`
- 使用 `GET /documents/{documentId}` API 获取单个文档
- 只显示当前文档，不再显示服务单的所有文档

#### 3. 文档编辑器简化

**frontend/src/app/documents/[caseId]/document-editor.tsx**
- 移除"审核通过"按钮
- 移除"保存"按钮（改为自动保存，debounce 1.5秒）
- 移除"预览"按钮（默认显示渲染后的内容）
- 只保留两个按钮：**确认发送**、**下载 docx**
- 点击内容进入编辑模式，修改后自动保存
- 编辑模式显示"Markdown 编辑模式（修改后自动保存）"提示

### 新流程

**文档审核列表页：**
- 显示：文档标题、用户姓名、手机号、服务单编号、状态
- 点击进入单个文档的详情页

**文档详情页：**
- 默认显示渲染好的文档内容（所见即所得）
- 点击内容进入编辑模式（Markdown源码）
- 修改后自动保存（1.5秒延迟）
- 只有两个按钮：确认发送、下载 docx

### 待 Codex 接力

1. **测试自动保存功能**：验证修改后是否自动保存
2. **测试单文档路由**：验证 `/documents/[documentId]` 是否正常工作
3. **优化 Markdown 渲染样式**：进一步美化文档预览效果
4. **添加返回按钮**：在文档详情页添加返回列表的按钮
