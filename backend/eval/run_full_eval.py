from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import get_settings
from app.db.session import SessionLocal, engine
from app.evaluation.rag_cases import RagEvalCase, load_rag_eval_suite
from app.schemas.rag_evaluation import (
    AnswerRelevancyCalibrationReport,
    AnswerRelevancyPolicy,
    FaithfulnessCalibrationReport,
    FaithfulnessPolicy,
    RagAnswerRelevancyJudgement,
    RagBinaryJudgement,
    RagCaseActual,
    RagCaseExpected,
    RagCaseOrigin,
    RagCaseScores,
    RagContextPrecisionJudgement,
    RagEvaluationCaseResult,
    RagEvaluationCounts,
    RagEvaluationMetrics,
    RagEvaluationRunDetail,
    RagEvaluationSummary,
    RagEvaluationUsageSummary,
    RagFailureReason,
    RagHistoryMessage,
    RagKnowledgeHit,
    RagMetricEvidence,
    RagMetricValue,
    RagRewriteExpected,
)
from app.services.agent.graph import RanrenAgentGraph
from app.services.agent.state import AgentRunContext
from app.services.knowledge_index import retrieve_indexed_knowledge_result

if __package__:
    from .intent_eval import (
        IntentResult,
        evaluate_intent,
        format_history,
    )
    from .rag_costs import (
        RagEvaluationCostLedger,
        install_rag_usage_collectors,
        usage_from_calls,
    )
    from .deepeval_eval import DeepEvalScorer, DEEPEVAL_VERSION, GATE_FAITHFULNESS
    from .read_only import install_read_only_guards
    from .reporting import aggregate_summary, atomic_write_json, utc_now, write_report
else:
    from intent_eval import (
        IntentResult,
        evaluate_intent,
        format_history,
    )
    from rag_costs import (
        RagEvaluationCostLedger,
        install_rag_usage_collectors,
        usage_from_calls,
    )
    from deepeval_eval import DeepEvalScorer, DEEPEVAL_VERSION, GATE_FAITHFULNESS
    from read_only import install_read_only_guards
    from reporting import aggregate_summary, atomic_write_json, utc_now, write_report


THRESHOLDS = {
    "context_recall": 0.80,
    "context_precision": 0.70,
    "faithfulness": 0.85,
    "answer_relevancy": 0.80,
}
EVALUATION_FRAMEWORK = "deepeval"
EVALUATION_FRAMEWORK_VERSION = DEEPEVAL_VERSION


def _empty_metrics() -> RagEvaluationMetrics:
    return RagEvaluationMetrics(
        retrieval_decision_error_rate=RagMetricValue(),
        rewrite_harm_rate=RagMetricValue(),
        context_recall=RagMetricValue(),
        context_precision=RagMetricValue(),
        faithfulness=RagMetricValue(),
        answer_relevancy=RagMetricValue(),
    )


def _gate_thresholds() -> dict[str, float]:
    """Fixed product gates for the DeepEval era. Answer relevancy and answer
    correctness/completeness are observational until calibrated."""
    return {
        "context_recall": 0.80,
        "context_precision": 0.70,
        "faithfulness": 0.8333,
    }


def _base_summary(
    *,
    run_id: str,
    dataset_id: str,
    dataset_version: int,
    total: int,
    mock: bool,
    answer_relevancy_policy: AnswerRelevancyPolicy,
    faithfulness_policy: FaithfulnessPolicy,
) -> RagEvaluationSummary:
    settings = get_settings()
    now = utc_now()
    return RagEvaluationSummary(
        run_id=run_id,
        run_mode="mock" if mock else "real",
        status="running",
        started_at=now,
        updated_at=now,
        git_sha=(os.getenv("RAG_EVAL_GIT_SHA") or "unknown")[:64],
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        model="mock-model" if mock else settings.ai_model,
        embedding_model="mock-embedding" if mock else settings.embedding_model,
        evaluation_framework="deepeval",
        framework_version=EVALUATION_FRAMEWORK_VERSION,
        counts=RagEvaluationCounts(
            total=total,
            completed=0,
            passed=0,
            failed=0,
            skipped=0,
            errors=0,
        ),
        metrics=_empty_metrics(),
        answer_relevancy_policy=answer_relevancy_policy,
        faithfulness_policy=faithfulness_policy,
        usage=RagEvaluationUsageSummary(tracking_available=True),
    )


