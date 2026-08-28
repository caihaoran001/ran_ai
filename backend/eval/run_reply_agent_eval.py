from __future__ import annotations

import argparse
import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, TypeVar

from app.core.config import get_settings
from app.db.session import SessionLocal, engine
from app.evaluation.reply_agent_cases import (
    ReplyAgentEvalCase,
    ReplyAgentEvalSuite,
    default_reply_agent_eval_path,
    load_reply_agent_eval_suite,
    summarize_reply_agent_eval_suite,
)
from app.evaluation.rag_cases import retrieval_clauses_for
from app.schemas.agent_evaluation import AgentEvaluationCaseResult
from app.schemas.rag_evaluation import RagEvaluationCaseResult
from app.schemas.reply_agent_evaluation import (
    ReplyAgentActual,
    ReplyAgentAverageMetric,
    ReplyAgentEvaluationCaseResult,
    ReplyAgentEvaluationCounts,
    ReplyAgentEvaluationCoverage,
    ReplyAgentBoundaryViolation,
    ReplyAgentEvaluationMetrics,
    ReplyAgentEvaluationRunDetail,
    ReplyAgentEvaluationSummary,
    ReplyAgentExpected,
    ReplyAgentFailureReason,
    ReplyAgentMetricResult,
    ReplyAgentLowScore,
    ReplyAgentRateMetric,
    ReplyAgentRulerPolicy,
    ReplyAgentUsageTotals,
)
from app.services.message_insight_service import analyze_message_insight
from app.services.ranren_ai.engine import _requires_health_handoff


REAL_CASE_CONCURRENCY = 8
TCase = TypeVar("TCase")
TResult = TypeVar("TResult")

if __package__:
    from .agent_costs import AgentEvaluationCostLedger, install_agent_usage_collector
    from .agent_judge import (
        TASK_COMPLETION_CALIBRATION_ID,
        TASK_COMPLETION_POLICY_VERSION,
        TASK_COMPLETION_THRESHOLD,
        AgentQualityJudge,
    )
    from .deepeval_eval import (
        DEEPEVAL_VERSION,
        GATE_CONTEXT_PRECISION,
        GATE_CONTEXT_RECALL,
        GATE_FAITHFULNESS,
        DeepEvalScorer,
    )
    from .faq_provenance_check import check_faq_provenance, format_drift_report
    from .rag_costs import RagEvaluationCostLedger, install_rag_usage_collectors
    from .read_only import install_read_only_guards
    from .reporting import atomic_write_json
    from .rewrite_harm import (
        REWRITE_HARM_POLICY_VERSION,
        REWRITE_RECALL_DROP_THRESHOLD,
        assess_rewrite_harm,
    )
    from .run_agent_eval import _mock_results as mock_agent_results
    from .run_agent_eval import _real_result as real_agent_result
    from .run_full_eval import _evaluate_real_case, _mock_cases
else:
    from agent_costs import AgentEvaluationCostLedger, install_agent_usage_collector
    from agent_judge import (
        TASK_COMPLETION_CALIBRATION_ID,
        TASK_COMPLETION_POLICY_VERSION,
        TASK_COMPLETION_THRESHOLD,
        AgentQualityJudge,
    )
    from deepeval_eval import (
        DEEPEVAL_VERSION,
        GATE_CONTEXT_PRECISION,
        GATE_CONTEXT_RECALL,
        GATE_FAITHFULNESS,
        DeepEvalScorer,
    )
    from faq_provenance_check import check_faq_provenance, format_drift_report
    from rag_costs import RagEvaluationCostLedger, install_rag_usage_collectors
    from read_only import install_read_only_guards
    from reporting import atomic_write_json
    from rewrite_harm import (
        REWRITE_HARM_POLICY_VERSION,
        REWRITE_RECALL_DROP_THRESHOLD,
        assess_rewrite_harm,
    )
    from run_agent_eval import _mock_results as mock_agent_results
    from run_agent_eval import _real_result as real_agent_result
    from run_full_eval import _evaluate_real_case, _mock_cases


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _zero_metrics() -> ReplyAgentEvaluationMetrics:
    return ReplyAgentEvaluationMetrics(
        task_pass_rate=ReplyAgentRateMetric(),
        retrieval_decision_accuracy=ReplyAgentRateMetric(),
        rewrite_harm_rate=ReplyAgentRateMetric(),
        context_recall_pass_rate=ReplyAgentRateMetric(),
        context_precision_pass_rate=ReplyAgentRateMetric(),
        faithfulness_pass_rate=ReplyAgentRateMetric(),
        tool_selection_accuracy=ReplyAgentRateMetric(),
        argument_accuracy=ReplyAgentRateMetric(),
        behavior_accuracy=ReplyAgentRateMetric(),
        handoff_accuracy=ReplyAgentRateMetric(),
        answer_correctness_observed=ReplyAgentRateMetric(),
        answer_relevancy_average=ReplyAgentAverageMetric(),
        context_recall_average=ReplyAgentAverageMetric(),
        context_precision_average=ReplyAgentAverageMetric(),
        faithfulness_average=ReplyAgentAverageMetric(),
        answer_correctness_average=ReplyAgentAverageMetric(),
        judge_task_completion_observed=ReplyAgentRateMetric(),
        judge_groundedness_observed=ReplyAgentRateMetric(),
        judge_clarification_observed=ReplyAgentRateMetric(),
        judge_expression_observed=ReplyAgentRateMetric(),
    )


