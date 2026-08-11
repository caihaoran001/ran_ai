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
from app.evaluation.answer_relevancy_calibration import (
    CALIBRATION_THRESHOLD,
    resolve_answer_relevancy_policy,
)
from app.evaluation.faithfulness_calibration import (
    CALIBRATION_THRESHOLD as FAITHFULNESS_CALIBRATION_THRESHOLD,
)
from app.evaluation.faithfulness_calibration import resolve_faithfulness_policy
from app.schemas.rag_evaluation import (
    AnswerRelevancyCalibrationReport,
    AnswerRelevancyPolicy,
    FaithfulnessCalibrationReport,
    FaithfulnessPolicy,
    RagCaseActual,
    RagCaseExpected,
    RagCaseOrigin,
    RagCaseScores,
    RagEvaluationCaseResult,
    RagEvaluationCounts,
    RagEvaluationMetrics,
    RagEvaluationRunDetail,
    RagEvaluationSummary,
    RagFailureReason,
    RagHistoryMessage,
    RagKnowledgeHit,
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
        install_in_memory_usage_collector,
    )
    from .ragas_eval import RagasScorer
    from .read_only import install_read_only_guards
    from .reporting import aggregate_summary, atomic_write_json, utc_now, write_report
else:
    from intent_eval import (
        IntentResult,
        evaluate_intent,
        format_history,
        install_in_memory_usage_collector,
    )
    from ragas_eval import RagasScorer
    from read_only import install_read_only_guards
    from reporting import aggregate_summary, atomic_write_json, utc_now, write_report


THRESHOLDS = {
    "context_recall": 0.80,
    "context_precision": 0.70,
    "faithfulness": 0.85,
    "answer_relevancy": 0.80,
}
RAGAS_VERSION = "0.4.3"
CRITICAL_RELATION_TAGS = frozenset({"number", "unit", "comparison", "negation"})


def _empty_metrics() -> RagEvaluationMetrics:
    return RagEvaluationMetrics(
        retrieval_decision_error_rate=RagMetricValue(),
        rewrite_harm_rate=RagMetricValue(),
        context_recall=RagMetricValue(),
        context_precision=RagMetricValue(),
        faithfulness=RagMetricValue(),
        answer_relevancy=RagMetricValue(),
    )


def _active_ragas_thresholds(
    *,
    answer_relevancy_enforced: bool,
    faithfulness_enforced: bool,
) -> dict[str, float]:
    disabled = set()
    if not answer_relevancy_enforced:
        disabled.add("answer_relevancy")
    if not faithfulness_enforced:
        disabled.add("faithfulness")
    return {
        name: threshold
        for name, threshold in THRESHOLDS.items()
        if name not in disabled
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
        ragas_version=RAGAS_VERSION,
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
    )


def _load_answer_relevancy_policy(
    *,
    mock: bool,
    reports_root: Path,
    settings,
) -> AnswerRelevancyPolicy:
    if mock:
        return AnswerRelevancyPolicy(
            status="mock",
            enforced=True,
            reason="模拟批次使用固定分数，只验证评测链路和页面结构",
        )

    path = Path(
        os.getenv(
            "RAG_ANSWER_RELEVANCY_CALIBRATION_LATEST_PATH",
            str(reports_root / "answer-relevancy-calibration.json"),
        )
    ).resolve()
    try:
        report = AnswerRelevancyCalibrationReport.model_validate(
            json.loads(path.read_text(encoding="utf-8"))
        )
    except FileNotFoundError:
        report = None
    except Exception:
        return AnswerRelevancyPolicy(
            status="not_calibrated",
            enforced=False,
            reason="中文校准报告无法读取，回复相关性仅展示、不参与通过判定",
        )
    return resolve_answer_relevancy_policy(
        report,
        model=settings.ai_model,
        embedding_model=settings.embedding_model,
        ragas_version=RAGAS_VERSION,
        threshold=CALIBRATION_THRESHOLD,
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
            reason="模拟批次使用固定分数，只验证评测链路和页面结构",
        )

    path = Path(
        os.getenv(
            "RAG_FAITHFULNESS_CALIBRATION_LATEST_PATH",
            str(reports_root / "faithfulness-calibration.json"),
        )
    ).resolve()
    try:
        report = FaithfulnessCalibrationReport.model_validate(
            json.loads(path.read_text(encoding="utf-8"))
        )
    except FileNotFoundError:
        report = None
    except Exception:
        return FaithfulnessPolicy(
            status="not_calibrated",
            enforced=False,
            reason="中文校准报告无法读取，回复忠实度仅展示、不参与通过判定",
        )
    return resolve_faithfulness_policy(
        report,
        model=settings.ai_model,
        ragas_version=RAGAS_VERSION,
        threshold=FAITHFULNESS_CALIBRATION_THRESHOLD,
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
    )


