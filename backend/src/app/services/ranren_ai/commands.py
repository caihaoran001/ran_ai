"""Explicit, non-AI group control commands."""

from __future__ import annotations


_QUESTIONNAIRE_COMMANDS = (
    "发送本群入营问卷",
    "发送本群专属问卷",
    "Send the group enrollment questionnaire",
)


def is_questionnaire_command(text: str) -> bool:
    """Return whether text explicitly asks the system to send this group's intake form."""
    normalized = text.casefold()
    return any(command.casefold() in normalized for command in _QUESTIONNAIRE_COMMANDS)
