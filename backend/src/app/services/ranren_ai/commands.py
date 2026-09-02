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


_ENGLISH_LANGUAGE_SWITCH_COMMANDS = (
    "请使用英文",
    "使用英文对话",
    "全程使用英文",
    "全程英文",
    "切换到英文",
    "切换为英文",
    "switch to english",
    "speak in english",
    "use english",
    "set language to english",
)

_CHINESE_LANGUAGE_SWITCH_COMMANDS = (
    "请使用中文",
    "使用中文对话",
    "全程使用中文",
    "全程中文",
    "切换到中文",
    "切换为中文",
    "switch to chinese",
    "speak in chinese",
    "use chinese",
    "set language to chinese",
)


def questionnaire_command_language(text: str) -> str:
    """Return language code ('en' or 'zh') for the questionnaire command."""
    normalized = text.casefold()
    if any(command.casefold() in normalized for command in _ENGLISH_QUESTIONNAIRE_COMMANDS):
        return "en"
    return "zh"


def is_language_switch_command(text: str) -> str | None:
    """Return 'en' or 'zh' if text matches an explicit language switch command, else None."""
    normalized = text.casefold()
    if any(command.casefold() in normalized for command in _ENGLISH_LANGUAGE_SWITCH_COMMANDS):
        return "en"
    if any(command.casefold() in normalized for command in _CHINESE_LANGUAGE_SWITCH_COMMANDS):
        return "zh"
    return None