def _coverage(suite: ReplyAgentEvalSuite) -> ReplyAgentEvaluationCoverage:
    summary = summarize_reply_agent_eval_suite(suite)
    return ReplyAgentEvaluationCoverage(
        retrieval_counts=summary["retrieval_counts"],
        execution_counts=summary["execution_counts"],
        metric_group_counts=summary["metric_group_counts"],
        retrieval_family_counts=summary["retrieval_family_counts"],
    )


def _base_summary(
    *,
    run_id: str,
    suite: ReplyAgentEvalSuite,
    mock: bool,
) -> ReplyAgentEvaluationSummary:
    now = utc_now()
    settings = get_settings()
    return ReplyAgentEvaluationSummary(
        run_id=run_id,
        run_mode="mock" if mock else "real",
        status="running",
        started_at=now,
        updated_at=now,
        git_sha=(
            os.getenv("REPLY_AGENT_EVAL_GIT_SHA")
            or os.getenv("RAG_EVAL_GIT_SHA")
            or "unknown"
        )[:64],
        dataset_id=suite.suite_id,
        dataset_version=suite.version,
        model="mock-model" if mock else settings.ai_model,
        evaluation_framework="deepeval",
        evaluation_framework_version=DEEPEVAL_VERSION,
        ruler_policy=ReplyAgentRulerPolicy(
            retrieval_reference_policy_version="atomic-clause-v3",
            rewrite_harm_policy_version=REWRITE_HARM_POLICY_VERSION,
            rewrite_recall_drop_threshold=REWRITE_RECALL_DROP_THRESHOLD,
            task_completion_policy_version=TASK_COMPLETION_POLICY_VERSION,
            task_completion_threshold=TASK_COMPLETION_THRESHOLD,
            task_completion_calibration_id=TASK_COMPLETION_CALIBRATION_ID,
        ),
        counts=ReplyAgentEvaluationCounts(
            total=len(suite.cases),
            completed=0,
            passed=0,
            failed=0,
            errors=0,
        ),
        coverage=_coverage(suite),
        metrics=_zero_metrics(),
        usage=ReplyAgentUsageTotals(),
    )


def _metric(
    *,
    key: str,
    label: str,
    group: str,
    kind: str,
    score: float | None,
    threshold: float | None,
    passed: bool | None,
    reason: str | None,
    enforced: bool = True,
) -> ReplyAgentMetricResult:
    return ReplyAgentMetricResult(
        key=key,
        label=label,
        group=group,
        kind=kind,
        score=score,
        threshold=threshold,
        passed=passed,
        enforced=enforced,
        reason=reason,
    )


def _expected(case: ReplyAgentEvalCase) -> ReplyAgentExpected:
    if case.rag_case:
        mode = case.rag_case.answer.response_mode
        tools: list[str] = []
        handoff = False
    elif case.agent_case:
        mode = case.agent_case.expected.response_mode
        tools = [item.name for item in case.agent_case.expected.required_tools]
        handoff = False
    else:
        mode = "handoff"
        tools = []
        handoff = True
    return ReplyAgentExpected(
        retrieval_decision=case.retrieval_decision,
        response_mode=mode,
        metric_groups=case.metric_groups,
        retrieval_families=case.retrieval_families,
        required_tools=tools,
        requires_handoff=handoff,
    )


def _rate(passed: int, total: int) -> ReplyAgentRateMetric:
    return ReplyAgentRateMetric(
        value=(round(passed / total, 6) if total else None),
        numerator=passed,
        denominator=total,
    )


def _metric_rate(
    cases: list[ReplyAgentEvaluationCaseResult],
    key: str,
) -> ReplyAgentRateMetric:
    matches = [
        metric
        for case in cases
        for metric in case.metrics
        if metric.key == key and metric.passed is not None
    ]
    return _rate(sum(metric.passed is True for metric in matches), len(matches))


def _metric_average(
    cases: list[ReplyAgentEvaluationCaseResult],
    key: str,
) -> ReplyAgentAverageMetric:
    values = [
        metric.score
        for case in cases
        for metric in case.metrics
        if metric.key == key and metric.score is not None
    ]
    return ReplyAgentAverageMetric(
        value=(round(sum(values) / len(values), 6) if values else None),
        denominator=len(values),
        minimum=(min(values) if values else None),
        zero_count=sum(1 for value in values if value == 0.0),
    )


def _rewrite_harm_rate(
    cases: list[ReplyAgentEvaluationCaseResult],
) -> ReplyAgentRateMetric:
    matches = [
        metric
        for case in cases
        for metric in case.metrics
        if metric.key == "rewrite_harm" and metric.passed is not None
    ]
    return _rate(sum(metric.passed is False for metric in matches), len(matches))


def _behavior_rate(
    cases: list[ReplyAgentEvaluationCaseResult],
) -> ReplyAgentRateMetric:
    applicable = [case for case in cases if "MB" in case.expected.metric_groups]
    passed = 0
    for case in applicable:
        metrics = [
            metric
            for metric in case.metrics
            if metric.group == "MB" and metric.enforced and metric.passed is not None
        ]
        passed += bool(metrics) and all(metric.passed for metric in metrics)
    return _rate(passed, len(applicable))


