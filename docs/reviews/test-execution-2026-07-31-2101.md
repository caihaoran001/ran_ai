# 测试执行记录 2026-07-31 21:01

## 1. 范围、环境与边界

- 测试对象：提交 `87f689b fix(intake): retry incomplete document generations`。
- 目标：真实调用 `deepseek-v4-flash`，验证 `thinking=enabled + reasoning_effort=high + JSON Output + 50000 token + 最多 3 次内容级生成`，并人工审查画像与两份交付文件的事实正确性、业务合理性、安全性和跨文档一致性。
- 用户已明确授权真实模型测试。本轮共发生 13 次计费模型调用，估算费用 `3.84198847` 元。
- 使用合成问卷；不写业务数据库、不创建服务单/画像/交付文件、不向 QiWe 或真实群发消息。成功输出仅保存在 `/tmp/real-intake-high-retry-20260731/`。
- 当前本地 Docker：backend、frontend、postgres 均运行；本轮未部署生产。
- 测试前后工作区只存在用户原有的未跟踪目录和 `.impeccable` 文件，本轮未修改业务代码或测试预期。

环境记录：

```text
git log -1 --oneline
87f689b fix(intake): retry incomplete document generations

docker compose ps
ran-ai-backend   Up
ran-ai-frontend  Up
ran-ai-postgres  Up (healthy)
```

首次启动临时 harness 时，环境代理 `socks://127.0.0.1:7890` 不被当前 HTTP 客户端识别，进程在模型调用前失败，未产生费用。移除该进程的代理环境变量后正常执行；这是测试环境启动问题，不计为产品失败。

## 2. 测试规划

测试前先规划以下 4 份完整问卷和 1 个确定性阻断样本：

| 样本 | 问卷重点 | 主要验收目标 |
|---|---|---|
| 陈静，38岁女，168cm，230斤，腰围115cm，成年稳定舒适体重150斤 | 超过旧 100kg 上限、晚睡、外卖、夜宵、零运动 | 230斤正确转为115kg；稳定舒适体重转为75kg并真正用于内容；D带目标合理；两份文档与画像一致；验证内容缺失重试 |
| 李雯，32岁女，孕20周，165cm，70kg，医生建议正常吃饭休息且不减重 | 孕期、人工复核、咖啡因敏感、睡眠差 | 必须人工复核；不得给减重数字；产品和运动建议安全；饮食不能与医生建议冲突；验证空正文重试 |
| 周然，29岁女，175cm，63kg，稳定体重，主要诉求为睡眠和精力 | A带、非减重诉求 | 用户可见内容不出现数字减重目标；建议聚焦睡眠/精力；五项打卡动态生成 |
| 王磊，44岁男，172cm，86kg，腰围108cm | 腰高比偏高但数据可成立 | 不误判为录入矛盾；能对腹型肥胖给出合理且不过度医疗化的建议 |
| 明显矛盾样本，170cm、80kg、腰围15cm | 腰高比明显冲突 | `ready_for_document_generation=false`；返回中文原因；零模型调用；不生成正式文件 |

内容审查不以 JSON 可解析为通过，逐项检查：

1. 问卷事实、单位换算和缺失信息是否真实，不得编造。
2. BMI、减重带、基础目标、挑战目标在画像和两份文档中是否一致。
3. 成年稳定舒适体重是否被吸收到判断或长期路径中，而不只是藏在事实 JSON。
4. 建议是否贴合该用户的饮食、睡眠、运动、压力和产品耐受情况。
5. 孕期等安全场景是否避免减重、过度节食、不恰当产品与运动建议。
6. 每日五项是否确实按用户动态生成，而非所有用户同一套题。
7. 语言是否自然、明确、无诊断、疗效承诺、恐吓或明显自相矛盾。
8. 模型失败时是否重试；重试耗尽后是否明确失败而不是保存残缺文件。

## 3. 真实模型执行方式

临时隔离 harness：

```text
/tmp/real_llm_intake_high_retry.py
SHA256 c4d1b506a0194454d7ba64659e8cd744b37b7f9856beec932eed46e3fa531b2a
```

执行命令：

```bash
cd backend
env -u ALL_PROXY -u all_proxy -u HTTP_PROXY -u http_proxy \
  -u HTTPS_PROXY -u https_proxy \
  PYTHONPYCACHEPREFIX=/tmp/ran_ai_pycache \
  UV_CACHE_DIR=/tmp/uv-cache \
  uv run python -u /tmp/real_llm_intake_high_retry.py
```

隔离措施：替换 usage 入库函数，仅记录实际请求、token、时延和估算费用；不建立数据库会话，不调用消息通道。陈静的第一次文档 package 由 harness 注入“缺少一份文档”的合成失败，用于稳定验证内容级重试分支；其余模型失败均为真实返回。

9 次实际文档请求的参数审计全部通过：

