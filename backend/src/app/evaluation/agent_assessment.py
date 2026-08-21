from __future__ import annotations

import re
from typing import Any

from app.schemas.agent_evaluation import (
    AgentCaseActual,
    AgentCaseExpected,
    AgentDeterministicCheck,
    AgentExpectedToolCall,
    AgentFailureReason,
    AgentToolCallEvidence,
)


_NO_REPLY_RE = re.compile(r"<\s*no_reply\s*/?\s*>", re.IGNORECASE)


def response_mode(response: str | None) -> str:
    if not response:
        return "unavailable"
    if _NO_REPLY_RE.search(response):
        return "no_reply"
    return "reply"


def assess_agent_case(
    *,
    expected: AgentCaseExpected,
    response: str | None,
    tool_calls: list[AgentToolCallEvidence],
) -> tuple[AgentCaseActual, list[AgentDeterministicCheck], list[AgentFailureReason]]:
    actual = AgentCaseActual(
        response_mode=response_mode(response),
        response=response,
        tool_calls=tool_calls,
    )
    checks: list[AgentDeterministicCheck] = []
    failures: list[AgentFailureReason] = []

    def add(
        code: str,
        label: str,
        passed: bool,
        message: str,
        *,
        severity: str = "warning",
    ) -> None:
        checks.append(
            AgentDeterministicCheck(
                code=code,
                label=label,
                passed=passed,
                message=message,
                severity=severity,
            )
        )
        if not passed:
            failures.append(
                AgentFailureReason(
                    stage="deterministic",
                    code=code,
                    message=message,
                    severity="critical" if severity == "critical" else "warning",
                )
            )

    if expected.response_mode == "no_reply":
        mode_ok = actual.response_mode == "no_reply"
        mode_message = "正确选择不回复" if mode_ok else "本题应当不回复，但 Agent 生成了正文"
    else:
        mode_ok = actual.response_mode == "reply"
        mode_message = "正确生成回复" if mode_ok else "本题需要回复，但 Agent 选择了不回复或没有产出"
    add("response_mode", "回复方式", mode_ok, mode_message, severity="critical")

    required_names = {item.name for item in expected.required_tools}
    actual_names = [item.name for item in tool_calls]
    for required in expected.required_tools:
        matching = [item for item in tool_calls if item.name == required.name]
        count_ok = required.min_calls <= len(matching) <= required.max_calls
        add(
            f"required_tool_{required.name}",
            f"必须调用 {required.name}",
            count_ok,
            (
                f"{required.name} 调用 {len(matching)} 次，符合预期"
                if count_ok
                else f"{required.name} 期望调用 {required.min_calls}—{required.max_calls} 次，实际 {len(matching)} 次"
            ),
            severity="critical",
        )
        args_ok = bool(matching) and any(
            _tool_args_match(item.args, required) for item in matching
        )
        add(
            f"tool_args_{required.name}",
            f"{required.name} 关键参数",
            args_ok,
            (
                f"{required.name} 的关键参数符合预期"
                if args_ok
                else (
                    f"{required.name} 没有一次调用符合预期参数："
                    f"固定字段 {required.args_subset}，文本规则 "
                    f"{required.string_args}"
                )
            ),
            severity="critical",
        )

    forbidden_hits = sorted(set(actual_names) & set(expected.forbidden_tools))
    add(
        "forbidden_tools",
        "禁止工具",
        not forbidden_hits,
        "没有调用禁止工具" if not forbidden_hits else f"调用了禁止工具：{', '.join(forbidden_hits)}",
        severity="critical",
    )

    unexpected = []
    if not expected.allow_additional_tools:
        permitted = (
            required_names
            | set(expected.forbidden_tools)
            | set(expected.allowed_additional_tools)
        )
        unexpected = sorted({name for name in actual_names if name not in permitted})
    used_allowed = sorted(set(actual_names) & set(expected.allowed_additional_tools))
    add(
        "additional_tools",
        "额外工具",
        not unexpected,
        (
            f"只调用了题目明确允许的辅助工具：{', '.join(used_allowed)}"
            if used_allowed and not unexpected
            else (
                "没有计划外工具调用"
                if not unexpected
                else f"调用了计划外工具：{', '.join(unexpected)}"
            )
        ),
        severity="warning",
    )

    clean_response = response or ""
    missing_literals = [item for item in expected.required_literals if item not in clean_response]
    forbidden_literals = [item for item in expected.forbidden_literals if item in clean_response]
    add(
        "required_literals",
        "关键事实原文",
        not missing_literals,
        "关键事实均已出现" if not missing_literals else f"缺少关键事实：{'；'.join(missing_literals)}",
        severity="critical",
    )
    add(
        "forbidden_literals",
        "禁止事实原文",
        not forbidden_literals,
        "没有出现禁止事实" if not forbidden_literals else f"出现禁止内容：{'；'.join(forbidden_literals)}",
        severity="critical",
    )
    return actual, checks, failures


def _contains_subset(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and _contains_subset(actual[key], value)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(
            _contains_subset(actual_item, expected_item)
            for actual_item, expected_item in zip(actual, expected, strict=True)
        )
    return actual == expected


def _tool_args_match(
    actual: dict[str, Any], expected: AgentExpectedToolCall
) -> bool:
    if not _contains_subset(actual, expected.args_subset):
        return False
    for name, rule in expected.string_args.items():
        value = actual.get(name)
        if not isinstance(value, str):
            return False
        if any(item not in value for item in rule.required_literals):
            return False
        if any(
            not any(item in value for item in group)
            for group in rule.required_any_groups
        ):
            return False
        if any(item in value for item in rule.forbidden_literals):
            return False
    return True