def _usage_total(cases: list[ReplyAgentEvaluationCaseResult]) -> ReplyAgentUsageTotals:
    totals: list[dict[str, Any]] = []
    for case in cases:
        total = case.usage.get("total") if isinstance(case.usage, dict) else None
        if isinstance(total, dict):
            totals.append(total)
    known_cost = round(
        sum(float(item.get("known_estimated_cost_yuan") or 0) for item in totals),
        8,
    )
    complete = all(
        bool(item.get("complete", True)) and item.get("estimated_cost_yuan") is not None
        for item in totals
    )
    return ReplyAgentUsageTotals(
        call_count=sum(int(item.get("call_count") or 0) for item in totals),
        prompt_tokens=sum(int(item.get("prompt_tokens") or 0) for item in totals),
        completion_tokens=sum(
            int(item.get("completion_tokens") or 0) for item in totals
        ),
        total_tokens=sum(int(item.get("total_tokens") or 0) for item in totals),
        prompt_cache_hit_tokens=sum(
            int(item.get("prompt_cache_hit_tokens") or 0) for item in totals
        ),
        known_estimated_cost_yuan=known_cost,
        estimated_cost_yuan=known_cost if complete else None,
        complete=complete,
    )


def aggregate_summary(
    *,
    base: ReplyAgentEvaluationSummary,
    cases: list[ReplyAgentEvaluationCaseResult],
    status: str = "completed",
    error: str | None = None,
) -> ReplyAgentEvaluationSummary:
    completed = sum(case.status in {"passed", "failed"} for case in cases)
    usage = _usage_total(cases)
    summary = base.model_copy(deep=True)
    summary.status = status
    summary.updated_at = utc_now()
    summary.finished_at = summary.updated_at if status != "running" else None
    summary.error = error
    summary.counts = ReplyAgentEvaluationCounts(
        total=base.counts.total,
        completed=completed,
        passed=sum(case.status == "passed" for case in cases),
        failed=sum(case.status == "failed" for case in cases),
        errors=sum(case.status == "error" for case in cases),
    )
    summary.metrics = ReplyAgentEvaluationMetrics(
        task_pass_rate=_rate(
            sum(case.status == "passed" for case in cases),
            len(cases),
        ),
        retrieval_decision_accuracy=_metric_rate(cases, "retrieval_decision"),
        rewrite_harm_rate=_rewrite_harm_rate(cases),
        context_recall_pass_rate=_metric_rate(cases, "context_recall"),
        context_precision_pass_rate=_metric_rate(cases, "context_precision"),
        faithfulness_pass_rate=_metric_rate(cases, "faithfulness"),
        tool_selection_accuracy=_metric_rate(cases, "tool_selection"),
        argument_accuracy=_metric_rate(cases, "argument_accuracy"),
        behavior_accuracy=_behavior_rate(cases),
        handoff_accuracy=_metric_rate(cases, "handoff_required"),
        answer_correctness_observed=_metric_rate(cases, "answer_correctness"),
        answer_relevancy_average=_metric_average(cases, "answer_relevancy"),
        context_recall_average=_metric_average(cases, "context_recall"),
        context_precision_average=_metric_average(cases, "context_precision"),
        faithfulness_average=_metric_average(cases, "faithfulness"),
        answer_correctness_average=_metric_average(cases, "answer_correctness"),
        judge_task_completion_observed=_metric_rate(cases, "judge_task_completion"),
        judge_groundedness_observed=_metric_rate(cases, "judge_groundedness"),
        judge_clarification_observed=_metric_rate(cases, "judge_clarification"),
        judge_expression_observed=_metric_rate(cases, "judge_expression"),
    )
    summary.boundary_violations = _boundary_violations(cases)
    summary.retrieval_returned_nothing = _boundary_violations(
        cases, only_code="retrieval_returned_nothing"
    )
    summary.lowest_scores = _lowest_scores(cases)
    # 显式区分“本批汇总后确实为空”和“旧报告生成时根本没有记录这些字段”。
    # 不能用列表是否为空推断，否则历史报告会被页面误报成“本批没有问题”。
    summary.diagnostic_rollups_recorded = True
    summary.usage = usage
    summary.estimated_cost_yuan = usage.estimated_cost_yuan
    return summary


def _boundary_violations(
    cases: list[ReplyAgentEvaluationCaseResult],
    *,
    only_code: str | None = None,
) -> list[ReplyAgentBoundaryViolation]:
    """踩了非黑即白红线的题。这是报告的第一份名单——人应该先看这几道。"""
    flagged: list[ReplyAgentBoundaryViolation] = []
    for case in cases:
        hits = [
            item
            for item in case.failure_reasons
            if item.severity == "critical"
            and (only_code is None or item.code == only_code)
        ]
        if not hits:
            continue
        flagged.append(
            ReplyAgentBoundaryViolation(
                case_id=case.case_id,
                title=case.title,
                codes=sorted({item.code for item in hits}),
                detail="；".join(item.message for item in hits)[:2000],
            )
        )
    return flagged


def _lowest_scores(
    cases: list[ReplyAgentEvaluationCaseResult],
) -> list[ReplyAgentLowScore]:
    """所有低于参考线的题，最差在前——不只是零分题。

    两个知识点的题漏一个是 0.5 分，在平均分里不显眼，业务上却是"该说的一半
    没说"（只说了漏喝直接跳过、没说不要集中补喝，用户就会去补两份）。这类
    才是最常见的真实失败。
    """
    rows = [
        ReplyAgentLowScore(
            case_id=case.case_id,
            title=case.title,
            metric_key=metric.key,
            metric_label=metric.label,
            score=metric.score,
            reference=metric.threshold,
            reason=(metric.reason or None),
        )
        for case in cases
        for metric in case.metrics
        if metric.kind == "llm"
        and metric.score is not None
        and metric.threshold is not None
        and metric.score < metric.threshold
    ]
    return sorted(rows, key=lambda item: (item.score, item.case_id, item.metric_key))


