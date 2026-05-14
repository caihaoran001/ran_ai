from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


class MessageType(str, Enum):
    """Normalized inbound message types."""

    TEXT = "text"
    PHOTO = "photo"
    VIDEO = "video"
    AUDIO = "audio"
    VOICE = "voice"
    DOCUMENT = "document"


@dataclass
class ChatSource:
    """Where a WeCom message came from."""

    chat_id: str
    chat_type: str = "dm"
    user_id: Optional[str] = None
    user_name: Optional[str] = None
    chat_name: Optional[str] = None
    platform: str = "wecom"

    def session_key(self, *, group_sessions_per_user: bool = True) -> str:
        if self.chat_type == "dm":
            return f"{self.platform}:dm:{self.chat_id}"
        parts = [self.platform, self.chat_type, self.chat_id]
        if group_sessions_per_user and self.user_id:
            parts.append(self.user_id)
        return ":".join(parts)


@dataclass
class WeComMessage:
    """Normalized inbound message produced by the bridge."""

    text: str
    source: ChatSource
    message_type: MessageType = MessageType.TEXT
    raw_payload: Any = None
    message_id: Optional[str] = None
    media_paths: list[str] = field(default_factory=list)
    media_types: list[str] = field(default_factory=list)
    reply_to_message_id: Optional[str] = None
    reply_to_text: Optional[str] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def is_command(self) -> bool:
        return self.text.startswith("/")

    def get_command(self) -> Optional[str]:
        if not self.is_command():
            return None
        raw = self.text.split(maxsplit=1)[0][1:].lower()
        if "@" in raw:
            raw = raw.split("@", 1)[0]
        if "/" in raw:
            return None
        return raw or None


@dataclass
class SendResult:
    """Result of sending a WeCom message."""

    success: bool
    message_id: Optional[str] = None
    error: Optional[str] = None
    raw_response: Any = None
    retryable: bool = False


@dataclass
class BridgeEvent:
    """Operational event emitted by the bridge/runtime."""

    kind: str
    message: str
    data: Any = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

