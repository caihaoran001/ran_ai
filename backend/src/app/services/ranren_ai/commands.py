"""Explicit, non-AI group control commands."""

from __future__ import annotations


_ENGLISH_QUESTIONNAIRE_COMMANDS = (
    "send the group enrollment questionnaire",
    "send the enrollment questionnaire",
    "send group enrollment questionnaire",
)

_CHINESE_QUESTIONNAIRE_COMMANDS = (
    "发送本群入营问卷",
    "发送本群专属问卷",
    "发送入营问卷",
)

_QUESTIONNAIRE_COMMANDS = (
    *_CHINESE_QUESTIONNAIRE_COMMANDS,
    *_ENGLISH_QUESTIONNAIRE_COMMANDS,
)


def is_questionnaire_command(text: str) -> bool:
    """Return whether text explicitly asks the system to send this group's intake form."""
    normalized = text.casefold()
    return any(command.casefold() in normalized for command in _QUESTIONNAIRE_COMMANDS)


def questionnaire_command_language(text: str) -> str:
    """Return language code ('en' or 'zh') for the questionnaire command."""
    normalized = text.casefold()
    if any(command.casefold() in normalized for command in _ENGLISH_QUESTIONNAIRE_COMMANDS):
        return "en"
    return "zh"