def _load_answer_relevancy_policy(
    *,
    mock: bool,
    reports_root: Path,
    settings,
) -> AnswerRelevancyPolicy:
    """DeepEval native answer relevancy is diagnostic-only by decision."""
    return AnswerRelevancyPolicy(
        status="diagnostic_only",
        enforced=False,
        reason="DeepEval 原生答案相关性仅作诊断展示，不参与通过判定",
    )


def _load_faithfulness_policy(
    *,
    mock: bool,
    reports_root: Path,
    settings,
) -> FaithfulnessPolicy:
    if mock:
        return FaithfulnessPolicy(
            status="mock",
            enforced=True,
            threshold=GATE_FAITHFULNESS,
            reason="模拟批次使用固定分数，只验证评测链路和页面结构",
        )
    return FaithfulnessPolicy(
        status="calibrated",
        enforced=True,
        threshold=GATE_FAITHFULNESS,
        reason="忠实度门禁采用中文正反校准选出的阈值 0.8333",
    )


def _knowledge_hit(hit) -> RagKnowledgeHit:
    return RagKnowledgeHit(
        rank=hit.rank,
        chunk_uid=hit.chunk_uid,
        title=hit.title,
        source_type=hit.source_type,
        content=hit.content[:12000],
    )


def _reference(case: RagEvalCase) -> str:
    required = set(case.answer.required_fact_ids or case.retrieval.required_fact_ids)
    statements = [
        fact.statement for fact in case.authoritative_facts if fact.fact_id in required
    ]
    return "\n".join(statements)


def _answer_fact_checks(case: RagEvalCase, response: str | None) -> list[RagFailureReason]:
    """Deterministic answer-correctness checks for every case, including skip
    cases whose replies never go through RAGAS scoring."""
    reasons: list[RagFailureReason] = []
    if not response:
        reasons.append(
            RagFailureReason(
                stage="answer",
                code="empty_response",
                message=(
                    f"本题需要回复（response_mode={case.answer.response_mode}），"
                    "但 Agent 没有产出最终回复"
                ),
            )
        )
        return reasons
    fact_by_id = {fact.fact_id: fact for fact in case.authoritative_facts}
    for fact_id in case.answer.required_fact_ids:
        statement = fact_by_id[fact_id].statement
        if statement not in response:
            reasons.append(
                RagFailureReason(
                    stage="answer",
                    code="missing_required_fact",
                    message=f"回复缺少必须事实[{fact_id}]：{statement[:80]}",
                )
            )
    for fact_id in case.answer.forbidden_fact_ids:
        statement = fact_by_id[fact_id].statement
        if statement in response:
            reasons.append(
                RagFailureReason(
                    stage="answer",
                    code="forbidden_fact_present",
                    message=f"回复出现禁止事实[{fact_id}]：{statement[:80]}",
                )
            )
    return reasons


async def _agent_reply(case: RagEvalCase, intent: IntentResult, settings) -> str | None:
    if case.answer.response_mode == "use_runtime_data":
        return None
    safe_settings = settings.model_copy(
        update={
            "web_search_enabled": False,
            "agent_max_tool_calls": min(settings.agent_max_tool_calls, 4),
        }
    )
    result = await RanrenAgentGraph(
        context=AgentRunContext(
            service_case_id=0,
            trigger_id=None,
            agent_run_id=None,
            run_kind="interactive",
        ),
        settings=safe_settings,
        include_reminder_tools=False,
        checkpointer=None,
    ).run(
        user_query=case.user_query,
        knowledge_query=intent.agent_knowledge_query,
        recent_messages=format_history(case, limit=20),
        query_speaker="用户",
    )
    return result.reply


def _base_case(case: RagEvalCase, *, status: str) -> RagEvaluationCaseResult:
    facts = {fact.fact_id: fact.statement for fact in case.authoritative_facts}
    return RagEvaluationCaseResult(
        case_id=case.case_id,
        title=case.title,
        origin=RagCaseOrigin(**case.origin.model_dump()),
        tags=case.tags,
        status=status,
        history=[RagHistoryMessage(**item.model_dump()) for item in case.history],
        user_query=case.user_query,
        expected=RagCaseExpected(
            retrieval_decision=case.rewrite.decision,
            response_mode=case.answer.response_mode,
            rewrite=RagRewriteExpected(
                must_include=case.rewrite.must_include,
                must_preserve_meanings=case.rewrite.must_preserve_meanings,
            ),
            required_facts=[
                facts[fact_id] for fact_id in case.answer.required_fact_ids
            ],
        ),
        actual=RagCaseActual(
            retrieval_decision=None,
            decision_source="unavailable",
        ),
        scores=RagCaseScores(),
        usage=usage_from_calls([]),
    )


