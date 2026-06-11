# QiWe API Integration & Debugging Guide

This guide details how to interact with the **QiWe API Gateway**, fetch group/member details, query chat history, and debug credentials. Use this to align other AI assistants (e.g., Claude) on how the integration is structured.

---

## 🔑 1. QiWe API Authentication & Base Gateway

All requests to the QiWe API gateway must use the following configuration:

* **Base URL**: `http://manager.qiweapi.com/qiwe/api/qw/doApi`
* **HTTP Header**:
  ```http
  X-QIWEI-TOKEN: <token_from_env>
  Content-Type: application/json
  ```

> [!WARNING]
> **GUID Verification**:
> The `QIWEI_GUID` is a unique device instance identifier representing the active WeCom iPad/Mac client. If the target assistant complains that `QIWEI_GUID` is incorrect, it might be due to:
> 1. The token or guid in `.env` is stale (e.g., the virtual device went offline).
> 2. They are running locally but the backend container has not restarted to pick up the updated `.env` credentials. (Remember: running `docker restart ran-ai-backend` is mandatory after updates).

---

## 🛠️ 2. Core API Methods

To execute an API query, send a **POST** request to the Base URL with a JSON body specifying `method` and `params`.

### A. Fetch Group Members (`/room/batchGetRoomDetail`)
Used to retrieve all member IDs and group nickname info for a specific group.

* **Payload**:
  ```json
  {
    "method": "/room/batchGetRoomDetail",
    "params": {
      "guid": "YOUR_GUID",
      "roomIdList": ["10893638395118043"]
    }
  }
  ```
* **Response Structure**:
  Returns a `roomList` containing `memberList`. Each member has a `userId` (WeCom User ID) and a group display `name`.

### B. Fetch Global Contact Nicknames (`/contact/batchGetUserinfo`)
Used to retrieve WeCom global nicknames and avatar URLs for a list of `userId`s.

* **Payload**:
  ```json
  {
    "method": "/contact/batchGetUserinfo",
    "params": {
      "guid": "YOUR_GUID",
      "userIdList": ["7881300647007710", "7881301778907147"]
    }
  }
  ```
* **Response Structure**:
  Returns a `contactList` containing objects with `userId` and their global `nickname` (e.g. `"奢啡加速燃营养师-勇哥"`).

---

## 📂 3. Diagnosing Chat History & Database Records

To inspect chat histories and verify if DND exclusion rules are working:

### A. Query Remote Production Database
Production data is stored in Aliyun RDS. To query it from the local machine:
1. Connect via SSH to the remote ECS instance:
   * **Host**: `43.98.170.61`
   * **Username**: `root`
   * **Password**: `ranAI3621715280`
2. Execute a Python script inside the production Docker container `ran_ai-backend-1`.
   For example, run this command to inspect the latest messages:
   ```bash
   sshpass -p 'ranAI3621715280' ssh -o StrictHostKeyChecking=no root@43.98.170.61 "docker exec ran_ai-backend-1 uv run python -c \"
   import asyncio
   from sqlalchemy import select
   from app.db.session import SessionLocal
   from app.models.message import Message
   async def run():
       async with SessionLocal() as session:
           stmt = select(Message).order_by(Message.id.desc()).limit(10)
           res = await session.execute(stmt)
           for m in res.scalars().all():
               print(f'MsgID: {m.id} | SenderID: {m.analysis_json.get(\\\"_qiwe_meta\\\", {}).get(\\\"sender_id\\\")} | SenderName: {m.analysis_json.get(\\\"_qiwe_meta\\\", {}).get(\\\"sender_name\\\")} | Content: {m.content}')
   asyncio.run(run())
   \""
   ```

### B. Use Pre-written Scratch Scripts
Several scripts exist in the [scratch/](file:///home/chr/桌面/ran_ai/scratch/) directory to help check logs and database states:
* [check_all_messages_names.py](file:///home/chr/桌面/ran_ai/scratch/check_all_messages_names.py): Connects to remote ECS and prints the last 50 messages with their sender IDs and nicknames.
* [query_remote_db.py](file:///home/chr/桌面/ran_ai/scratch/query_remote_db.py): Connects to remote ECS and queries both the DND settings and the messages within Case `RAI-20260611-024A7F`.
* [query_latest_messages.py](file:///home/chr/桌面/ran_ai/scratch/query_latest_messages.py): Run this locally inside the `backend/` folder (`uv run python ../scratch/query_latest_messages.py`) to query the local development SQLite/PostgreSQL database.