def _failure_severity(value: str) -> str:
    return value if value in {"info", "warning", "critical"} else "warning"


def _recall_clause_count(case: ReplyAgentEvalCase) -> int:
    """本题召回实际按几个子句评分（即这道题的分母）。

    门槛 0.75 是在平均 4 子句的校准集上选的，但正式题库里多数题只有 2~3 个
    子句：2 子句题漏一句就是 0.50，等价于"一句都不能漏"。分母不写进报告，
    读报告的人无从判断某道题的低分是判官抖动还是真漏检。
    """
    rag_case = case.rag_case
    if rag_case is None:
        return 0
    fact_by_id = {fact.fact_id: fact for fact in rag_case.authoritative_facts}
    total = 0
    for fact_id in rag_case.retrieval.required_fact_ids:
        fact = fact_by_id.get(fact_id)
        if fact is not None:
            total += len(retrieval_clauses_for(fact))
    return total


def _with_clause_denominator(reason: str | None, clause_count: int) -> str | None:
    """把本题召回分母写进判分理由，方便人读报告时判断一格值多少分。"""
    if clause_count <= 0:
        return reason
    note = f"[本题召回分母：{clause_count} 个子句，漏 1 句 ≈ -{1 / clause_count:.2f}]"
    return f"{note} {reason}" if reason else note


def adapt_rag_result(
    case: ReplyAgentEvalCase,
    result: RagEvaluationCaseResult,
) -> ReplyAgentEvaluationCaseResult:
    decision_ok = result.actual.retrieval_decision == case.retrieval_decision
    behavior_group = "MB" if "MB" in case.metric_groups else "M0"
    metrics = [
        _metric(
            key="retrieval_decision",
            label="知识检索决策",
            group="M0",
            kind="deterministic",
            score=1.0 if decision_ok else 0.0,
            threshold=1.0,
            passed=decision_ok,
            reason=(
                f"期望 {case.retrieval_decision}，实际 "
                f"{result.actual.retrieval_decision or 'unavailable'}"
            ),
        ),
        _metric(
            key="response_delivery",
            label="回复方式与交付",
            group=behavior_group,
            kind="deterministic",
            score=1.0 if result.actual.response else 0.0,
            threshold=1.0,
            passed=bool(result.actual.response),
            reason="已生成最终回复" if result.actual.response else "未生成最终回复",
        ),
    ]
    if case.retrieval_decision == "retrieve":
        raw_recall = result.scores.raw_context_recall
        rewritten_recall = result.scores.context_recall
        assessment = assess_rewrite_harm(
            original_query=result.user_query,
            rewritten_query=result.actual.knowledge_query,
            raw_hit_count=len(result.actual.raw_query_hits),
            rewritten_hit_count=len(result.actual.rewritten_query_hits),
            raw_recall=raw_recall,
            rewritten_recall=rewritten_recall,
        )
        if assessment.applicable:
            metrics.append(
                _metric(
                    key="rewrite_harm",
                    label="查询改写损伤",
                    group="MR",
                    kind="deterministic",
                    score=assessment.score,
                    threshold=REWRITE_RECALL_DROP_THRESHOLD,
                    passed=(
                        None
                        if assessment.signal == "missing_recall_scores"
                        else not assessment.harmed
                    ),
                    reason=assessment.reason,
                )
            )
        specs = (
            (
                "context_recall",
                "上下文召回率",
                result.scores.context_recall,
                GATE_CONTEXT_RECALL,
                _with_clause_denominator(
                    result.judge_reasons.get("context_recall"),
                    _recall_clause_count(case),
                ),
            ),
            (
                "context_precision",
                "上下文精确率",
                result.scores.context_precision,
                GATE_CONTEXT_PRECISION,
                result.judge_reasons.get("context_precision"),
            ),
            (
                "faithfulness",
                "回复忠实度",
                result.scores.faithfulness,
                GATE_FAITHFULNESS,
                result.judge_reasons.get("faithfulness"),
            ),
        )
        for key, label, score, threshold, reason in specs:
            metrics.append(
                _metric(
                    key=key,
                    label=label,
                    group="MR",
                    kind="llm",
                    score=score,
                    threshold=threshold,
                    # 阈值降为参考线：仍然标红提示"低于这条线"，但不再决定整题成败。
                    # 这套评测没有接任何自动流水线，是人手启动、人读报告，
                    # 判死没有对象；判死反而会让一次判官抖动改写结论。
                    passed=(score >= threshold if score is not None else None),
                    reason=reason,
                    enforced=False,
                )
            )
        metrics.append(
            _metric(
                key="answer_relevancy",
                label="原生答案相关性（诊断）",
                group="MR",
                kind="llm",
                score=result.scores.answer_relevancy,
                threshold=None,
                passed=None,
                reason=result.judge_reasons.get("answer_relevancy"),
                enforced=False,
            )
        )
    if result.scores.answer_correctness is not None:
        metrics.append(
            _metric(
                key="answer_correctness",
                label="回答正确与完整度（观察）",
                group="M0",
                kind="llm",
                score=result.scores.answer_correctness,
                threshold=0.5,
                passed=result.scores.answer_correctness >= 0.5,
                reason=result.scores.answer_correctness_reason,
                enforced=False,
            )
        )
    return ReplyAgentEvaluationCaseResult(
        case_id=case.case_id,
        title=case.title,
        source_case_ids=case.source_case_ids,
        execution_kind=case.execution_kind,
        tags=case.tags,
        status="error" if result.status == "error" else result.status,
        user_query=result.user_query,
        expected=_expected(case),
        actual=ReplyAgentActual(
            retrieval_decision=result.actual.retrieval_decision,
            knowledge_query=result.actual.knowledge_query,
            response_mode="reply" if result.actual.response else None,
            response=result.actual.response,
            tool_names=[],
        ),
        metrics=metrics,
        failure_reasons=[
            ReplyAgentFailureReason(
                stage=item.stage,
                code=item.code,
                message=item.message,
                severity=_failure_severity(item.severity),
            )
            for item in result.failure_reasons
        ],
        evidence={"rag": result.model_dump(mode="json", exclude={"usage"})},
        usage=result.usage.model_dump(mode="json"),
        elapsed_ms=result.elapsed_ms,
    )


