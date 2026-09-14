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

Production data is stored in Aliyun RDS and is reachable from the ECS backend container. Use the configured SSH authentication; do not put passwords or access tokens in this guide or shell commands. The following read-only example lists recent message IDs and times without printing message content:

```bash
ssh root@43.98.170.61

docker exec -i ran_ai-backend-1 uv run python - <<'PYCODE'
import asyncio
from sqlalchemy import select
from app.db.session import SessionLocal
from app.models.message import Message

async def main():
    async with SessionLocal() as session:
        result = await session.execute(
            select(Message.id, Message.created_at)
            .order_by(Message.id.desc())
            .limit(10)
        )
        for message_id, created_at in result.all():
            print(message_id, created_at)

asyncio.run(main())
PYCODE
```

Check actual model fields and the relevant service-case scope before adapting the query. Local `SessionLocal()` scripts use the local development database and cannot verify production records.
