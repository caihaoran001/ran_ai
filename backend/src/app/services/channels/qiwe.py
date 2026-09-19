from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import logging
from pathlib import Path
import random
import time
from typing import Any

import httpx

from app.core.config import Settings, get_settings
from app.services.channels.base import ChannelSendResult
from app.services.channels.send_context import notify_send_request_started

logger = logging.getLogger("uvicorn.error")


@dataclass(frozen=True, slots=True)
class QiWeHistoryPage:
    items: list[dict[str, Any]]
    has_more: bool
    travel_sync_key: int | str | None


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

        return await self._post_with_quote_fallback("/msg/sendText", params)

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
                "fileMd5": data.get("fileMd5") or _file_md5_hex(path),
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

    async def sync_message_history(
        self,
        *,
        after_seq: int,
        limit: int = 50,
    ) -> QiWeHistoryPage:
        """Read one bounded page of archived QiWe messages after ``after_seq``.

        This is a public channel-adapter operation for startup reconciliation.
        Callers deliberately own pagination policy; the current recovery flow
        always requests one page capped at 50 records.
        """
        if after_seq < 0:
            raise ValueError("after_seq must be non-negative")
        if limit < 1 or limit > 50:
            raise ValueError("limit must be between 1 and 50")
        payload = {
            "method": "/msg/syncMsg",
            "params": {
                "guid": self.guid,
                "msgSeq": after_seq,
                "limit": limit,
            },
        }
        result = await self._post_json(
            "/api/qw/doApi",
            payload,
            rate_limit=False,
        )
        if not result.success:
            raise RuntimeError(result.error or "QiWe history sync failed")
        raw = result.raw_response if isinstance(result.raw_response, dict) else {}
        data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
        raw_items = data.get("syncMsgList")
        items = [item for item in (raw_items or []) if isinstance(item, dict)]
        return QiWeHistoryPage(
            items=items,
            has_more=bool(data.get("hasMore")),
            travel_sync_key=data.get("travelSyncKey"),
        )

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

    async def batch_get_room_members(self, room_ids: list[str]) -> dict[str, set[str]]:
        """批量取群成员名单：返回 {room_id: {成员企微号}}，只含真的取到人的群。

        取不到、或成员列表为空的群（机器人已被移出、群解散、接口抖动）**不出现在结果里**，
        调用方据此跳过——否则一次接口异常就会被误读成"全员退群"。
        分批 ≤50 调 /room/batchGetRoomDetail，与取群名同一个接口。
        """
        result: dict[str, set[str]] = {}
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
                logger.error("QiWe batch_get_room_members failed for %d rooms: %s", len(batch), exc)
                continue
            if not (res.success and isinstance(res.raw_response, dict)):
                continue
            data = res.raw_response.get("data") or {}
            for room in data.get("roomList") or []:
                rid = str(room.get("roomId") or "")
                if not rid:
                    continue
                members = {
                    str(member.get("userId") or "").strip()
                    for member in (room.get("memberList") or [])
                }
                members.discard("")
                if members:
                    result[rid] = members
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

        return await self._post_with_quote_fallback("/msg/sendHyperText", params)

    async def send_feed_video(
        self,
        chat_id: str,
        feed: dict[str, Any],
    ) -> ChannelSendResult:
        """发送视频号原生卡片；字段来自 msgType=141 入站回调。"""
        if not self._ready:
            return ChannelSendResult(success=False, error="QIWEI_TOKEN or QIWEI_GUID is missing")

        required = (
            "channelName",
            "channelUrl",
            "coverUrl",
            "encodeData",
            "headImgUrl",
            "feedId",
            "feedNo",
            "username",
        )
        missing = [key for key in required if not feed.get(key)]
        if missing:
            return ChannelSendResult(
                success=False,
                error=f"video channel payload missing fields: {', '.join(missing)}",
            )
        params = {key: feed[key] for key in required}
        params.update({"guid": self.guid, "toId": str(chat_id)})
        return await self._post_json(
            "/api/qw/doApi",
            {"method": "/msg/sendFeedVideo", "params": params},
            rate_limit=True,
        )

    async def transcribe_voice(
        self,
        msg_server_id: Any,
        *,
        guid: str | None = None,
        timeout_seconds: float = 8.0,
        poll_interval_seconds: float = 0.5,
    ) -> str | None:
        """把一条入站语音转成文字（企微客户端自带能力，网关代转，不计费）。

        两步：voiceToTextApply 拿 voiceId → voiceToTextQuery 轮询到 isEnd。
        生产实测 3 秒语音总耗时约 1.3 秒，稳定落在聚合窗口内。

        guid 必须传**收到这条语音的那台设备**（回调 payload 自带），不能用全局
        self.guid：账号换过号后两者会不一致，用错设备转写必然失败。缺省才回落自身。

        任何失败（未就绪/网关报错/超时/异常）一律返回 None，由调用方退回“只留痕
        不回复”的原行为——绝不因为转不出文字就中断整条入站链路。
        """
        if not self.token:
            return None
        device_guid = (guid or self.guid or "").strip()
        if not device_guid or msg_server_id in (None, ""):
            return None

        try:
            msg_server_id = int(msg_server_id)
        except (TypeError, ValueError):
            logger.warning("QiWe voice transcribe: bad msgServerId=%r", msg_server_id)
            return None

        apply_res = await self._post_json(
            "/api/qw/doApi",
            {
                "method": "/msg/voiceToTextApply",
                "params": {"guid": device_guid, "msgServerId": msg_server_id},
            },
            rate_limit=False,
        )
        if not apply_res.success or not isinstance(apply_res.raw_response, dict):
            logger.warning(
                "QiWe voice transcribe apply failed msg_server_id=%s error=%s",
                msg_server_id,
                apply_res.error,
            )
            return None
        voice_id = str(
            (apply_res.raw_response.get("data") or {}).get("voiceId") or ""
        ).strip()
        if not voice_id:
            logger.warning(
                "QiWe voice transcribe apply returned no voiceId msg_server_id=%s",
                msg_server_id,
            )
            return None

        deadline = time.monotonic() + max(0.0, timeout_seconds)
        while True:
            query_res = await self._post_json(
                "/api/qw/doApi",
                {
                    "method": "/msg/voiceToTextQuery",
                    "params": {
                        "guid": device_guid,
                        "msgServerId": msg_server_id,
                        "voiceId": voice_id,
                    },
                },
                rate_limit=False,
            )
            if query_res.success and isinstance(query_res.raw_response, dict):
                data = query_res.raw_response.get("data") or {}
                text = str(data.get("text") or "").strip()
                if data.get("isEnd"):
                    if text:
                        return text
                    logger.info(
                        "QiWe voice transcribe finished empty msg_server_id=%s",
                        msg_server_id,
                    )
                    return None
            elif not query_res.success:
                logger.warning(
                    "QiWe voice transcribe query failed msg_server_id=%s error=%s",
                    msg_server_id,
                    query_res.error,
                )
                return None
            if time.monotonic() + poll_interval_seconds >= deadline:
                logger.warning(
                    "QiWe voice transcribe timed out msg_server_id=%s timeout_seconds=%.1f",
                    msg_server_id,
                    timeout_seconds,
                )
                return None
            await asyncio.sleep(poll_interval_seconds)

    @property
    def _ready(self) -> bool:
        return bool(self.token and self.guid)

    async def _execute_post_json(self, path: str, payload: dict[str, Any]) -> ChannelSendResult:
        # For outbound message sends, the engine binds a task-local hook that
        # moves the durable row PENDING -> SENDING here: after queue/rate-limit
        # waiting, immediately before the external HTTP request.
        await notify_send_request_started()
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
            return ChannelSendResult(
                success=False,
                error=str(exc),
                retryable=True,
                delivery_uncertain=True,
            )
        return _qiwe_result(data)

    async def _post_with_quote_fallback(
        self, method: str, params: dict[str, Any]
    ) -> ChannelSendResult:
        """先带引用发；新平台拒收引用时（明确失败、未实际发出）降级重发纯文本。

        新版要求 reply.type 与被引用消息类型一致、msgData 结构匹配，
        不可引用的消息会整单失败。失败是平台明确返回的业务失败，
        消息并未发出，此时去掉引用重试一次是安全的。
        """
        reply_attached = "reply" in params
        result = await self._post_json("/api/qw/doApi", {"method": method, "params": params})
        if (
            reply_attached
            and result is not None
            and not result.success
            and not result.delivery_uncertain
        ):
            logger.warning(
                "QiWe send with quote rejected (error=%s); retrying without quote",
                result.error,
            )
            plain_params = {key: value for key, value in params.items() if key != "reply"}
            result = await self._post_json(
                "/api/qw/doApi", {"method": method, "params": plain_params}, rate_limit=False
            )
        return result

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
            content = file_path.read_bytes()
            async with self._client_context() as client:
                response = await client.post(
                    f"{self.base_url}/api/qw/doApi",
                    headers={"X-QIWEI-TOKEN": self.token},
                    data={
                        "method": "/cloud/cdnBigUpload",
                        "guid": self.guid,
                        "fileType": "5",
                        "filename": file_path.name,
                        "fileMd5": hashlib.md5(content).hexdigest(),
                    },
                    files={
                        "file": (
                            file_path.name,
                            content,
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


def _file_md5_hex(file_path: Path) -> str:
    digest = hashlib.md5()
    with file_path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
