from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import random
import time
from typing import Any

import httpx

from app.core.config import Settings, get_settings
from app.services.channels.base import ChannelSendResult

logger = logging.getLogger("uvicorn.error")


class QiWeChannel:
    name = "qiwe"
    _nickname_cache: dict[str, str] = {}

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        http_client: httpx.AsyncClient | None = None,
    ):
        self.settings = settings or get_settings()
        self.token = self.settings.qiwei_token
        self.guid = self.settings.qiwei_guid
        self.base_url = self.settings.qiwei_base_url.rstrip("/")
        self._client = http_client
        self._last_send_time = 0.0
        self._send_lock = asyncio.Lock()
        # 机器人账号自身昵称缓存：避免每条消息都网络查；换号时随 apply_credentials 失效。
        self._self_nickname: str | None = None

    def apply_credentials(self, guid: str, token: str) -> None:
        """热切换账号凭证：原地更新 guid/token 并清空进程内昵称缓存。

        send_* / _post_json 每次调用都实时读取 self.guid / self.token，
        故原地更新即对后续收发生效，无需重启或替换 channel 对象。
        昵称缓存按账号（企业）维度失效，避免切号后返回旧账号解析到的昵称。
        """
        self.guid = (guid or "").strip()
        self.token = (token or "").strip()
        type(self)._nickname_cache.clear()
        self._self_nickname = None

    async def send_text(
        self,
        chat_id: str,
        content: str,
        *,
        reply_to: str | None = None,
        reply_context: Any = None,
    ) -> ChannelSendResult:
        if not self._ready:
            return ChannelSendResult(success=False, error="QIWEI_TOKEN or QIWEI_GUID is missing")

        params = {
            "guid": self.guid,
            "toId": str(chat_id),
            "content": content,
        }
        reply_payload = _reply_payload_from_context(reply_context, reply_to)
        if reply_payload:
            params["reply"] = reply_payload

        payload = {
            "method": "/msg/sendText",
            "params": params,
        }
        return await self._post_json("/api/qw/doApi", payload)

    async def send_document(
        self,
        chat_id: str,
        file_path: str,
        *,
        caption: str | None = None,
        reply_to: str | None = None,
        reply_context: Any = None,
    ) -> ChannelSendResult:
        if not self._ready:
            return ChannelSendResult(success=False, error="QIWEI_TOKEN or QIWEI_GUID is missing")
        path = Path(file_path)
        if not path.exists():
            return ChannelSendResult(success=False, error=f"file not found: {file_path}")

        upload = await self._upload_file(path)
        if not upload.success:
            return upload
        data = upload.raw_response.get("data") if isinstance(upload.raw_response, dict) else {}
        payload = {
            "method": "/msg/sendFile",
            "params": {
                "guid": self.guid,
                "toId": str(chat_id),
                "fileAesKey": data.get("fileAesKey"),
                "fileId": data.get("fileId"),
                "fileSize": data.get("fileSize"),
                "filename": data.get("filename") or path.name,
            },
        }
        result = await self._post_json("/api/qw/doApi", payload)
        if caption and result.success:
            await self.send_text(chat_id, caption, reply_to=reply_to, reply_context=reply_context)
        return result

    async def get_profile(self) -> dict[str, Any] | None:
        """查询当前 guid/token 对应企微账号(机器人自身)的资料。

        返回 /user/getProfile 的 data（含 nickname/alias/avatarUrl/realName 等），
        取不到或凭证无效/设备离线时返回 None——调用方据此判断账号是否可用。
        """
        if not self._ready:
            return None
        payload = {
            "method": "/user/getProfile",
            "params": {"guid": self.guid},
        }
        try:
            res = await self._post_json("/api/qw/doApi", payload, rate_limit=False)
        except Exception as exc:
            logger.error("QiWe get_profile failed for guid=%s: %s", self.guid, exc)
            return None
        if res.success and isinstance(res.raw_response, dict):
            data = res.raw_response.get("data")
            return data if isinstance(data, dict) else None
        return None

    async def get_self_nickname(self) -> str:
        """取机器人账号自身在群里显示的昵称（带进程内缓存）。

        供回复 Agent 注入身份用：让它知道"别人 @ 这个昵称就是在叫我"。
        取不到（凭证无效/离线）返回空串；缓存命中不发网络请求，换号后缓存已失效。
        """
        if self._self_nickname is not None:
            return self._self_nickname
        profile = await self.get_profile()
        nickname = ""
        if isinstance(profile, dict):
            nickname = str(profile.get("nickname") or "").strip()
        self._self_nickname = nickname
        return nickname

    async def get_contact_nickname(self, user_id: str) -> str:
        if not user_id:
            return ""
        user_id_str = str(user_id)
        if user_id_str in self._nickname_cache:
            return self._nickname_cache[user_id_str]

        if not self._ready:
            return ""

        payload = {
            "method": "/contact/batchGetUserinfo",
            "params": {
                "guid": self.guid,
                "userIdList": [user_id_str],
            },
        }
        try:
            logger.info("QiWe fetching nickname for user_id=%s from API", user_id_str)
            res = await self._post_json("/api/qw/doApi", payload, rate_limit=False)
            if res.success and isinstance(res.raw_response, dict):
                data = res.raw_response.get("data") or {}
                contact_list = data.get("contactList") or []
                for contact in contact_list:
                    c_id = str(contact.get("userId") or "")
                    c_name = str(contact.get("nickname") or "").strip()
                    if c_id and c_name:
                        self._nickname_cache[c_id] = c_name
            nickname = self._nickname_cache.get(user_id_str, "")
            logger.info("QiWe resolved nickname for user_id=%s: '%s'", user_id_str, nickname)
            return nickname
        except Exception as exc:
            logger.error("QiWe failed to fetch nickname for user_id=%s: %s", user_id_str, exc)
            return ""

    async def get_room_detail(self, room_id: str) -> dict[str, Any] | None:
        if not self._ready:
            return None
        payload = {
            "method": "/room/batchGetRoomDetail",
            "params": {
                "guid": self.guid,
                "roomIdList": [str(room_id)],
            },
        }
        res = await self._post_json("/api/qw/doApi", payload, rate_limit=False)
        if res.success and isinstance(res.raw_response, dict):
            return res.raw_response.get("data")
        return None

    async def get_room_member_nicknames(self, room_id: str) -> dict[str, str]:
        """取一次群详情并返回全部成员的群昵称，由业务层持久缓存。"""
        data = await self.get_room_detail(room_id)
        if not isinstance(data, dict):
            return {}
        result: dict[str, str] = {}
        for room in data.get("roomList") or []:
            if str(room.get("roomId") or "") != str(room_id):
                continue
            for member in room.get("memberList") or []:
                user_id = str(member.get("userId") or "").strip()
                name = str(member.get("name") or "").strip()
                if user_id and name:
                    result[user_id] = name
        return result

    async def batch_get_room_names(self, room_ids: list[str]) -> dict[str, str]:
        """批量取群名：返回 {room_id: roomName}，仅含非空名。

        取不到/空名的群（机器人已退/群解散）不出现在结果里，调用方据此保留旧值、不覆盖成空。
        分批 ≤50 调 /room/batchGetRoomDetail。
        """
        result: dict[str, str] = {}
        if not self._ready or not room_ids:
            return result
        unique_ids = [s for s in dict.fromkeys(str(r) for r in room_ids) if s]
        for start in range(0, len(unique_ids), 50):
            batch = unique_ids[start:start + 50]
            payload = {
                "method": "/room/batchGetRoomDetail",
                "params": {"guid": self.guid, "roomIdList": batch},
            }
            try:
                res = await self._post_json("/api/qw/doApi", payload, rate_limit=False)
            except Exception as exc:
                logger.error("QiWe batch_get_room_names failed for %d rooms: %s", len(batch), exc)
                continue
            if not (res.success and isinstance(res.raw_response, dict)):
                continue
            data = res.raw_response.get("data") or {}
            for room in data.get("roomList") or []:
                rid = str(room.get("roomId") or "")
                name = str(room.get("roomName") or "").strip()
                if rid and name:
                    result[rid] = name
        return result

    async def batch_get_user_info(self, user_ids: list[str]) -> list[dict[str, Any]] | None:
        if not self._ready or not user_ids:
            return None
        payload = {
            "method": "/contact/batchGetUserinfo",
            "params": {
                "guid": self.guid,
                "userIdList": [str(uid) for uid in user_ids],
            },
        }
        res = await self._post_json("/api/qw/doApi", payload, rate_limit=False)
        if res.success and isinstance(res.raw_response, dict):
            data = res.raw_response.get("data") or {}
            return data.get("contactList")
        return None

    async def send_hypertext(
        self,
        chat_id: str,
        content: list[dict[str, Any]],
        *,
        reply_to: str | None = None,
        reply_context: Any = None,
    ) -> ChannelSendResult:
        if not self._ready:
            return ChannelSendResult(success=False, error="QIWEI_TOKEN or QIWEI_GUID is missing")

        params = {
            "guid": self.guid,
            "toId": str(chat_id),
            "content": content,
        }
        reply_payload = _reply_payload_from_context(reply_context, reply_to)
        if reply_payload:
            params["reply"] = reply_payload

        payload = {
            "method": "/msg/sendHyperText",
            "params": params,
        }
        return await self._post_json("/api/qw/doApi", payload, rate_limit=True)

    @property
    def _ready(self) -> bool:
        return bool(self.token and self.guid)

    async def _execute_post_json(self, path: str, payload: dict[str, Any]) -> ChannelSendResult:
        try:
            async with self._client_context() as client:
                response = await client.post(
                    f"{self.base_url}{path}",
                    headers={
                        "X-QIWEI-TOKEN": self.token,
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            return ChannelSendResult(success=False, error=str(exc), retryable=True)
        return _qiwe_result(data)

    async def _post_json(self, path: str, payload: dict[str, Any], *, rate_limit: bool = True) -> ChannelSendResult:
        if not self._ready:
            return ChannelSendResult(success=False, error="QIWEI_TOKEN or QIWEI_GUID is missing")

        if not rate_limit:
            return await self._execute_post_json(path, payload)

        async with self._send_lock:
            now = time.monotonic()
            elapsed = now - self._last_send_time
            # 3.0s base delay + 0.5s to 1.5s random jitter
            target_delay = 3.0 + random.uniform(0.5, 1.5)
            if elapsed < target_delay:
                sleep_time = target_delay - elapsed
                logger.info(
                    "QiWe rate limit: sleeping for %.2f seconds before sending to chat_id=%s",
                    sleep_time,
                    payload.get("params", {}).get("toId") or "unknown",
                )
                await asyncio.sleep(sleep_time)

            self._last_send_time = time.monotonic()
            return await self._execute_post_json(path, payload)


    async def _upload_file(self, file_path: Path) -> ChannelSendResult:
        try:
            async with self._client_context() as client:
                with file_path.open("rb") as file_obj:
                    response = await client.post(
                        f"{self.base_url}/api/qw/doFileApi",
                        headers={"X-QIWEI-TOKEN": self.token},
                        data={
                            "method": "/cloud/cdnBigUpload",
                            "guid": self.guid,
                            "fileType": "5",
                        },
                        files={
                            "file": (
                                file_path.name,
                                file_obj,
                                "application/octet-stream",
                            )
                        },
                    )
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            return ChannelSendResult(success=False, error=str(exc), retryable=True)
        return _qiwe_result(data)

    def _client_context(self):
        if self._client is not None:
            return _BorrowedAsyncClient(self._client)
        return httpx.AsyncClient(timeout=30.0)


class _BorrowedAsyncClient:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def __aenter__(self) -> httpx.AsyncClient:
        return self.client

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


def _qiwe_result(data: dict[str, Any]) -> ChannelSendResult:
    success = data.get("code") == 0
    payload = data.get("data") if isinstance(data.get("data"), dict) else {}
    return ChannelSendResult(
        success=success,
        message_id=_message_id(payload) if success else None,
        error=None if success else str(data.get("msg") or data),
        raw_response=data,
        retryable=not success,
    )


def _message_id(payload: dict[str, Any]) -> str | None:
    for key in ("msgUniqueIdentifier", "msgServerId", "seq"):
        value = payload.get(key)
        if value is not None:
            return str(value)
    return None


def _reply_payload_from_context(context: Any, reply_to: str | None) -> dict[str, Any] | None:
    if not reply_to:
        return None
    item = _last_raw_item(context)
    if not isinstance(item, dict):
        return None
    msg_data = item.get("msgData") if isinstance(item.get("msgData"), dict) else {}
    content = str((msg_data or {}).get("content") or "").strip()
    if not content:
        return None
    return {
        "type": 0,
        "userId": str(item.get("senderId") or (msg_data or {}).get("sender_id") or ""),
        "showName": str(item.get("senderName") or (msg_data or {}).get("sender_name") or ""),
        "timeStamp": int(item.get("timestamp") or 0),
        "msgUniqueIdentifier": str(reply_to),
        "msgData": {"content": content},
    }


def _last_raw_item(context: Any) -> dict[str, Any] | None:
    if isinstance(context, list):
        for item in reversed(context):
            if isinstance(item, dict):
                return item
        return None
    if isinstance(context, dict):
        return context
    return None
