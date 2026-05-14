from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from .access import _coerce_list

DEFAULT_WS_URL = "wss://openws.work.weixin.qq.com"


@dataclass
class WeComConfig:
    """Configuration for the WeCom AI Bot bridge.

    The standalone bridge is safe by default: DM and group policies default to
    allowlist, so empty allowlists block inbound messages.
    """

    bot_id: str = ""
    secret: str = ""
    websocket_url: str = DEFAULT_WS_URL
    dm_policy: str = "allowlist"
    allow_from: list[str] = field(default_factory=list)
    group_policy: str = "allowlist"
    group_allow_from: list[str] = field(default_factory=list)
    groups: dict[str, dict[str, Any]] = field(default_factory=dict)
    cache_dir: Path = Path(".wecom_cache")
    text_batch_delay_seconds: float = 0.6
    text_batch_split_delay_seconds: float = 2.0
    group_sessions_per_user: bool = True

    @classmethod
    def from_env(cls, *, load_dotenv_file: bool = True) -> "WeComConfig":
        if load_dotenv_file:
            load_dotenv()

        groups_raw = os.getenv("WECOM_GROUPS_JSON", "").strip()
        groups: dict[str, dict[str, Any]] = {}
        if groups_raw:
            try:
                parsed = json.loads(groups_raw)
                if isinstance(parsed, dict):
                    groups = {
                        str(key): value
                        for key, value in parsed.items()
                        if isinstance(value, dict)
                    }
            except json.JSONDecodeError:
                groups = {}

        return cls(
            bot_id=os.getenv("WECOM_BOT_ID", "").strip(),
            secret=os.getenv("WECOM_SECRET", "").strip(),
            websocket_url=(
                os.getenv("WECOM_WEBSOCKET_URL", DEFAULT_WS_URL).strip()
                or DEFAULT_WS_URL
            ),
            dm_policy=os.getenv("WECOM_DM_POLICY", "allowlist").strip().lower(),
            allow_from=_coerce_list(
                os.getenv("WECOM_ALLOWED_USERS")
                or os.getenv("WECOM_ALLOW_FROM")
                or ""
            ),
            group_policy=os.getenv("WECOM_GROUP_POLICY", "allowlist").strip().lower(),
            group_allow_from=_coerce_list(
                os.getenv("WECOM_GROUP_ALLOW_FROM")
                or os.getenv("WECOM_GROUP_ALLOWED_CHATS")
                or ""
            ),
            groups=groups,
            cache_dir=Path(os.getenv("WECOM_CACHE_DIR", ".wecom_cache")),
            text_batch_delay_seconds=float(
                os.getenv("WECOM_TEXT_BATCH_DELAY_SECONDS", "0.6")
            ),
            text_batch_split_delay_seconds=float(
                os.getenv("WECOM_TEXT_BATCH_SPLIT_DELAY_SECONDS", "2.0")
            ),
            group_sessions_per_user=os.getenv(
                "WECOM_GROUP_SESSIONS_PER_USER", "true"
            ).lower()
            not in {"0", "false", "no"},
        )