def adapt_agent_result(
    case: ReplyAgentEvalCase,
    result: AgentEvaluationCaseResult,
) -> ReplyAgentEvaluationCaseResult:
    check_by_code = {item.code: item for item in result.deterministic_checks}
    response_check = check_by_code.get("response_mode")
    tool_checks = [
        item
        for item in result.deterministic_checks
        if item.code.startswith("required_tool_")
        or item.code in {"forbidden_tools", "additional_write_tools"}
    ]
    tool_notes = [
        item
        for item in result.deterministic_checks
        if not item.passed
        and (
            item.code.startswith("tool_calls_over_plan_")
            or item.code == "additional_read_tools"
        )
    ]
    literal_checks = [
        item
        for item in result.deterministic_checks
        if item.code in {"required_literals", "forbidden_literals"}
    ]
    arg_checks = [
        item
        for item in result.deterministic_checks
        if item.code.startswith("tool_args_")
    ]
    behavior_group = "MB" if "MB" in case.metric_groups else "M0"
    metrics = [
        _metric(
            key="retrieval_decision",
            label="知识检索决策",
            group="M0",
            kind="deterministic",
            score=1.0,
            threshold=1.0,
            passed=True,
            reason="本题按固定契约跳过通用知识库",
        ),
        _metric(
            key="response_delivery",
            label="回复方式与交付",
            group=behavior_group,
            kind="deterministic",
            score=(1.0 if response_check and response_check.passed else 0.0),
            threshold=1.0,
            passed=(response_check.passed if response_check else None),
            reason=(response_check.message if response_check else None),
        ),
    ]
    if "MT" in case.metric_groups:
        tool_passed = bool(tool_checks) and all(item.passed for item in tool_checks)
        args_passed = all(item.passed for item in arg_checks) if arg_checks else True
        metrics.extend(
            [
                _metric(
                    key="tool_selection",
                    label="工具选择",
                    group="MT",
                    kind="deterministic",
                    score=1.0 if tool_passed else 0.0,
                    threshold=1.0,
                    passed=tool_passed,
                    reason="；".join(
                        item.message for item in (*tool_checks, *tool_notes)
                    ),
                ),
                _metric(
                    key="argument_accuracy",
                    label="工具参数",
                    group="MT",
                    kind="deterministic",
                    score=1.0 if args_passed else 0.0,
                    threshold=1.0,
                    passed=args_passed,
                    reason=(
                        "；".join(item.message for item in arg_checks)
                        if arg_checks
                        else "本题没有必填工具参数"
                    ),
                ),
            ]
        )
    if literal_checks:
        # 这一项能判死整题（RA-041 就栽在"回复里必须出现编号 71"），过去只在
        # 行为边界题里露面，其余题九项指标全绿却整题失败，看报告的人无从下手。
        # 凡是能判死的检查，必须有自己的一行。
        literals_passed = all(item.passed for item in literal_checks)
        metrics.append(
            _metric(
                key="key_facts",
                label="关键事实原文",
                group=behavior_group,
                kind="deterministic",
                score=1.0 if literals_passed else 0.0,
                threshold=1.0,
                passed=literals_passed,
                reason="；".join(item.message for item in literal_checks),
            )
        )
    if "MB" in case.metric_groups:
        behavior_checks = [
            item
            for item in result.deterministic_checks
            if item.code
            in {
                "response_mode",
                "required_literals",
                "forbidden_literals",
                "forbidden_tools",
                "additional_write_tools",
            }
        ]
        behavior_passed = bool(behavior_checks) and all(
            item.passed for item in behavior_checks
        )
        metrics.append(
            _metric(
                key="behavior_contract",
                label="行为边界",
                group="MB",
                kind="deterministic",
                score=1.0 if behavior_passed else 0.0,
                threshold=1.0,
                passed=behavior_passed,
                reason="；".join(item.message for item in behavior_checks),
            )
        )
    for item in result.judge.framework_metrics:
        group = "MT" if item.key == "tool_correctness" else behavior_group
        metrics.append(
            _metric(
                key=f"judge_{item.key}",
                label=item.label,
                group=group,
                kind=item.kind,
                score=item.score,
                threshold=item.threshold,
                passed=item.passed,
                reason=item.reason,
                # 任务完成度也降为诊断。它的判分标准里含"必须调用某工具"，
                # 而它只拿到回复正文、看不到调用记录，只能猜——8/23 有 5 道
                # 正确回复被它判 0 分。工具调用由确定性核对负责，那份能看见记录。
                enforced=False,
            )
        )
    failure_reasons = [
        ReplyAgentFailureReason(
            stage=item.stage,
            code=item.code,
            message=item.message,
            severity=item.severity,
        )
        for item in result.failure_reasons
    ]
    return ReplyAgentEvaluationCaseResult(
        case_id=case.case_id,
        title=case.title,
        source_case_ids=case.source_case_ids,
        execution_kind=case.execution_kind,
        tags=case.tags,
        status=(
            "error"
            if result.status == "error"
            else (
                "failed" if result.status == "failed" else "passed"
            )
        ),
        user_query=result.user_query,
        expected=_expected(case),
        actual=ReplyAgentActual(
            retrieval_decision="skip",
            response_mode=result.actual.response_mode,
            response=result.actual.response,
            tool_names=[item.name for item in result.actual.tool_calls],
        ),
        metrics=metrics,
        failure_reasons=failure_reasons,
        evidence={"agent": result.model_dump(mode="json", exclude={"usage"})},
        usage=result.usage.model_dump(mode="json"),
        elapsed_ms=result.elapsed_ms,
    )


