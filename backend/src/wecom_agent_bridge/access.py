from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


def _coerce_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _normalize_entry(raw: str) -> str:
    value = str(raw).strip()
    value = re.sub(r"^wecom:", "", value, flags=re.IGNORECASE)
    value = re.sub(r"^(user|group):", "", value, flags=re.IGNORECASE)
    return value.strip()


def _entry_matches(entries: list[str], target: str) -> bool:
    normalized_target = str(target or "").strip().lower()
    if not normalized_target:
        return False
    for entry in entries:
        normalized = _normalize_entry(entry).lower()
        if normalized == "*" or normalized == normalized_target:
            return True
    return False


@dataclass
class AccessPolicy:
    dm_policy: str = "allowlist"
    allow_from: list[str] = field(default_factory=list)
    group_policy: str = "allowlist"
    group_allow_from: list[str] = field(default_factory=list)
    groups: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class AdmissionDecision:
    allowed: bool
    reason: str = "allowed"


class WeComAccessController:
    """Small standalone access layer for inbound WeCom messages."""

    def __init__(self, policy: AccessPolicy):
        self.policy = policy

    @classmethod
    def from_config(cls, config: Any) -> "WeComAccessController":
        return cls(
            AccessPolicy(
                dm_policy=str(getattr(config, "dm_policy", "allowlist")).lower(),
                allow_from=_coerce_list(getattr(config, "allow_from", [])),
                group_policy=str(getattr(config, "group_policy", "allowlist")).lower(),
                group_allow_from=_coerce_list(getattr(config, "group_allow_from", [])),
                groups=getattr(config, "groups", {}) or {},
            )
        )

    def admit(self, *, chat_type: str, chat_id: str, sender_id: str) -> AdmissionDecision:
        if chat_type == "group":
            allowed = self.is_group_allowed(chat_id, sender_id)
            return AdmissionDecision(allowed, "allowed" if allowed else "group_policy")
        allowed = self.is_dm_allowed(sender_id)
        return AdmissionDecision(allowed, "allowed" if allowed else "dm_policy")

    def is_dm_allowed(self, sender_id: str) -> bool:
        policy = (self.policy.dm_policy or "allowlist").lower()
        if policy == "open":
            return True
        if policy == "disabled":
            return False
        if policy == "pairing":
            logger.info("WeCom pairing mode is not implemented in this standalone bridge")
            return False
        if policy == "allowlist":
            return _entry_matches(self.policy.allow_from, sender_id)
        logger.warning("Unknown WeCom DM policy %r; blocking by default", policy)
        return False

    def is_group_allowed(self, chat_id: str, sender_id: str) -> bool:
        policy = (self.policy.group_policy or "allowlist").lower()
        if policy == "disabled":
            return False
        if policy == "allowlist" and not _entry_matches(
            self.policy.group_allow_from, chat_id
        ):
            return False
        if policy not in {"open", "allowlist"}:
            logger.warning("Unknown WeCom group policy %r; blocking by default", policy)
            return False

        group_cfg = self.resolve_group_config(chat_id)
        sender_allow = _coerce_list(group_cfg.get("allow_from") or group_cfg.get("allowFrom"))
        if sender_allow:
            return _entry_matches(sender_allow, sender_id)
        return True

    def resolve_group_config(self, chat_id: str) -> dict[str, Any]:
        groups = self.policy.groups
        if not isinstance(groups, dict):
            return {}
        if chat_id in groups and isinstance(groups[chat_id], dict):
            return groups[chat_id]
        lowered = str(chat_id).lower()
        for key, value in groups.items():
            if isinstance(key, str) and key.lower() == lowered and isinstance(value, dict):
                return value
        wildcard = groups.get("*")
        return wildcard if isinstance(wildcard, dict) else {}