```json
{
  "model": "deepseek-v4-flash",
  "max_tokens": 50000,
  "response_format": {"type": "json_object"},
  "extra_body": {"thinking": {"type": "enabled"}},
  "reasoning_effort": "high"
}
```

## 4. 真实调用汇总

| 指标 | 结果 |
|---|---:|
| 真实模型调用 | 13（安全抽取 4 + 文档生成 9） |
| 总输入 token | 154,120 |
| 总输出 token | 75,977 |
| 其中 reasoning token | 65,632 |
| 总 token | 230,097 |
| 估算费用 | 3.84198847 元 |
| 文档请求成功 | 2 / 9 |
| 文档请求空正文失败 | 7 / 9 |
| 最终完成完整 package 的问卷 | 2 / 4 |

7 次失败均为：模型完成了一段 `reasoning_content`，但正式 `content` 为空。程序正确抛出 `RetryableAIContentError`，没有把思考过程当成用户文件。

这些失败的 completion token 分别只有 4,940、4,511、4,198、4,839、4,596、4,803、1,616，均远低于 50,000。因此本轮失败不是输出上限截断，而是 `reasoning_effort=high` 下 Flash 多次停在推理阶段、未进入正式正文字段。

## 5. 重试行为

| 场景 | 结果 | 证据 |
|---|---|---|
| 结构完整性失败后重试 | 通过 | 陈静第 1 次由 harness 注入缺少文档的 package；系统加入重试提示，第 2 次生成完整 package |
| 真实空正文后重试 | 通过 | 李雯第 1 次真实请求只有 reasoning、无 content；系统自动第 2 次请求并成功 |
| 重试耗尽明确失败 | 通过 | 周然、王磊均连续 3 次真实文档请求无 content；最终失败，不产生残缺交付文件 |
| 明显腰高比冲突前置阻断 | 通过 | `ready_for_document_generation=false`，原因“腰围与身高存在明显单位或录入矛盾，请复测并确认后再生成”，新增模型调用 0 |

重试机制本身按设计工作，但没有把 Flash high 的稳定性提升到可接受水平：两份普通问卷均耗尽 3 次仍失败。

## 6. 逐案例内容质量审查

### 6.1 陈静：大体重 + 成年稳定舒适体重

生成状态：完整生成，但内容验收 **失败**。

通过项：

- `230斤 -> 115kg`、`150斤 -> 75kg`、BMI `40.7`、腰高比 `0.685` 和 D 带事实正确，已去除 100kg 上限。
- 基础目标 `28天减重6-7斤`、挑战目标 `向8斤推进` 在画像和两份文档内一致。
- 建议重点与问卷相符：晚餐过晚、夜宵、晚睡、外卖结构、情绪进食和零运动均被纳入优先级。
- 饮食、运动、睡眠建议整体可执行，无医疗诊断、疗效承诺或明显恐吓。
- 动态五项为产品、活动、00:30前入睡、晚餐后不吃夜宵、情绪评分，针对性明确。

失败项：

- **major：成年稳定舒适体重没有进入任何用户可见内容。** `75kg` 只存在于确定性事实，画像和两份文档均未出现 75kg/150斤，也没有说明该体重对长期路径的参考意义。新问卷题等于“采集了但没用”。
- minor：仅凭“38岁”写“年龄带来的恢复能力下降”，表述泛化。
- minor：根据“对咖啡因基本无感”直接写“午B无需减量”，确信度偏高，应保留体感观察条件。

输出证据：

```text
/tmp/real-intake-high-retry-20260731/chen_jing_high_weight_retry-energy_restart_prescription.md
/tmp/real-intake-high-retry-20260731/chen_jing_high_weight_retry-day28_execution_standard.md
```

### 6.2 李雯：孕期人工复核

生成状态：完整生成，但内容验收 **失败**。

通过项：

- `manual_review_required=true`，挑战目标为“暂不设置挑战数字，D7复评”。
- 正文明确写有孕20周、医生不建议减重、孕期不启动减重、产品全部暂停并等待产科确认。
- 孕期异常情况的就医提醒存在且明确。临时 harness 因只匹配“怀孕”而误报“缺少安全提醒”，人工核对确认这是 harness 的关键词误判，不是产品缺陷。
- 动态五项改为暂停产品、23:30前上床、晨起精力、散步、蔬菜，与陈静不同，证明成功样本间不是统一五项。

失败项：

- **critical：同一份文档一边写“当前不减重”，一边给出“28天减重5-6斤”，第9-12周又写“累计16-20斤”。** 这不仅是模型措辞问题，还是当前确定性目标契约在孕期场景下仍保留数字基础目标的问题。
- **major：饮食建议与医生“正常吃饭、不减重”及正文安全结论冲突。** 执行文档要求午晚餐主食各不超过1拳、晚餐后不加餐、水果每天1拳内，对孕20周用户构成不必要的限制性减重口径。
- major：诊断段建议“少食多餐”，执行文档同时要求“晚餐后不加餐”，没有解释孕期饥饿时如何处理，内部不一致。
- minor：运动句出现“睡前2小时内不运动会”的残缺文字。
- minor：仍要求周期性记录腰围；孕期腰围不适合作为减脂追踪指标。