def adapt_handoff_result(
    case: ReplyAgentEvalCase,
    *,
    analysis: dict[str, Any],
    usage: dict[str, Any],
) -> ReplyAgentEvaluationCaseResult:
    assert case.handoff is not None
    actual_codes = {
        str(item.get("code"))
        for item in analysis.get("risk_flags", [])
        if isinstance(item, dict) and item.get("code")
    }
    actual_codes.update(
        str(item.get("code"))
        for item in analysis.get("intervention_reasons", [])
        if isinstance(item, dict) and item.get("code")
    )
    handoff = bool(
        analysis.get("requires_human_intervention")
    ) or _requires_health_handoff(
        analysis,
        "high" if "pregnancy" in actual_codes else None,
    )
    risk_match = bool(actual_codes & set(case.handoff.expected_risk_codes))
    passed = handoff and risk_match
    failures = []
    if not handoff:
        failures.append(
            ReplyAgentFailureReason(
                stage="handoff",
                code="handoff_not_triggered",
                message="风险识别后没有触发转人工",
                severity="critical",
            )
        )
    if not risk_match:
        failures.append(
            ReplyAgentFailureReason(
                stage="handoff",
                code="risk_code_mismatch",
                message=(
                    f"期望风险 {case.handoff.expected_risk_codes}，实际 {sorted(actual_codes)}"
                ),
                severity="warning",
            )
        )
    return ReplyAgentEvaluationCaseResult(
        case_id=case.case_id,
        title=case.title,
        source_case_ids=case.source_case_ids,
        execution_kind="handoff",
        tags=case.tags,
        status="passed" if passed else "failed",
        user_query=case.handoff.user_query,
        expected=_expected(case),
        actual=ReplyAgentActual(
            retrieval_decision="skip",
            response_mode="handoff" if handoff else None,
            response=None,
            tool_names=[],
            handoff_triggered=handoff,
        ),
        metrics=[
            _metric(
                key="retrieval_decision",
                label="知识检索决策",
                group="M0",
                kind="deterministic",
                score=1.0,
                threshold=1.0,
                passed=True,
                reason="高风险消息在回复 Agent 前转人工，不进入知识检索",
            ),
            _metric(
                key="handoff_required",
                label="转人工触发",
                group="MB",
                kind="deterministic",
                score=1.0 if handoff else 0.0,
                threshold=1.0,
                passed=handoff,
                reason="已触发转人工" if handoff else "未触发转人工",
            ),
            _metric(
                key="risk_classification",
                label="风险类别",
                group="MB",
                kind="llm",
                score=1.0 if risk_match else 0.0,
                threshold=1.0,
                passed=risk_match,
                reason=f"识别风险：{sorted(actual_codes)}",
            ),
            _metric(
                key="reply_agent_bypassed",
                label="常规回复链路被拦截",
                group="MB",
                kind="deterministic",
                score=1.0 if handoff else 0.0,
                threshold=1.0,
                passed=handoff,
                reason="评测器在转人工后不会调用回复 Agent",
            ),
        ],
        failure_reasons=failures,
        evidence={"message_insight": analysis},
        usage=usage,
    )


def _mock_handoff_analysis(case: ReplyAgentEvalCase) -> dict[str, Any]:
    assert case.handoff is not None
    return {
        "requires_human_intervention": True,
        "requires_immediate_safety_reply": True,
        "risk_flags": [
            {
                "code": case.handoff.expected_risk_codes[0],
                "level": "high"
                if "pregnancy" in case.handoff.expected_risk_codes
                else "medium",
                "evidence": case.handoff.user_query,
            }
        ],
        "intervention_reasons": [],
        "knowledge_query": None,
    }


