# QiWe 开放平台接口集成开发指南 (Ran_AI 1.0)

本项目提供了一个绝对独立、安全的测试环境目录 `qiwe_test/`，用于研究和联调 **QiWe 开放平台**（基于企业微信 iPad/Mac 客户端协议的第三方自动化网关）。

本指南汇总了**获取个人信息**、**发送文本消息**、**发送文件消息**三大核心功能的 API 调用契约、回调数据结构及架构集成蓝图，以便于其他 AI 助手或开发人员能够零门槛、零差错地为本项目开发并拓展全新的 `QiWeChannel` 消息通道。

---

## 🧭 目录导航
* [一、核心全局配置](#一核心全局配置)
* [二、三大核心接口调用契约](#二三大核心接口调用契约)
  * [1. 获取个人信息接口](#1-获取个人信息接口)
  * [2. 发送纯文本消息接口 (踩坑必看)](#2-发送纯文本消息接口-踩坑必看)
  * [3. 发送文档文件消息接口 (两步流水线)](#3-发送文档文件消息接口-两步流水线)
* [三、Webhook 接收端事件结构详解](#三webhook-接收端事件结构详解)
* [四、Ran_AI 1.0 架构集成蓝图](#四ran_ai-10-架构集成蓝图)

---

## 一、核心全局配置

在调用任何接口前，必须先在控制台或环境配置文件 `.env` 中保存以下全局变量：
* **`X-QIWEI-TOKEN`**：应用鉴权令牌。必须作为 **HTTP Request Header** 传入。
* **`guid`**：已登录的虚拟设备实例 ID。必须作为所有的请求 `params` 字段或表单字段传入。
* **统一接口网关**:
  * **常规 API 路由**: `http://manager.qiweapi.com/qiwe/api/qw/doApi` (处理 JSON POST 请求)
  * **媒体/文件上传路由**: `http://manager.qiweapi.com/qiwe/api/qw/doFileApi` (处理 `multipart/form-data` 上传请求)

---

## 二、三大核心接口调用契约

### 1. 获取个人信息接口
用于查询当前登录设备实例所挂载的企业微信账号详情（昵称、真实姓名、userId、corpId、手机号等）。

* **请求路由**: `POST /api/qw/doApi`
* **Request Headers**:
  ```http
  X-QIWEI-TOKEN: <YOUR_TOKEN>
  Content-Type: application/json
  ```
* **Request Body (JSON)**:
  ```json
  {
      "method": "/user/getProfile",
      "params": {
          "guid": "<YOUR_GUID>"
      }
  }
  ```
* **Response Body (JSON)**:
  ```json
  {
      "code": 0,
      "msg": "成功",
      "data": {
          "userId": "1688855528918944",      // 用户在企业内部的唯一标识
          "nickname": "chr",                 // 微信昵称
          "realName": "蔡浩然",               // 真实姓名
          "corpId": "1970326587021882",      // 所属企业 ID
          "mobile": "13337591349",           // 绑定手机号
          "acctid": "chr",
          "gender": 1,
          "groupId": "2251802628074282",
          "unionId": "ozynqsrWrw4pv-njDiIk7A_AiQ_I"
      }
  }
  ```

---

### 2. 发送纯文本消息接口 (踩坑必看)
用于向指定联系人或群聊发送纯文本消息。

> [!CAUTION]
> **官方文档拼写 Typo 避坑指南：**
> 官方的快速入门文档中，将接收方字段印成了 `"told"`。**经实测，底层的真实参数名称必须为 `"toId"`（大写的 I）**！如果写成 `"told"`，接口将报 `[-2003[]] 交互异常` 错误。

> [!WARNING]
> **协议限制提示：**
> 在此客户端协议中，**禁止向自己本身的 userId 发送消息**（会报 `[-2003[]]` 错误）。测试发送时，请务必传入真实好友的 userId 或合法的群聊 roomId。

* **请求路由**: `POST /api/qw/doApi`
* **Request Headers**:
  ```http
  X-QIWEI-TOKEN: <YOUR_TOKEN>
  Content-Type: application/json
  ```
* **Request Body (JSON)**:
  ```json
  {
      "method": "/msg/sendText",
      "params": {
          "guid": "<YOUR_GUID>",
          "toId": "<RECIPIENT_USER_ID_OR_ROOM_ID>",  // 接收方微信ID/企业内部ID/群聊RoomID
          "content": "这是一条测试消息内容"
      }
  }
  ```
* **Response Body (JSON)**:
  ```json
  {
      "code": 0,
      "msg": "成功",
      "data": {
          "isSendSuccess": 1,
          "msgServerId": 1000171,
          "msgType": 2,
          "msgUniqueIdentifier": "CIGABBCW0N/QBhign4OPlYCAAyDihdaLAQ==",
          "seq": 8425447,
          "timestamp": 1779951638
      }
  }
  ```

---

### 3. 发送文档文件消息接口 (两步流水线)
发送本地文件（如 `.xlsx`、`.pdf`、`.txt` 等文档）无法直接传文件路径，必须分为**两步流**完成：

#### 🛠️ 步骤 3-1: 上传本地文件到微信 CDN
* **请求路由**: `POST /api/qw/doFileApi`
* **Request Headers**:
  ```http
  X-QIWEI-TOKEN: <YOUR_TOKEN>
  # Content-Type 必须为 multipart/form-data，由客户端/HTTP库自动生成 boundary
  ```
* **Multipart Form Fields (表单数据)**:
  * `method` (text): `"/cloud/cdnBigUpload"` (必填，调用的上传方法)
  * `guid` (text): `"<YOUR_GUID>"` (必填)
  * `fileType` (text): `"5"` (必填！**1 代表 JPG图片，4 代表 MP4视频，5 代表 文件文档**。若将文档传为 1 会因图片解码失败报错)
  * `file` (file): 本地文件的二进制流 (关联文件名)
* **Response Body (JSON)**:
  ```json
  {
      "code": 0,
      "msg": "成功",
      "data": {
          "fileAesKey": "2753028fe92b49eab94031363d8cc0d1", // 文件解密密钥 (重要)
          "fileId": "306a02010204633061020100020451...",      // 微信系统内的唯一文件ID (重要)
          "fileSize": 128,                                  // 文件大小 (字节)
          "filename": "test_doc.txt"                        // 关联的文件名
      }
  }
  ```

#### 🚀 步骤 3-2: 调用 /msg/sendFile 发送文件消息
将第一步返回的 `fileAesKey` 和 `fileId` 拼装到 JSON 中，向统一网关发送：

* **请求路由**: `POST /api/qw/doApi`
* **Request Headers**:
  ```http
  X-QIWEI-TOKEN: <YOUR_TOKEN>
  Content-Type: application/json
  ```
* **Request Body (JSON)**:
  ```json
  {
      "method": "/msg/sendFile",
      "params": {
          "guid": "<YOUR_GUID>",
          "toId": "<RECIPIENT_USER_ID_OR_ROOM_ID>",
          "fileAesKey": "<STEP_1_RETURNED_fileAesKey>",
          "fileId": "<STEP_1_RETURNED_fileId>",
          "fileSize": <STEP_1_RETURNED_fileSize>,         // 必须为整型 (int)
          "filename": "<STEP_1_RETURNED_filename>"
      }
  }
  ```
* **Response Body (JSON)**:
  ```json
  {
      "code": 0,
      "msg": "成功",
      "data": {
          "isSendSuccess": 1,
          "msgServerId": 1000173,
          "msgType": 15, // 15 代表文件类型的消息
          "msgUniqueIdentifier": "CIGABBCC1d/QBhign4OPlYCAAyDmhdaLAQ=="
      }
  }
  ```

---

## 三、Webhook 接收端事件结构详解

在 QiWe 控制台配置了回调 URL 后，用户的微信收发任何消息，平台都会以 **HTTP POST (JSON)** 的方式实时推送给您的接收服务器。

### 1. 消息格式基类
所有推送过来的 Body 正文都具有以下外层包结构：
```json
{
  "code": 0,
  "msg": "成功",
  "data": [
    {
      "TenantId": 0,
      "guid": "2c70c6f3-b8dc-46f7-978e-e241e6882a09",
      "userId": "1688855528918944", // 机器人微信号的 userId
      "cmd": 15000,                  // 事件大类码
      "fromRoomId": 10926518308089720, // 核心！群聊此值不为 0；单聊此值绝对为 0
      "msgType": 2,                  // 核心！消息子类型
      "msgUniqueIdentifier": "5769685921402320854",
      "senderId": 7881300658070689,  // 实际发送该消息的微信用户 ID
      "senderName": "",
      "timestamp": 1779951380,
      "msgData": {
          // 伴随 msgType 变化的具体消息正文
      }
    }
  ]
}
```

### 2. 核心大类码 (`cmd` 分类)
* **`11016`**：**账号状态变动**。如：登录成功（code=11001，status=2）、离线或被顶号。
* **`15000`**：**常规聊天消息**。普通文本、图片、文件、语音均在此类。
* **`15500`**：**微信系统变动事件**。如：有人入群、退群、修改群名等。

### 3. 常规聊天消息类型 (`cmd = 15000` 时的 `msgType` 对照表)
| `msgType` 值 | 消息类型 (新版v2字符串) | `msgData` 内部结构与重点字段 |
| :--- | :--- | :--- |
| **0** 或 **2** | `TEXT`, `TEXT_ALT` | `{"content": "文本内容"}` |
| **7 / 14 / 101** | `IMAGE` | `{"cdnUrl": "公网图片链接", "fileSize": 1204}` |
| **15 / 20 / 102** | `FILE` | `{"fileName": "表.xlsx", "cdnUrl": "下载链接", "fileSize": 1302}` |
| **29** 或 **104** | `GIF` | `{"filename": "动画表情", "fileHttpUrl": "表情GIF链接"}` |
| **16** | `VOICE` | `{"voiceTime": 3, "fileMd5": "xxx"}`（格式默认为 `.silk` 语音包） |

---

## 四、Ran_AI 1.0 架构集成蓝图

要将 QiWe 通道接入本项目并替换现有的 `WECOM` 通道，可以完全继承现有的设计。以下是供另一个 AI 助手编写代码时的架构设计蓝图：

### 1. 实现 `MessageChannel` 协议 (推荐文件名: `app/services/channels/qiwe.py`)
该类应实现 `name: str`、`send_text()`、`send_document()` 协议方法。使用异步的 `httpx.AsyncClient()` 替换同步请求：
```python
# 核心伪代码设计
class QiWeChannel:
    name = "qiwe"
    
    def __init__(self):
        self.token = os.getenv("QIWEI_TOKEN")
        self.guid = os.getenv("QIWEI_GUID")
        self.base_url = "http://manager.qiweapi.com/qiwe"
        self.headers = {"X-QIWEI-TOKEN": self.token}

    async def send_text(self, chat_id: str, content: str, **kwargs) -> ChannelSendResult:
        # 1. 封装 JSON 格式调用 doApi 的 /msg/sendText (注意使用 toId 传参)
        # 2. 返回 ChannelSendResult
        
    async def send_document(self, chat_id: str, file_path: str, **kwargs) -> ChannelSendResult:
        # 1. 第一步：调用 doFileApi 的 /cloud/cdnBigUpload 接口 (注意 fileType="5")
        # 2. 第二步：拿到 fileId 和 fileAesKey，调用 doApi 的 /msg/sendFile
        # 3. 返回 ChannelSendResult
```

### 2. 编写 FastAPI 接收端 (推荐路由: `POST /api/v1/channels/qiwe/callback`)
用来替代 WebSocket 消息监听，改为被动 HTTP 接收推送：
```python
# 核心逻辑伪代码设计
@router.post("/qiwe/callback")
async def handle_qiwe_webhook(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()
    if payload.get("code") != 0: 
        return "" # 快速返回，防止超时
        
    for msg in payload.get("data", []):
        # 过滤只处理文本消息 (cmd=15000, msgType=2)
        if msg.get("cmd") == 15000 and msg.get("msgType") in (0, 2):
            msg_data = msg.get("msgData", {})
            
            # 判断消息来源
            from_room_id = msg.get("fromRoomId")
            is_group = from_room_id != 0
            chat_id = str(from_room_id) if is_group else msg.get("userId")
            
            # 拼装为项目标准的 ChannelMessage
            channel_msg = ChannelMessage(
                channel="qiwe",
                chat_id=chat_id,
                chat_type="group" if is_group else "private",
                text=msg_data.get("content", ""),
                message_id=msg.get("msgUniqueIdentifier"),
                user_id=str(msg.get("senderId")),
                user_name=msg.get("senderName") or "微信用户"
            )
            
            # 🚀 必须放入 FastAPI 的 background_tasks 执行大模型 AI 处理
            # 避免 LLM 回复较慢导致 API 响应超过 3 秒，被 QiWe 平台判定超时重发
            background_tasks.add_task(run_engine, channel_msg)
            
    return "" # 返回空字符或 200 OK 给 QiWe 平台即可
```

### 3. 全局通道的切换
在正式替换时，只需在启动时：
1. 实例化 `QiWeChannel()`。
2. 调用 `set_current_channel(qiwe_channel)`，将全局通道切换为 QiWe。
3. 现有的所有核心逻辑（大模型提示词调用、打卡解析、问卷收集等）均可在**不做任何代码改动**的前提下，顺畅无比地在 QiWe 渠道中正常运作！