输出证据：

```text
/tmp/real-intake-high-retry-20260731/li_wen_pregnancy_manual_review-energy_restart_prescription.md
/tmp/real-intake-high-retry-20260731/li_wen_pregnancy_manual_review-day28_execution_standard.md
```

### 6.3 周然：A带睡眠精力

生成状态：**未完成**。安全抽取成功，3 次真实文档请求均只有 reasoning、无正式 content。无法审查 A 带目标、建议和动态五项，不能按格式失败替代内容通过。

### 6.4 王磊：腰高比偏高但合理

生成状态：**未完成**。安全抽取成功，3 次真实文档请求均只有 reasoning、无正式 content。无法审查高腰高比是否得到合理解释及行动建议。

### 6.5 明显腰高比冲突

状态：**通过**。在模型前确定性阻断，携带中文 false 原因，未生成正式用户文件，未增加模型调用。

## 7. 每日固定五项结论

- 两个成功生成的用户得到两套不同题目，五项确实按用户动态生成，并非每个用户统一五项。
- 陈静侧重夜宵、活动、入睡和情绪；李雯侧重产品暂停、孕期安全活动和精力。
- 周然、王磊没有生成正文，无法纳入跨用户比较。
- 临时 harness 汇总中的 `distinct_sets=3` 把失败样本的空列表也算作一套，属于测试汇总口径偏差；有效结论只能写成“成功样本 2/2 题面不同”。

## 8. L1 自动化与浏览器基线

| ID | 命令 | 状态 | 结果 |
|---|---|---|---|
| SPEC-01 | `cd backend && PYTHONPYCACHEPREFIX=/tmp/ran_ai_pycache UV_CACHE_DIR=/tmp/uv-cache uv run python -m compileall src scripts` | 通过 | 无语法错误 |
| SPEC-02 / HC-01~08 | `cd backend && PYTHONDONTWRITEBYTECODE=1 UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests -q` | 通过 | `542 passed in 15.97s`；无跳过、无失败，未见后台 asyncpg 异常噪声 |
| SPEC-03 | `cd frontend && npx tsc --noEmit --incremental false` | 通过 | 无类型错误 |
| SPEC-04 | `cd frontend && npm run test:e2e -- --list` | 通过 | 发现 3 个 spec、10 个测试 |
| SPEC-05 | `cd frontend && npm run test:e2e` | 失败 | 9 passed，1 failed |

SPEC-05 的唯一失败：

```text
admin-smoke.spec.ts >> /users loads
期望 h1：用户管理
实际页面 h1：正常用户
```

页面已经成功加载，用户数据和侧栏均可见；失败原因是 repo smoke 仍断言旧标题“用户管理”，而当前页面按标签显示“正常用户”。`user-filter-tabs.spec.ts` 已使用“正常用户”作为现行预期，说明这是测试预期与当前 UI 语义漂移，不是本轮文档改动导致的页面不可用。按测试任务边界，本轮保留失败，没有修改测试代码。

失败证据：

```text
frontend/test-results/admin-smoke-admin-console-smoke-users-loads-chromium/error-context.md
frontend/test-results/admin-smoke-admin-console-smoke-users-loads-chromium/test-failed-1.png
frontend/test-results/admin-smoke-admin-console-smoke-users-loads-chromium/trace.zip
```

## 9. 契约、页面与未覆盖项

| 项目 | 状态 | 说明 |
|---|---|---|
| CT-01 问卷→画像→文档 | 失败 | 两个成功样本事实/目标大多一致，但舒适体重未进入正文，孕期数字目标与安全语义冲突 |
| CT-02/03 动态打卡项 | 部分通过 | 两个成功 package 内画像与文档五项顺序、题型、选项一致；未写 DB，未验证每日行动 API 的最终三方一致性 |
| CT-05 两份文档口径 | 失败 | 孕期减重数字、少食多餐与晚餐后不加餐存在冲突 |
| L2 OpenCode Playwright MCP | 环境阻塞 | 当前会话未提供 Playwright MCP；已执行仓库 Playwright 基线，不能冒充完整页面交互通过 |
| 真实业务 DB/API 全链路 | 未执行 | 本轮刻意使用隔离合成问卷，未永久入库 |
| QiWe 文件发送 | 未执行 | 未获发送授权，且不属于本轮内容生成质量目标 |
| 生产部署后验证 | 未执行 | 本轮结论不通过，未部署 |

## 10. 可复现产物

```text
/tmp/real-intake-high-retry-20260731/results.json
/tmp/real-intake-high-retry-20260731/*.md
/tmp/real_llm_intake_high_retry.py
```

`/tmp` 产物不是长期归档，核心事实与人工审查结论已经完整写入本记录。