async def _mock_cases_for_suite(
    suite: ReplyAgentEvalSuite,
) -> list[ReplyAgentEvaluationCaseResult]:
    settings = get_settings().model_copy(update={"web_search_enabled": False})
    rag_cases = [case.rag_case for case in suite.cases if case.rag_case]
    rag_results = {item.case_id: item for item in _mock_cases(rag_cases)}

    agent_cases = [case.agent_case for case in suite.cases if case.agent_case]
    agent_ledger = AgentEvaluationCostLedger(settings)
    judge = AgentQualityJudge(settings, mock=True, cost_ledger=agent_ledger)
    try:
        agent_results = await mock_agent_results(
            agent_cases,
            1,
            judge,
            agent_ledger,
        )
    finally:
        await judge.close()
    agent_by_id = {item.case_id: item for item in agent_results}

    results: list[ReplyAgentEvaluationCaseResult] = []
    for case in suite.cases:
        if case.rag_case:
            results.append(adapt_rag_result(case, rag_results[case.rag_case.case_id]))
        elif case.agent_case:
            results.append(
                adapt_agent_result(case, agent_by_id[case.agent_case.case_id])
            )
        else:
            results.append(
                adapt_handoff_result(
                    case,
                    analysis=_mock_handoff_analysis(case),
                    usage={
                        "tracking_available": True,
                        "total": ReplyAgentUsageTotals().model_dump(mode="json"),
                    },
                )
            )
    return results


async def _run_case_batch(
    cases: list[TCase],
    *,
    run_case: Callable[[TCase], Awaitable[TResult]],
    results: list[TResult],
    on_progress,
    max_concurrency: int = REAL_CASE_CONCURRENCY,
) -> None:
    """Run independent cases with a fixed worker pool and one progress writer.

    A worker only takes a new case after the previous result has been persisted.
    If the cost gate or a case raises, no new cases start; already-running cases
    are allowed to finish so their calls remain attributable in the report.
    """
    if not cases:
        return
    worker_count = min(max(1, max_concurrency), len(cases))
    next_index = 0
    stopped = False
    first_error: Exception | None = None
    state_lock = asyncio.Lock()
    progress_lock = asyncio.Lock()

    async def worker() -> None:
        nonlocal next_index, stopped, first_error
        while True:
            async with state_lock:
                if stopped or next_index >= len(cases):
                    return
                case = cases[next_index]
                next_index += 1
            try:
                result = await run_case(case)
            except Exception as exc:  # stop scheduling, preserve in-flight evidence
                async with state_lock:
                    stopped = True
                    if first_error is None:
                        first_error = exc
                return

            async with progress_lock:
                results.append(result)
                try:
                    await on_progress(results)
                except Exception as exc:
                    async with state_lock:
                        stopped = True
                        if first_error is None:
                            first_error = exc
                    return

    await asyncio.gather(*(worker() for _ in range(worker_count)))
    if first_error is not None:
        raise first_error


async def _assert_faq_provenance_fresh(
    suite: ReplyAgentEvalSuite, *, allow_drift: bool
) -> None:
    """题库快照落后于库中 FAQ 时，在跑真实评测前中止。

    否则判官会把"模型答对了新口径"记成失败，而失败原因看起来像模型问题。
    """
    async with SessionLocal() as session:
        drifts = await check_faq_provenance(session, suite)
    if not drifts:
        return
    report = format_drift_report(drifts)
    if allow_drift:
        print(f"[warn] {report}", flush=True)
        return
    raise RuntimeError(report)


async def _real_cases_for_suite(
    suite: ReplyAgentEvalSuite,
    *,
    run_dir: Path,
    results: list[ReplyAgentEvaluationCaseResult],
    on_progress,
    allow_faq_drift: bool = False,
) -> list[ReplyAgentEvaluationCaseResult]:
    settings = get_settings().model_copy(update={"web_search_enabled": False})
    if not settings.ai_api_key or not settings.embedding_api_key:
        raise RuntimeError("真实统一评测要求 AI_API_KEY 和 EMBEDDING_API_KEY")
    install_read_only_guards(engine)
    await _assert_faq_provenance_fresh(suite, allow_drift=allow_faq_drift)
    rag_ledger = RagEvaluationCostLedger(
        settings,
        call_log_path=run_dir / "calls.jsonl",
    )
    restore_rag = install_rag_usage_collectors(rag_ledger)
    try:
        async def run_rag_or_handoff_case(
            case: ReplyAgentEvalCase,
        ) -> ReplyAgentEvaluationCaseResult:
            if case.rag_case:
                source_id = case.rag_case.case_id
                rag_ledger.begin_case(source_id)
                scorer: DeepEvalScorer | None = None
                try:
                    scorer = DeepEvalScorer(settings, cost_ledger=rag_ledger)
                    source_result = await _evaluate_real_case(
                        case.rag_case,
                        scorer=scorer,
                        settings=settings,
                        answer_relevancy_enforced=False,
                        answer_relevancy_threshold=None,
                        faithfulness_enforced=True,
                        faithfulness_threshold=GATE_FAITHFULNESS,
                        cost_ledger=rag_ledger,
                    )
                    source_result.usage = rag_ledger.case_usage(source_id)
                    return adapt_rag_result(case, source_result)
                finally:
                    try:
                        if scorer is not None:
                            await scorer.close()
                    finally:
                        rag_ledger.end_case(source_id)

            assert case.handoff is not None
            source_id = case.source_case_ids[0]
            rag_ledger.begin_case(source_id)
            try:
                analysis = await analyze_message_insight(
                    source="chat_message",
                    content=case.handoff.user_query,
                    context={
                        "current_message": case.handoff.user_query,
                        "recent_user_messages": case.handoff.history,
                    },
                )
                usage = rag_ledger.case_usage(source_id).model_dump(mode="json")
                return adapt_handoff_result(case, analysis=analysis, usage=usage)
            finally:
                rag_ledger.end_case(source_id)

        await _run_case_batch(
            [case for case in suite.cases if case.rag_case or case.handoff],
            run_case=run_rag_or_handoff_case,
            results=results,
            on_progress=on_progress,
        )
    finally:
        restore_rag()

    agent_ledger = AgentEvaluationCostLedger(
        settings,
        call_log_path=run_dir / "calls.jsonl",
        reset_call_log=False,
    )
    restore_agent = install_agent_usage_collector(agent_ledger)
    try:
        async def run_agent_case(
            case: ReplyAgentEvalCase,
        ) -> ReplyAgentEvaluationCaseResult:
            assert case.agent_case is not None
            judge = AgentQualityJudge(
                settings,
                mock=False,
                cost_ledger=agent_ledger,
            )
            try:
                source_result = await real_agent_result(
                    case=case.agent_case,
                    round_index=1,
                    settings=settings,
                    judge=judge,
                    cost_ledger=agent_ledger,
                    knowledge_query="",
                )
                return adapt_agent_result(case, source_result)
            finally:
                await judge.close()

        await _run_case_batch(
            [case for case in suite.cases if case.agent_case],
            run_case=run_agent_case,
            results=results,
            on_progress=on_progress,
        )
    finally:
        restore_agent()
    return sorted(results, key=lambda item: item.case_id)