async def _evaluate_real_case(
    case: RagEvalCase,
    *,
    scorer: DeepEvalScorer,
    settings,
    answer_relevancy_enforced: bool,
    answer_relevancy_threshold: float | None,
    faithfulness_enforced: bool,
    faithfulness_threshold: float | None,
    cost_ledger: RagEvaluationCostLedger,
) -> RagEvaluationCaseResult:
    started = time.monotonic()
    result = _base_case(case, status="passed")
    reasons: list[RagFailureReason] = []
    try:
        with cost_ledger.stage("intent_rewrite"):
            intent = await evaluate_intent(case)
        result.actual.retrieval_decision = intent.decision
        result.actual.decision_source = intent.decision_source
        result.actual.knowledge_query = intent.knowledge_query
        if intent.decision != case.rewrite.decision:
            reasons.append(
                RagFailureReason(
                    stage="decision",
                    code="retrieval_decision_mismatch",
                    message=(f"期望{case.rewrite.decision}，实际{intent.decision}"),
                )
            )

        raw_hits = []
        rewritten_hits = []
        if case.rewrite.decision == "retrieve":
            with cost_ledger.stage("knowledge_retrieval"):
                async with SessionLocal() as session:
                    raw = await retrieve_indexed_knowledge_result(
                        session,
                        case.user_query,
                        settings=settings,
                        exclude_source_types={"case"},
                    )
                    actual = (
                        await retrieve_indexed_knowledge_result(
                            session,
                            intent.knowledge_query or case.user_query,
                            settings=settings,
                            exclude_source_types={"case"},
                        )
                        if intent.decision == "retrieve"
                        else None
                    )
            raw_hits = raw.hits
            rewritten_hits = actual.hits if actual is not None else []
            result.actual.raw_query_hits = [_knowledge_hit(hit) for hit in raw_hits]
            result.actual.rewritten_query_hits = [
                _knowledge_hit(hit) for hit in rewritten_hits
            ]

        with cost_ledger.stage("answer_generation"):
            response = await _agent_reply(case, intent, settings)
        result.actual.response = response
        reference = _reference(case)

        reasons.extend(_answer_fact_checks(case, response))

        if case.rewrite.decision == "retrieve" and response:
            fact_by_id = {fact.fact_id: fact for fact in case.authoritative_facts}
            scores = await scorer.score(
                user_input=case.user_query,
                response=response,
                reference=reference,
                retrieved_contexts=[hit.content for hit in rewritten_hits],
                raw_contexts=[hit.content for hit in raw_hits],
                must_have_statements=[
                    fact_by_id[f].statement
                    for f in case.answer.required_fact_ids
                    if f in fact_by_id
                ],
                forbidden_statements=[
                    fact_by_id[f].statement
                    for f in case.answer.forbidden_fact_ids
                    if f in fact_by_id
                ],
                response_mode=case.answer.response_mode,
            )
            result.scores = RagCaseScores(
                context_recall=scores.context_recall,
                context_precision=scores.context_precision,
                faithfulness=scores.faithfulness,
                answer_relevancy=scores.answer_relevancy,
                answer_correctness=scores.answer_correctness,
                answer_correctness_reason=scores.answer_correctness_reason,
            )
            result.metric_evidence = RagMetricEvidence(
                context_recall=[
                    RagBinaryJudgement(
                        statement=item.statement,
                        reason=item.reason,
                        verdict=item.verdict,
                    )
                    for item in scores.context_recall_details
                ],
                raw_context_recall=[
                    RagBinaryJudgement(
                        statement=item.statement,
                        reason=item.reason,
                        verdict=item.verdict,
                    )
                    for item in scores.raw_context_recall_details
                ],
                context_precision=[
                    RagContextPrecisionJudgement(
                        rank=item.rank,
                        reason=item.reason,
                        verdict=item.verdict,
                    )
                    for item in scores.context_precision_details
                ],
                faithfulness=[
                    RagBinaryJudgement(
                        statement=item.statement,
                        reason=item.reason,
                        verdict=item.verdict,
                    )
                    for item in scores.faithfulness_details
                ],
                answer_relevancy=[
                    RagAnswerRelevancyJudgement(
                        generated_question=item.generated_question,
                        noncommittal=item.noncommittal,
                        similarity=item.similarity,
                    )
                    for item in scores.answer_relevancy_details
                ],
                answer_relevancy_all_noncommittal=(
                    scores.answer_relevancy_all_noncommittal
                ),
            )
            if scores.context_recall + 0.05 < scores.raw_context_recall:
                reasons.append(
                    RagFailureReason(
                        stage="rewrite",
                        code="rewrite_harmed_retrieval",
                        message=(
                            "改写后的事实召回率低于原问题："
                            f"{scores.context_recall:.2f} < {scores.raw_context_recall:.2f}"
                        ),
                    )
                )
            for name, threshold in _gate_thresholds().items():
                value = getattr(scores, name)
                if value < threshold:
                    reasons.append(
                        RagFailureReason(
                            stage="deepeval",
                            code=f"{name}_below_threshold",
                            message=f"{name}={value:.2f}，低于基线{threshold:.2f}",
                        )
                    )
        elif case.rewrite.decision == "retrieve":
            reasons.append(
                RagFailureReason(
                    stage="retrieval",
                    code="missing_ragas_inputs",
                    message="缺少召回内容或最终回复，无法计算四项 RAGAS 指标",
                )
            )
        result.failure_reasons = reasons
        result.status = "failed" if reasons else "passed"
    except Exception as exc:
        result.status = "error"
        result.failure_reasons = [
            RagFailureReason(
                stage="runtime",
                code="case_execution_error",
                message=f"{type(exc).__name__}: {exc}"[:2000],
            )
        ]
    result.elapsed_ms = int((time.monotonic() - started) * 1000)
    return result


