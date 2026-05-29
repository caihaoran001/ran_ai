# QiWe 通道集成与穿透服务状态交接手册 (Handover Brief)

本手册专为 **GPT 助手** 编写，汇总了当前后台运行的**腾讯云长期穿透隧道状态**、**十秒响应延迟的原因分析**，以及**接下来需要对生产代码进行的 3 项核心功能优化规范**。

---

## 📡 1. 当前后台运行的穿透基础设施状态 (Infrastructure Status)

为了实现公网事件接收，我们在宿主机后台启动了专属的穿透隧道，请勿对其进行修改或终止：

1. **隧道运行脚本**：`qiwe_test/establish_tunnel.py`（进程处于 active/running 状态）。
2. **桥接拓扑关系**：
   * **远程接收端 (QiWe 回调配置)**：`http://<your_server_ip>:8080/api/channels/qiwe/callback`
   * **本地映射目标**：`http://localhost:8000`（直连 Docker 中的 FastAPI 容器）。
3. **安全连接方式**：隧道已升级为从本地 `.env` 文件读取 `TENCENT_SSH_PASSWORD` 进行 SSH 稳定反向代理，并设置了 30 秒 Keepalive 心跳包以保持长期在线。
4. **验证结论**：消息通路已完全打通！用户在微信群发送的消息能毫秒级穿透进入 FastAPI，且由于 `/api/channels/qiwe/callback` 已在主中间件中加入豁免名单，不存在 401/403 拦截风险。

---

## ⏱️ 2. 用户发送消息响应延迟约 10 秒的原因分析

用户测试时反馈“收到回复需要约 10 秒”，**这是完全正常且符合架构设计的预期行为**。

### ⏳ 耗时拆解：
* **8.0 秒 (故意延迟的聚合等待)**：
  在 `QiWeMessageAggregator` 中，我们为群聊设置了默认的 **8 秒消息聚合窗口** (`QIWEI_BATCH_WINDOW_SECONDS=8`)。当收到用户的第一条消息后，系统为了等待用户是否会连发第二条、第三条短句，会在后台故意挂起并等待 8 秒。
* **1.5 ~ 2.0 秒 (AI 大模型处理 + 网络交互)**：
  8 秒截止后，多条消息被合并，送入 `RanrenAIEngine`。引擎调用 DeepSeek-V4-Flash 大模型进行意图风控识别并生成营养师回复文案，这部分网络请求与大模型推理耗时约 1.5 - 2 秒。
* **总耗时**：8 秒 (聚合) + 2 秒 (大模型与发送) ≈ **10 秒**。

> **💡 测试提示**：
> 如果在本地测试中想要体验“瞬间秒回”，可以临时在 `.env` 中将 `QIWEI_BATCH_WINDOW_SECONDS` 改为 `1` 或 `0`。但是在生产环境（多对一 VIP 专属服务群）中，**强烈建议保持 8 秒**，以达到最贴近真人班主任“听完几句话再一并温暖回复”的高级社群陪伴体验。

---

## 🛠️ 3. 接下来需要 GPT 助手配合完成 Lambrin 生产代码优化规范

GPT 助手已成功写出基础通道代码，为了让服务在 VIP 专属群中达到完美的商业级人情味体验，请 GPT 助手接下来实现以下 3 项升级：

### ① 🛡️ 真人防撞抢话机制 (Staff Interception)
* **目的**：在多对一专属 VIP 群中，若真人班主任或营养师已经在 8 秒内手动在微信群里进行了回复，AI 必须立刻闭嘴，防止“回复撞车”导致露馅。
* **开发规范**：
  在 `QiWeMessageAggregator.flush()` 倒计时截止、准备将合并消息发送至核心引擎的临界瞬间，增加一次轻量的数据库检索，检查在用户这批消息的最新一条之后，是否已有 `sender_type == MessageSenderType.STAFF`（服务人员）的消息：
  ```python
  # 伪代码逻辑
  async with db_session() as session:
      staff_reply_exists = await session.scalar(
          select(Message)
          .where(
              Message.group_id == message.chat_id,
              Message.sender_type == MessageSenderType.STAFF,
              Message.created_at > message.timestamp
          )
      )
  if staff_reply_exists:
      logger.info(f"真人服务人员已回复群聊 {message.chat_id}，AI 放弃本次自动发送。")
      return # 静默退出，不调用 AI 引擎发送
  ```

### ② 💬 轻度反馈的温润简短回复约束 (Soft Reply Constraint)
* **目的**：当用户发“谢谢”、“收到”、“哈哈”等礼貌短句时，AI 不应冷漠忽略，但也不能用大段的专业说教来轰炸，而应以极其简短、高情商的短句提供情绪价值。
* **开发规范**：
  在大模型 `generate_text` 时，如果识别到用户意图为轻度反馈，自动在 Prompt 中注入以下约束限制：
  > “用户当前发送了轻度/情感反馈（谢谢、收到、哈哈等）。请将回复严格控制在 **30字以内**，严禁进行任何学术/营养说教，使用极简、充满阳光、温暖的高情商口语化真人短句（例如：‘不客气啦宝子！今晚一定要早点睡，明天继续稳住代谢哦！✨’）提供纯粹的情绪陪伴价值。”

### ③ ⏱️ 聚合器升级为固定窗口算法 (Fixed Window Aggregation)
* **现状**：目前 `QiWeMessageAggregator.enqueue` 采用的是防抖（Debounce）滑动窗口算法（每次收到新消息就 `cancel` 前一个任务并重新启动 8 秒定时器）。如果用户断断续续一直打字，AI 的回复会被无限期延后。
* **开发规范**：
  应升级为 **固定窗口（Fixed Window）算法**：
  * 当某个 `(chat_id, sender_id)` 在空闲状态收到 **第一条消息** 时，立刻启动一个 8 秒的定时任务。
  * 8 秒内所有后续收到的消息均只在列表中追加（`batch.messages.append`），但 **不重置/不取消（Not Cancel）** 已有的定时任务。
  * 8 秒时间一到，准时进行 `flush` 处理。这可以确保任何持续输入的场景下，AI 回复都不会发生恶性延迟。

### ④ ⚡ 系统指令快速通道 (Bypass Aggregator for Commands) 🆕
* **目的**：对于像 `"发送本群入营问卷"` 这样有明确、固定逻辑且不依赖大模型（LLM）的系统硬编码指令，用户期望的是**秒级瞬间回复**，任何延迟等待（例如 8 秒聚合）都会极大地损害系统交互的流畅感。
* **开发规范**：
  在 `qiwe_callback` 接收到回调消息并过滤出合法文本消息后，进行一次系统指令前置扫描：
  * **逻辑**：若 `message.text` 中包含特定的系统操作指令（如 `"发送本群入营问卷"`），则**完全绕过消息聚合器（Bypass Aggregator）**，不需要调用 `aggregator.enqueue(message)`，而是**直接且立即**异步调用 `aggregator.engine.handle_message(message)`。
  * **效果**：系统将在 0.5 秒内瞬间识别该硬编码指令，当即在微信群内发送专属评测问卷卡片，达成秒级响应！