def write_report(run_dir: Path, report: ReplyAgentEvaluationRunDetail) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(run_dir / "report.json", report.model_dump(mode="json"))


async def async_main(args: argparse.Namespace) -> int:
    if getattr(args, "max_cost_yuan", None) is not None and args.max_cost_yuan <= 0:
        raise ValueError("max_cost_yuan must be greater than zero")
    reports_root = args.reports_root.resolve()
    run_id = args.run_id or datetime.now(timezone.utc).strftime(
        "reply-agent-eval-%Y%m%dT%H%M%SZ"
    )
    run_dir = reports_root / run_id
    if run_dir.exists():
        raise RuntimeError(f"run directory already exists: {run_dir}")
    suite = load_reply_agent_eval_suite(args.dataset)
    case_ids = [
        item.strip().upper()
        for item in (getattr(args, "case_ids", None) or "").split(",")
        if item.strip()
    ]
    if case_ids:
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("case_ids contains duplicates")
        available = {case.case_id for case in suite.cases}
        unknown = sorted(set(case_ids) - available)
        if unknown:
            raise ValueError(f"unknown case_ids: {', '.join(unknown)}")
        selected = set(case_ids)
        suite = suite.model_copy(
            update={"cases": [case for case in suite.cases if case.case_id in selected]}
        )
    base = _base_summary(run_id=run_id, suite=suite, mock=args.mock)
    atomic_write_json(run_dir / "status.json", base.model_dump(mode="json"))
    results: list[ReplyAgentEvaluationCaseResult] = []

    async def progress(current: list[ReplyAgentEvaluationCaseResult]) -> None:
        running = aggregate_summary(base=base, cases=current, status="running")
        atomic_write_json(run_dir / "status.json", running.model_dump(mode="json"))
        max_cost = getattr(args, "max_cost_yuan", None)
        if (
            not args.mock
            and max_cost is not None
            and running.usage.known_estimated_cost_yuan >= max_cost
        ):
            raise RuntimeError(
                "统一评测已达到费用上限："
                f"¥{running.usage.known_estimated_cost_yuan:.4f} / ¥{max_cost:.2f}"
            )

    try:
        results = (
            await _mock_cases_for_suite(suite)
            if args.mock
            else await _real_cases_for_suite(
                suite,
                run_dir=run_dir,
                results=results,
                on_progress=progress,
                allow_faq_drift=getattr(args, "allow_faq_drift", False),
            )
        )
        summary = aggregate_summary(base=base, cases=results)
        report = ReplyAgentEvaluationRunDetail(summary=summary, cases=results)
        write_report(run_dir, report)
        atomic_write_json(run_dir / "status.json", summary.model_dump(mode="json"))
        return 0
    except Exception as exc:
        failed = aggregate_summary(
            base=base,
            cases=results,
            status="failed",
            error=f"{type(exc).__name__}: {exc}"[:2000],
        )
        atomic_write_json(run_dir / "status.json", failed.model_dump(mode="json"))
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the unified reply Agent evaluation"
    )
    parser.add_argument("--mock", action="store_true", help="No DB or model calls")
    parser.add_argument("--run-id")
    parser.add_argument(
        "--case-ids",
        default="",
        help="Comma-separated RA case ids for a targeted run; empty runs all 80",
    )
    parser.add_argument(
        "--max-cost-yuan",
        type=float,
        default=float(os.getenv("REPLY_AGENT_EVAL_MAX_COST_YUAN", "10")),
        help="Stop the real run after known cost reaches this amount",
    )
    parser.add_argument(
        "--allow-faq-drift",
        action="store_true",
        help="题库引用的 FAQ 版本落后于库中时仅告警，不中止（默认中止）",
    )
    parser.add_argument(
        "--reports-root",
        type=Path,
        default=Path(
            os.getenv(
                "REPLY_AGENT_EVALUATION_REPORTS_PATH",
                "/app/diagnostics/reply-agent-evaluations",
            )
        ),
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=(
            Path(os.environ["REPLY_AGENT_EVALUATION_DATASET_PATH"])
            if os.getenv("REPLY_AGENT_EVALUATION_DATASET_PATH", "").strip()
            else default_reply_agent_eval_path()
        ),
    )
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(async_main(parse_args())))