def _mock_knowledge_query(case: RagEvalCase) -> str | None:
    if case.rewrite.decision == "skip":
        return None
    return " ".join(case.rewrite.must_include) or case.user_query


def _mock_knowledge_hits(case: RagEvalCase) -> list[RagKnowledgeHit]:
    facts = {fact.fact_id: fact for fact in case.authoritative_facts}
    return [
        RagKnowledgeHit(
            rank=rank,
            chunk_uid=f"mock-{case.case_id}-{fact_id}",
            title=f"模拟召回：{facts[fact_id].provenance}"[:300],
            source_type="mock",
            content=facts[fact_id].statement,
        )
        for rank, fact_id in enumerate(case.retrieval.required_fact_ids, start=1)
    ]


def _mock_cases(cases: list[RagEvalCase]) -> list[RagEvaluationCaseResult]:
    output: list[RagEvaluationCaseResult] = []
    for index, case in enumerate(cases):
        result = _base_case(case, status="passed")
        result.actual.retrieval_decision = case.rewrite.decision
        result.actual.decision_source = (
            "rewritten" if case.rewrite.decision == "retrieve" else "explicit_skip"
        )
        result.actual.knowledge_query = _mock_knowledge_query(case)
        result.actual.response = (
            _reference(case) or "收到，我会按你刚才说的情况接着处理。"
        )
        if case.rewrite.decision == "retrieve":
            mock_hits = _mock_knowledge_hits(case)
            result.actual.raw_query_hits = list(mock_hits)
            result.actual.rewritten_query_hits = list(mock_hits)
            value = 0.88 + (index % 5) * 0.015
            result.scores = RagCaseScores(
                context_recall=min(value, 0.98),
                context_precision=min(value - 0.04, 0.96),
                faithfulness=min(value + 0.03, 0.99),
                answer_relevancy=min(value - 0.01, 0.97),
            )
            facts = [hit.content for hit in mock_hits]
            result.metric_evidence = RagMetricEvidence(
                context_recall=[
                    RagBinaryJudgement(
                        statement=fact,
                        reason="模拟召回内容覆盖该权威事实",
                        verdict=1,
                    )
                    for fact in facts
                ],
                raw_context_recall=[
                    RagBinaryJudgement(
                        statement=fact,
                        reason="模拟原问题召回内容覆盖该权威事实",
                        verdict=1,
                    )
                    for fact in facts
                ],
                context_precision=[
                    RagContextPrecisionJudgement(
                        rank=hit.rank,
                        reason="模拟召回内容可用于回答当前问题",
                        verdict=1,
                    )
                    for hit in mock_hits
                ],
                faithfulness=[
                    RagBinaryJudgement(
                        statement=fact,
                        reason="模拟回复陈述可由召回内容推出",
                        verdict=1,
                    )
                    for fact in facts
                ],
                answer_relevancy=[
                    RagAnswerRelevancyJudgement(
                        generated_question=case.user_query,
                        noncommittal=0,
                        similarity=result.scores.answer_relevancy or 0.0,
                    )
                    for _ in range(3)
                ],
                answer_relevancy_all_noncommittal=False,
            )
        output.append(result)

    if output:
        failure = next(
            (item for item in output if item.case_id == "rag-chat-cap-ambiguity"),
            output[0],
        )
        failure.status = "failed"
        failure.actual.response = "一大刻度不够一瓶盖。"
        failure.scores.faithfulness = 0.42
        failure.metric_evidence.faithfulness = [
            RagBinaryJudgement(
                statement="一大刻度不够一瓶盖。",
                reason="模拟回复反转了瓶盖与一大刻度的用量关系",
                verdict=0,
            )
        ]
        failure.failure_reasons = [
            RagFailureReason(
                stage="deepeval",
                code="faithfulness_below_threshold",
                message="faithfulness=0.42，低于基线0.85",
            ),
        ]
    return output


