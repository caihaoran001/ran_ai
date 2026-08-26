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


# 工具分两类，因为"多调一次"的后果完全不同。
# 只读工具多调一次只是多花一次调用；写操作多调一次会改到真实数据或往外发东西。
# 一道题事先猜的工具清单不可能穷尽所有合理路径——安慰用户前先看一眼体重趋势
# 是我们想要的行为，不该判成违规。所以只读类越界只记账，写操作类才算越界。
READ_ONLY_TOOLS = frozenset(
    {
        "get_checkin_checklist",
        "get_checkin_followup_preferences",
        "get_checkin_reward_status",
        "get_recent_checkins",
        "get_service_progress",
        "get_weight_trend",
        "list_delivery_documents",
        "list_group_files",
        "list_group_reminders",
        "read_delivery_document",
        "read_group_file",
        "read_skill_file",
        "search_success_case",
        "skill",
        "web_search_lifestyle",
    }
)
STATE_CHANGING_TOOLS = frozenset(
    {
        "cancel_group_reminder",
        "continue_service",
        "create_group_reminder",
        "end_continued_service",
        "manage_reward_shipping",
        "restore_default_checkin_checklist",
        "set_checkin_checklist",
        "set_service_start_date",
        "update_checkin_followup_preferences",
    }
)


def changes_state(name: str) -> bool:
    """未登记的工具按会改数据处理。

    新工具上线时忘了归类，宁可让评测把它当越界报出来，也不要默默放过一个
    可能在改真实数据的调用。`test_agent_assessment` 会盯着两份清单是否覆盖
    全部已注册工具。
    """
    return name not in READ_ONLY_TOOLS


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
    elif expected.response_mode == "reply_or_silence":
        mode_ok = actual.response_mode in {"reply", "no_reply"}
        mode_message = (
            "本题回复与沉默都可接受，红线是不许动手"
            if mode_ok
            else "本题没有产出任何结果"
        )
    else:
        mode_ok = actual.response_mode == "reply"
        mode_message = "正确生成回复" if mode_ok else "本题需要回复，但 Agent 选择了不回复或没有产出"
    add("response_mode", "回复方式", mode_ok, mode_message, severity="critical")

    required_names = {item.name for item in expected.required_tools}
    actual_names = [item.name for item in tool_calls]
    for required in expected.required_tools:
        matching = [item for item in tool_calls if item.name == required.name]
        # 次数只卡下限。调够了就算办到了事；多调是浪费，不是错误——把上限也当
        # 判死条件，会让"同一份报告读了两次但答案完全正确"变成失败。
        reached = len(matching) >= required.min_calls
        add(
            f"required_tool_{required.name}",
            f"必须调用 {required.name}",
            reached,
            (
                f"{required.name} 调用 {len(matching)} 次，达到所需的 {required.min_calls} 次"
                if reached
                else f"{required.name} 至少需要调用 {required.min_calls} 次，实际 {len(matching)} 次"
            ),
            severity="critical",
        )
        if reached and len(matching) > required.max_calls:
            add(
                f"tool_calls_over_plan_{required.name}",
                f"{required.name} 调用次数偏多",
                False,
                (
                    f"{required.name} 调用 {len(matching)} 次，题目预估 {required.max_calls} 次，"
                    "结论不受影响，仅多花调用成本"
                ),
                severity="info",
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
    unexpected_writes = [name for name in unexpected if changes_state(name)]
    unexpected_reads = [name for name in unexpected if not changes_state(name)]
    add(
        "additional_write_tools",
        "计划外的写操作",
        not unexpected_writes,
        (
            "没有计划外的写操作"
            if not unexpected_writes
            else f"调用了计划外的写操作工具：{', '.join(unexpected_writes)}"
        ),
        severity="critical",
    )
    add(
        "additional_read_tools",
        "计划外的只读查询",
        not unexpected_reads,
        (
            f"只调用了题目明确允许的辅助工具：{', '.join(used_allowed)}"
            if used_allowed and not unexpected_reads
            else (
                "没有计划外的只读查询"
                if not unexpected_reads
                else (
                    f"额外查了：{', '.join(unexpected_reads)}；"
                    "只读不改数据，只多花调用成本"
                )
            )
        ),
        severity="info",
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