async def _evaluate_real_case(
    case: RagEvalCase,
    *,
    scorer: RagasScorer,
    settings,
    answer_relevancy_enforced: bool,
    faithfulness_enforced: bool,
) -> RagEvaluationCaseResult:
    started = time.monotonic()
    result = _base_case(case, status="passed")
    reasons: list[RagFailureReason] = []
    try:
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

        response = await _agent_reply(case, intent, settings)
        result.actual.response = response
        reference = _reference(case)
        if response and reference and CRITICAL_RELATION_TAGS.intersection(case.tags):
            consistency = await scorer.check_fact_consistency(
                response=response,
                reference=reference,
            )
            if not consistency.consistent:
                reasons.append(
                    RagFailureReason(
                        stage="answer",
                        code="critical_fact_contradiction",
                        message=f"关键事实关系与权威事实冲突：{consistency.reason}",
                        severity="critical",
                    )
                )

        if case.rewrite.decision == "retrieve" and response:
            scores = await scorer.score(
                user_input=case.user_query,
                response=response,
                reference=reference,
                retrieved_contexts=[hit.content for hit in rewritten_hits],
                raw_contexts=[hit.content for hit in raw_hits],
            )
            result.scores = RagCaseScores(
                context_recall=scores.context_recall,
                context_precision=scores.context_precision,
                faithfulness=scores.faithfulness,
                answer_relevancy=scores.answer_relevancy,
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
            for name, threshold in _active_ragas_thresholds(
                answer_relevancy_enforced=answer_relevancy_enforced,
                faithfulness_enforced=faithfulness_enforced,
            ).items():
                value = getattr(scores, name)
                if value < threshold:
                    reasons.append(
                        RagFailureReason(
                            stage="ragas",
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
        output.append(result)

    if output:
        failure = next(
            (item for item in output if item.case_id == "rag-chat-cap-ambiguity"),
            output[0],
        )
        failure.status = "failed"
        failure.actual.response = "一大刻度不够一瓶盖。"
        failure.scores.faithfulness = 0.42
        failure.failure_reasons = [
            RagFailureReason(
                stage="answer",
                code="critical_fact_contradiction",
                message="最终回复反转了瓶盖与大刻度的比较方向",
                severity="critical",
            ),
            RagFailureReason(
                stage="ragas",
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
    if not args.mock:
        install_read_only_guards(engine)
        install_in_memory_usage_collector()

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
    try:
        if args.mock:
            results = _mock_cases(cases)
        else:
            if not settings.ai_api_key or not settings.embedding_api_key:
                raise RuntimeError("真实评测要求 AI_API_KEY 和 EMBEDDING_API_KEY")
            scorer = RagasScorer(settings)
            results = []
            for case in cases:
                results.append(
                    await _evaluate_real_case(
                        case,
                        scorer=scorer,
                        settings=settings,
                        answer_relevancy_enforced=(answer_relevancy_policy.enforced),
                        faithfulness_enforced=faithfulness_policy.enforced,
                    )
                )
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
            cases=[],
            status="failed",
            error=f"{type(exc).__name__}: {exc}"[:2000],
        )
        failed.counts.total = len(cases)
        atomic_write_json(run_dir / "status.json", failed.model_dump(mode="json"))
        raise


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
                "/app/tests/fixtures/rag_eval_cases.v1.json",
            )
        ),
    )
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(async_main(parse_args())))