async def async_main(args: argparse.Namespace) -> int:
    reports_root = args.reports_root.resolve()
    run_id = args.run_id or datetime.now(timezone.utc).strftime("eval-%Y%m%dT%H%M%SZ")
    run_dir = reports_root / run_id
    if run_dir.exists():
        raise RuntimeError(f"run directory already exists: {run_dir}")

    suite = load_rag_eval_suite(args.dataset)
    dataset_id = suite.suite_id
    dataset_version = suite.version
    cases = suite.cases
    settings = get_settings()
    answer_relevancy_policy = _load_answer_relevancy_policy(
        mock=args.mock,
        reports_root=reports_root,
        settings=settings,
    )
    faithfulness_policy = _load_faithfulness_policy(
        mock=args.mock,
        reports_root=reports_root,
        settings=settings,
    )

    base = _base_summary(
        run_id=run_id,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        total=len(cases),
        mock=args.mock,
        answer_relevancy_policy=answer_relevancy_policy,
        faithfulness_policy=faithfulness_policy,
    )
    atomic_write_json(run_dir / "status.json", base.model_dump(mode="json"))
    results: list[RagEvaluationCaseResult] = []
    restore_usage_collectors = None
    try:
        if args.mock:
            results = _mock_cases(cases)
        else:
            if not settings.ai_api_key or not settings.embedding_api_key:
                raise RuntimeError("真实评测要求 AI_API_KEY 和 EMBEDDING_API_KEY")
            install_read_only_guards(engine)
            cost_ledger = RagEvaluationCostLedger(settings)
            restore_usage_collectors = install_rag_usage_collectors(cost_ledger)
            scorer = DeepEvalScorer(settings, cost_ledger=cost_ledger)
            for case in cases:
                cost_ledger.begin_case(case.case_id)
                result = await _evaluate_real_case(
                    case,
                    scorer=scorer,
                    settings=settings,
                    answer_relevancy_enforced=(answer_relevancy_policy.enforced),
                    answer_relevancy_threshold=answer_relevancy_policy.threshold,
                    faithfulness_enforced=faithfulness_policy.enforced,
                    faithfulness_threshold=faithfulness_policy.threshold,
                    cost_ledger=cost_ledger,
                )
                result.usage = cost_ledger.case_usage(case.case_id)
                cost_ledger.end_case(case.case_id)
                results.append(result)
                running = aggregate_summary(base=base, cases=results, status="running")
                running.finished_at = None
                running.counts.total = len(cases)
                atomic_write_json(
                    run_dir / "status.json",
                    running.model_dump(mode="json"),
                )
        summary = aggregate_summary(base=base, cases=results)
        report = RagEvaluationRunDetail(summary=summary, cases=results)
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
        failed.counts.total = len(cases)
        atomic_write_json(run_dir / "status.json", failed.model_dump(mode="json"))
        raise
    finally:
        if restore_usage_collectors is not None:
            restore_usage_collectors()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the isolated RAG evaluation")
    parser.add_argument("--mock", action="store_true", help="No DB or model calls")
    parser.add_argument("--run-id")
    parser.add_argument(
        "--reports-root",
        type=Path,
        default=Path(
            os.getenv(
                "RAG_EVALUATION_REPORTS_PATH",
                "/app/diagnostics/rag-evaluations",
            )
        ),
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path(
            os.getenv(
                "RAG_EVALUATION_DATASET_PATH",
                "/app/tests/fixtures/rag_eval_cases.v2.json",
            )
        ),
    )
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(async_main(parse_args())))
