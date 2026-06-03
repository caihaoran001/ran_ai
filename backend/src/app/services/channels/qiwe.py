from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from app.core.config import Settings, get_settings
from app.services.channels.base import ChannelSendResult


class QiWeChannel:
    name = "qiwe"

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

    @property
    def _ready(self) -> bool:
        return bool(self.token and self.guid)

    async def _post_json(self, path: str, payload: dict[str, Any]) -> ChannelSendResult:
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
