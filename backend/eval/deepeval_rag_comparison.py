"""Offline DeepEval-vs-RAGAS comparison over one saved real RAG evaluation run.

Reads the stored per-case artifacts (intent decision, rewritten query, retrieved
chunks, final reply, RAGAS scores) from a completed report and re-scores the
byte-identical inputs with DeepEval 4.1.5 native metrics plus two deterministic
business checks. No database access, no business-flow reruns, no QiWe.

Budget guard: every judge call is priced immediately via the production pricing
helper; once accumulated spend reaches --budget-yuan the run stops and writes a
partial report. Never retried automatically.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__:
    from .reporting import atomic_write_json, utc_now
else:
    from reporting import atomic_write_json, utc_now

from app.core.config import get_settings
from app.services.ai_service.client import _estimated_cost_yuan

from deepeval.metrics import (
    AnswerRelevancyMetric,
    ContextualPrecisionMetric,
    ContextualRecallMetric,
    FaithfulnessMetric,
)
from deepeval.models import DeepEvalBaseLLM
from deepeval.test_case import LLMTestCase

from app.evaluation.answer_relevancy_calibration import (
    SELECTION_CASE_COUNT as AR_SELECTION_CASE_COUNT,
    VALIDATION_CASE_COUNT as AR_VALIDATION_CASE_COUNT,
    assess_answer_relevancy_calibration,
    assess_two_stage_answer_relevancy_calibration,
    build_answer_relevancy_calibration_pairs,
    default_answer_relevancy_calibration_path,
    load_answer_relevancy_calibration_suite,
)
from app.evaluation.faithfulness_calibration import (
    CALIBRATION_REPEAT_COUNT,
    SELECTION_CASE_COUNT,
    VALIDATION_CASE_COUNT,
    assess_faithfulness_calibration,
    assess_two_stage_faithfulness_calibration,
    build_faithfulness_calibration_pairs,
    default_faithfulness_calibration_path,
    load_faithfulness_calibration_suite,
)
from app.evaluation.rag_cases import load_rag_eval_suite

DEEPEVAL_VERSION = "4.1.5"
_MOCK_MODE = False
REWRITE_HARM_TOLERANCE = 0.05
BASELINE_THRESHOLDS = {
    "context_recall": 0.80,
    "context_precision": 0.70,
    "faithfulness": 0.85,
    "answer_relevancy": 0.80,
}
# Faithfulness threshold currently enforced in production (zh-v4 calibration).
RAGAS_FAITHFULNESS_EFFECTIVE_THRESHOLD = 0.625


class BudgetExceeded(RuntimeError):
    pass


class ComparisonLedger:
    def __init__(self, settings, limit_yuan: float) -> None:
        self._settings = settings
        self.limit_yuan = limit_yuan
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.cache_hit_tokens = 0
        self.spent_yuan = 0.0
        self.failed_calls = 0

    def snapshot(self) -> dict[str, Any]:
        return {
            "limit_yuan": self.limit_yuan,
            "spent_yuan": round(self.spent_yuan, 6),
            "calls": self.calls,
            "failed_calls": self.failed_calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
            "cache_hit_tokens": self.cache_hit_tokens,
        }

    def ensure_budget(self) -> None:
        if self.spent_yuan >= self.limit_yuan - 1e-9:
            raise BudgetExceeded(
                f"budget {self.limit_yuan} yuan reached (spent "
                f"{self.spent_yuan:.4f}); stopping without retry"
            )

    def record(self, *, model: str, usage: Any) -> None:
        prompt_tokens = getattr(usage, "prompt_tokens", None)
        completion_tokens = getattr(usage, "completion_tokens", None)
        cache_hit = getattr(usage, "prompt_cache_hit_tokens", None)
        cost = _estimated_cost_yuan(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            settings=self._settings,
            model=model,
            prompt_cache_hit_tokens=cache_hit,
        )
        self.calls += 1
        self.prompt_tokens += int(prompt_tokens or 0)
        self.completion_tokens += int(completion_tokens or 0)
        self.cache_hit_tokens += int(cache_hit or 0)
        self.spent_yuan += float(cost or 0.0)


class DeepSeekJudge(DeepEvalBaseLLM):
    def __init__(self, settings, ledger: ComparisonLedger, semaphore: asyncio.Semaphore):
        self._settings = settings
        self._ledger = ledger
        self._semaphore = semaphore
        self._async_client = None
        self._sync_client = None

    def load_model(self):
        from openai import AsyncOpenAI

        self._async_client = AsyncOpenAI(
            base_url=self._settings.ai_base_url,
            api_key=self._settings.ai_api_key,
            timeout=float(self._settings.ai_timeout_seconds),
        )
        return self._async_client

    def get_model_name(self) -> str:
        return self._settings.ai_model

    def _format(self, prompt: str) -> list[dict[str, str]]:
        # Format-only instruction; judgement semantics stay untouched.
        return [{"role": "user", "content": prompt + "\n\nRespond with valid JSON only."}]

    async def _a_call(self, prompt: str) -> str:
        self._ledger.ensure_budget()
        client = self._async_client or self.load_model()
        async with self._semaphore:
            response = await client.chat.completions.create(
                model=self.get_model_name(),
                messages=self._format(prompt),
                temperature=0.0,
                max_tokens=int(os.getenv("DEEPEVAL_JUDGE_MAX_TOKENS", "8000")),
                response_format={"type": "json_object"},
            )
        self._ledger.record(model=self.get_model_name(), usage=getattr(response, "usage", None))
        return response.choices[0].message.content or ""

    async def a_generate(self, prompt: str, *args, schema=None, **kwargs):
        return await self._a_call(prompt)

    def generate(self, prompt: str, *args, schema=None, **kwargs):
        from openai import OpenAI

        if self._sync_client is None:
            self._sync_client = OpenAI(
                base_url=self._settings.ai_base_url,
                api_key=self._settings.ai_api_key,
                timeout=float(self._settings.ai_timeout_seconds),
            )
        self._ledger.ensure_budget()
        response = self._sync_client.chat.completions.create(
            model=self.get_model_name(),
            messages=self._format(prompt),
            temperature=0.0,
            max_tokens=int(os.getenv("DEEPEVAL_JUDGE_MAX_TOKENS", "8000")),
            response_format={"type": "json_object"},
        )
        self._ledger.record(model=self.get_model_name(), usage=getattr(response, "usage", None))
        return response.choices[0].message.content or ""


def _expected_reference(case_report: dict) -> str:
    exp = case_report.get("expected")
    if isinstance(exp, str):
        return exp
    if isinstance(exp, dict):
        for key in ("reference", "text", "answer"):
            value = exp.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return json.dumps(exp, ensure_ascii=False)
    return ""


def _hits_context(hits: Any) -> list[str]:
    if not isinstance(hits, list):
        return []
    return [str(hit.get("content") or "") for hit in hits if isinstance(hit, dict)]


async def _ascore(metric: Any, test_case: LLMTestCase) -> tuple[float | None, str | None]:
    try:
        score = await metric.a_measure(test_case)
        return float(score), None
    except BudgetExceeded:
        raise
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"[:400]


def _make_metric(kind: str, judge: DeepSeekJudge) -> Any:
    common = {"model": judge, "include_reason": True, "async_mode": True}
    if kind == "faithfulness":
        return FaithfulnessMetric(threshold=BASELINE_THRESHOLDS["faithfulness"], **common)
    if kind == "answer_relevancy":
        return AnswerRelevancyMetric(threshold=BASELINE_THRESHOLDS["answer_relevancy"], **common)
    if kind == "context_precision":
        return ContextualPrecisionMetric(threshold=BASELINE_THRESHOLDS["context_precision"], **common)
    if kind == "context_recall":
        return ContextualRecallMetric(threshold=BASELINE_THRESHOLDS["context_recall"], **common)
    raise ValueError(f"unknown metric kind: {kind}")


async def run_main_comparison(source_cases: list[dict], expected_decisions: dict, judge: DeepSeekJudge, on_progress) -> dict:
    results: list[dict] = []
    for index, case in enumerate(source_cases, start=1):
        case_id = case.get("case_id")
        actual = case.get("actual") or {}
        ragas_scores = case.get("scores") or {}
        entry: dict[str, Any] = {
            "case_id": case_id,
            "title": case.get("title"),
            "ragas_scores": ragas_scores,
            "deepeval_scores": {},
            "metric_errors": {},
        }

        expected_decision = expected_decisions.get(case_id)
        actual_decision = actual.get("retrieval_decision")
        entry["decision"] = {
            "expected": expected_decision,
            "actual": actual_decision,
            "match": expected_decision == actual_decision,
        }

        rewritten_hits = _hits_context(actual.get("rewritten_query_hits"))
        raw_hits = _hits_context(actual.get("raw_query_hits"))
        response = actual.get("response") or ""
        reference = _expected_reference(case)

        if rewritten_hits:
            test_case = LLMTestCase(
                input=case.get("user_query") or "",
                actual_output=response,
                retrieval_context=rewritten_hits,
                expected_output=reference,
            )
            faithfulness_metric = _make_metric("faithfulness", judge)
            ar_metric = _make_metric("answer_relevancy", judge)
            cp_metric = _make_metric("context_precision", judge)
            cr_metric = _make_metric("context_recall", judge)
            outcomes = await asyncio.gather(
                _ascore(faithfulness_metric, test_case),
                _ascore(ar_metric, test_case),
                _ascore(cp_metric, test_case),
                _ascore(cr_metric, test_case),
                return_exceptions=False,
            )
            for kind, (score, error) in zip(
                ("faithfulness", "answer_relevancy", "context_precision", "context_recall"),
                outcomes,
                strict=True,
            ):
                if error is None:
                    entry["deepeval_scores"][kind] = score
                else:
                    entry["metric_errors"][kind] = error

            if raw_hits:
                raw_case = LLMTestCase(
                    input=case.get("user_query") or "",
                    actual_output=response,
                    retrieval_context=raw_hits,
                    expected_output=reference,
                )
                raw_score, raw_error = await _ascore(_make_metric("context_recall", judge), raw_case)
                if raw_error is None:
                    entry["deepeval_scores"]["raw_context_recall"] = raw_score
                else:
                    entry["metric_errors"]["raw_context_recall"] = raw_error

                cr = entry["deepeval_scores"].get("context_recall")
                if cr is not None and raw_score is not None:
                    entry["rewrite_harm"] = {
                        "deepeval_harmed": (raw_score - cr) > REWRITE_HARM_TOLERANCE,
                        "raw_context_recall": raw_score,
                        "context_recall": cr,
                        "tolerance": REWRITE_HARM_TOLERANCE,
                    }
        results.append(entry)
        on_progress(index, len(source_cases))
    return {"per_case": results}


async def _run_calibration_items(
    *,
    judge,
    metric_kind: str,
    pairs,
    needs_retrieval_context: bool,
    mock: bool,
    on_progress,
) -> list[dict]:
    """Judge every (pair, repeat, side) exactly once; bounded by the shared
    judge semaphore; results are reassembled in deterministic suite order."""

    async def one(pair, repeat_index: int, side: str):
        key = (pair.case_id, repeat_index, side)
        if mock:
            return key, (0.92 if side == "positive" else 0.31), None
        response = pair.positive_response if side == "positive" else pair.negative_response
        contexts = getattr(pair, "retrieved_contexts", None)
        retrieval_context = list(contexts) if needs_retrieval_context and contexts else None
        if retrieval_context is None:
            test_case = LLMTestCase(input=pair.user_input, actual_output=response)
        else:
            test_case = LLMTestCase(
                input=pair.user_input,
                actual_output=response,
                retrieval_context=retrieval_context,
            )
        score, error = await _ascore(_make_metric(metric_kind, judge), test_case)
        return key, score, error

    total = len(pairs) * CALIBRATION_REPEAT_COUNT * 2
    completed = 0

    async def tracked(coro):
        nonlocal completed
        outcome = await coro
        completed += 1
        on_progress(completed, total)
        return outcome

    tasks = [
        tracked(one(pair, repeat_index, side))
        for repeat_index in range(1, CALIBRATION_REPEAT_COUNT + 1)
        for pair in pairs
        for side in ("positive", "negative")
    ]
    outcomes = await asyncio.gather(*tasks)

    by_key = {key: (score, error) for key, score, error in outcomes}
    duplicate_keys = len(outcomes) != len(by_key)
    items: list[dict] = []
    for repeat_index in range(1, CALIBRATION_REPEAT_COUNT + 1):
        for pair in pairs:
            (pos_score, pos_err) = by_key.get((pair.case_id, repeat_index, "positive"), (None, "missing"))
            (neg_score, neg_err) = by_key.get((pair.case_id, repeat_index, "negative"), (None, "missing"))
            errors = [e for e in (pos_err, neg_err) if e]
            if duplicate_keys:
                errors.append("duplicate judgement key detected")
            items.append(
                {
                    "case_id": pair.case_id,
                    "phase": pair.phase,
                    "error_type": getattr(pair, "error_type", None),
                    "repeat_index": repeat_index,
                    "positive_score": pos_score,
                    "negative_score": neg_score,
                    "errors": errors,
                }
            )
    return items


def _stage_runs(
    scored: list[tuple[str, int, float, float]],
    phase_of,
) -> tuple[list, list]:
    selection: dict[int, tuple[list[float], list[float]]] = {}
    validation: dict[int, tuple[list[float], list[float]]] = {}
    for case_id, repeat_index, positive, negative in scored:
        bucket = selection if phase_of(case_id) == "selection" else validation
        runs = bucket.setdefault(repeat_index, ([], []))
        runs[0].append(positive)
        runs[1].append(negative)
    ordered_selection = [selection[key] for key in sorted(selection)]
    ordered_validation = [validation[key] for key in sorted(validation)]
    return ordered_selection, ordered_validation


async def run_faithfulness_calibration(judge: DeepSeekJudge, on_progress) -> dict:
    suite = load_faithfulness_calibration_suite(default_faithfulness_calibration_path())
    pairs = build_faithfulness_calibration_pairs(suite)
    items = await _run_calibration_items(
        judge=judge,
        metric_kind="faithfulness",
        pairs=pairs,
        needs_retrieval_context=True,
        mock=_MOCK_MODE,
        on_progress=on_progress,
    )
    scored = [
        (item["case_id"], item["repeat_index"], item["positive_score"], item["negative_score"])
        for item in items
        if item["positive_score"] is not None and item["negative_score"] is not None
    ]
    phase_by_case = {pair.case_id: pair.phase for pair in pairs}
    selection_runs, validation_runs = _stage_runs(scored, phase_by_case.get)
    if (
        len(selection_runs) != CALIBRATION_REPEAT_COUNT
        or any(len(p) != SELECTION_CASE_COUNT for p, _ in selection_runs)
        or len(validation_runs) != CALIBRATION_REPEAT_COUNT
        or any(len(p) != VALIDATION_CASE_COUNT for p, _ in validation_runs)
    ):
        return {
            "suite_id": suite.calibration_id,
            "items": items,
            "error": (
                "unscored pairs left the stage runs incomplete; "
                f"scored={len(scored)} expected={len(pairs) * CALIBRATION_REPEAT_COUNT}"
            ),
            "passed": None,
            "selected_threshold": None,
            "ragas_production_threshold": RAGAS_FAITHFULNESS_EFFECTIVE_THRESHOLD,
            "stability": _repeat_stability(items),
        }
    two_stage = assess_two_stage_faithfulness_calibration(
        selection_runs=selection_runs,
        validation_runs=validation_runs,
    )
    positives_all = [pos for _, _, pos, _ in scored]
    negatives_all = [neg for _, _, _, neg in scored]
    overall = None
    if two_stage.selected_threshold is not None:
        assessment = assess_faithfulness_calibration(
            positives_all, negatives_all, threshold=two_stage.selected_threshold
        )
        overall = {
            "auc": assessment.auc,
            "paired_accuracy": assessment.paired_accuracy,
            "positive_pass_rate": assessment.positive_pass_rate,
            "negative_rejection_rate": assessment.negative_rejection_rate,
            "mean_gap": assessment.mean_gap,
            "passed": assessment.passed,
            "failed_criteria": list(assessment.failed_criteria),
        }
    stability = _repeat_stability(items)
    return {
        "suite_id": suite.calibration_id,
        "items": items,
        "selected_threshold": two_stage.selected_threshold,
        "threshold_rule": (
            two_stage.threshold_selection.rule if two_stage.threshold_selection else None
        ),
        "threshold_interval": (
            {
                "lower_exclusive": two_stage.threshold_selection.lower_exclusive,
                "upper_inclusive": two_stage.threshold_selection.upper_inclusive,
                "safety_margin": two_stage.threshold_selection.safety_margin,
            }
            if two_stage.threshold_selection
            else None
        ),
        "passed": two_stage.passed,
        "failed_criteria": list(two_stage.failed_criteria),
        "overall_assessment": overall,
        "ragas_production_threshold": RAGAS_FAITHFULNESS_EFFECTIVE_THRESHOLD,
        "stability": stability,
    }


async def run_answer_relevancy_calibration(judge: DeepSeekJudge, on_progress) -> dict:
    suite = load_answer_relevancy_calibration_suite(default_answer_relevancy_calibration_path())
    pairs = build_answer_relevancy_calibration_pairs(suite)
    items = await _run_calibration_items(
        judge=judge,
        metric_kind="answer_relevancy",
        pairs=pairs,
        needs_retrieval_context=False,
        mock=_MOCK_MODE,
        on_progress=on_progress,
    )
    scored = [
        (item["case_id"], item["repeat_index"], item["positive_score"], item["negative_score"])
        for item in items
        if item["positive_score"] is not None and item["negative_score"] is not None
    ]
    phase_by_case = {pair.case_id: pair.phase for pair in pairs}
    selection_runs, validation_runs = _stage_runs(scored, phase_by_case.get)
    if (
        len(selection_runs) != CALIBRATION_REPEAT_COUNT
        or any(len(p) != AR_SELECTION_CASE_COUNT for p, _ in selection_runs)
        or len(validation_runs) != CALIBRATION_REPEAT_COUNT
        or any(len(p) != AR_VALIDATION_CASE_COUNT for p, _ in validation_runs)
    ):
        return {
            "suite_id": suite.calibration_id,
            "items": items,
            "error": (
                "unscored pairs left the stage runs incomplete; "
                f"scored={len(scored)} expected={len(pairs) * CALIBRATION_REPEAT_COUNT}"
            ),
            "passed": None,
            "selected_threshold": None,
            "ragas_production_state": "calibration_failed_not_enforced_display_only",
            "stability": _repeat_stability(items),
        }
    two_stage = assess_two_stage_answer_relevancy_calibration(
        selection_runs=selection_runs,
        validation_runs=validation_runs,
    )
    positives_all = [pos for _, _, pos, _ in scored]
    negatives_all = [neg for _, _, _, neg in scored]
    overall = None
    if two_stage.selected_threshold is not None:
        assessment = assess_answer_relevancy_calibration(
            positives_all, negatives_all, threshold=two_stage.selected_threshold
        )
        overall = {
            "auc": assessment.auc,
            "paired_accuracy": assessment.paired_accuracy,
            "positive_pass_rate": assessment.positive_pass_rate,
            "negative_rejection_rate": assessment.negative_rejection_rate,
            "mean_gap": assessment.mean_gap,
            "passed": assessment.passed,
            "failed_criteria": list(assessment.failed_criteria),
        }
    return {
        "suite_id": suite.calibration_id,
        "items": items,
        "selected_threshold": two_stage.selected_threshold,
        "threshold_rule": (
            two_stage.threshold_selection.rule if two_stage.threshold_selection else None
        ),
        "passed": two_stage.passed,
        "failed_criteria": list(two_stage.failed_criteria),
        "overall_assessment": overall,
        "ragas_production_state": "calibration_failed_not_enforced_display_only",
        "stability": _repeat_stability(items),
    }


def _repeat_stability(items: list[dict]) -> dict[str, Any]:
    by_pair: dict[str, dict[int, dict]] = {}
    for item in items:
        by_pair.setdefault(item["case_id"], {})[item["repeat_index"]] = item
    gaps: list[float] = []
    flips = 0
    compared = 0
    for repeats in by_pair.values():
        first = repeats.get(1)
        second = repeats.get(2)
        if not first or not second:
            continue
        for side in ("positive_score", "negative_score"):
            a, b = first.get(side), second.get(side)
            if a is None or b is None:
                continue
            compared += 1
            gaps.append(abs(a - b))
    return {
        "compared_values": compared,
        "max_abs_repeat_gap": max(gaps) if gaps else None,
        "mean_abs_repeat_gap": (sum(gaps) / len(gaps)) if gaps else None,
    }


def _summarize_comparison(per_case: list[dict]) -> dict[str, Any]:
    metric_keys = ("context_recall", "context_precision", "faithfulness", "answer_relevancy")
    summary: dict[str, Any] = {"metrics": {}, "pass_rate": {}, "counts": {}}
    decisions = [c["decision"] for c in per_case if c.get("decision")]
    summary["counts"]["decision_mismatches"] = sum(1 for d in decisions if not d["match"])
    summary["counts"]["decision_total"] = len(decisions)
    harms = [
        (c["case_id"], c["rewrite_harm"])
        for c in per_case
        if isinstance(c.get("rewrite_harm"), dict)
    ]
    summary["counts"]["rewrite_compared"] = len(harms)
    summary["counts"]["rewrite_harmed_deepeval"] = sum(1 for _, h in harms if h["deepeval_harmed"])
    ragas_harmed = sum(
        1
        for case in per_case
        for reason in (case.get("_ragas_failure_reasons") or [])
        if reason.get("code") == "rewrite_harmed_retrieval"
    )
    summary["counts"]["rewrite_harmed_ragas_baseline"] = ragas_harmed
    for key in metric_keys:
        ragas_values = [
            c["ragas_scores"].get(key)
            for c in per_case
            if isinstance(c.get("ragas_scores"), dict) and c["ragas_scores"].get(key) is not None
        ]
        deep_values = [
            c["deepeval_scores"][key]
            for c in per_case
            if c["deepeval_scores"].get(key) is not None
        ]
        pairs = [
            (float(c["ragas_scores"][key]), float(c["deepeval_scores"][key]))
            for c in per_case
            if c["deepeval_scores"].get(key) is not None
            and isinstance(c.get("ragas_scores"), dict)
            and c["ragas_scores"].get(key) is not None
        ]
        divergences = [round(abs(a - b), 6) for a, b in pairs]
        threshold = BASELINE_THRESHOLDS[key]
        ragas_pass = sum(1 for a, _ in pairs if a >= threshold)
        deep_pass = sum(1 for _, b in pairs if b >= threshold)
        effective_threshold = (
            RAGAS_FAITHFULNESS_EFFECTIVE_THRESHOLD if key == "faithfulness" else threshold
        )
        ragas_pass_effective = sum(1 for a, _ in pairs if a >= effective_threshold)
        verdict_flips_vs_effective = sum(
            1
            for a, b in pairs
            if (a >= effective_threshold) != (b >= effective_threshold)
        )
        summary["metrics"][key] = {
            "compared_pairs": len(pairs),
            "ragas_mean": round(sum(a for a, _ in pairs) / len(pairs), 4) if pairs else None,
            "deepeval_mean": round(sum(b for _, b in pairs) / len(pairs), 4) if pairs else None,
            "max_abs_divergence": max(divergences) if divergences else None,
            "mean_abs_divergence": round(sum(divergences) / len(divergences), 4) if divergences else None,
            "verdict_flips_vs_effective_threshold": verdict_flips_vs_effective,
            "effective_threshold_used_for_flips": effective_threshold,
        }
        summary["pass_rate"][key] = {
            "baseline_threshold": threshold,
            "ragas_pass": ragas_pass,
            "deepeval_pass": deep_pass,
            "ragas_pass_rate": round(ragas_pass / len(pairs), 4) if pairs else None,
            "deepeval_pass_rate": round(deep_pass / len(pairs), 4) if pairs else None,
            "ragas_pass_at_effective_threshold": ragas_pass_effective,
        }
    summary["counts"]["metric_errors"] = sum(
        len(c.get("metric_errors") or {}) for c in per_case
    )
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DeepEval vs RAGAS offline comparison")
    parser.add_argument(
        "--source-report", type=Path, default=None,
        help="completed RAGAS run report; required only for --stage full",
    )
    parser.add_argument("--run-id")
    parser.add_argument("--budget-yuan", type=float, default=10.0)
    parser.add_argument("--max-concurrency", type=int, default=12)
    parser.add_argument(
        "--reports-root",
        type=Path,
        default=Path(os.getenv("RAG_EVALUATION_REPORTS_PATH", "/app/diagnostics/rag-evaluations")),
    )
    parser.add_argument("--dataset", type=Path, default=None)
    parser.add_argument(
        "--stage", choices=("full", "calibration"), default="full",
        help="full = main comparison + both calibrations; calibration = calibrations only",
    )
    parser.add_argument(
        "--mock", action="store_true",
        help="zero-cost dry run of the calibration pipeline with deterministic scores;"
        " implies --stage calibration and never calls a model",
    )
    return parser.parse_args()


async def async_main(args: argparse.Namespace) -> int:
    global _MOCK_MODE
    _MOCK_MODE = bool(args.mock)
    if _MOCK_MODE:
        args.stage = "calibration"

    if args.stage == "full":
        if not args.source_report:
            raise RuntimeError("--stage full requires --source-report")
        source_payload = json.loads(args.source_report.read_text(encoding="utf-8"))
        source_summary = source_payload.get("summary") or {}
        source_cases = source_payload.get("cases") or []
        if not source_cases:
            raise RuntimeError("source report contains no cases")
        suite = load_rag_eval_suite(args.dataset) if args.dataset else load_rag_eval_suite()
        expected_decisions = {case.case_id: case.rewrite.decision for case in suite.cases}
        for case in source_cases:
            case["_ragas_failure_reasons"] = case.get("failure_reasons") or []
    else:
        source_summary = {}
        source_cases = []
        expected_decisions = {}

    run_id = args.run_id or datetime.now(timezone.utc).strftime(
        ("deepeval-calibration-%Y%m%dT%H%M%SZ" if args.stage == "calibration"
         else "deepeval-comparison-%Y%m%dT%H%M%SZ")
    )
    run_dir = args.reports_root / run_id
    if run_dir.exists():
        raise RuntimeError(f"single-run lock violated: {run_dir} already exists")
    run_dir.mkdir(parents=True)

    settings = get_settings()
    if not _MOCK_MODE and not settings.ai_api_key:
        raise RuntimeError("real comparison requires AI_API_KEY")

    started_at = utc_now()
    state: dict[str, Any] = {
        "schema_version": 1,
        "run_id": run_id,
        "run_mode": "mock" if _MOCK_MODE else "real",
        "stage_requested": args.stage,
        "status": "running",
        "started_at": started_at.isoformat(),
        "updated_at": started_at.isoformat(),
        "git_sha": (os.getenv("RAG_EVAL_GIT_SHA") or "unknown")[:64],
        "model": settings.ai_model,
        "judge_model": settings.ai_model,
        "deepeval_version": DEEPEVAL_VERSION,
        "ragas_baseline_run_id": source_summary.get("run_id"),
        "dataset_id": source_summary.get("dataset_id"),
        "dataset_version": source_summary.get("dataset_version"),
        "budget": {"limit_yuan": args.budget_yuan},
        "stage": ("main_comparison" if args.stage == "full" else "faithfulness_calibration"),
        "progress": {"done": 0},
        "main_comparison": None,
        "faithfulness_calibration": None,
        "answer_relevancy_calibration": None,
        "error": None,
    }
    ledger = ComparisonLedger(settings, args.budget_yuan)
    judge = DeepSeekJudge(settings, ledger, asyncio.Semaphore(max(1, args.max_concurrency)))

    def persist(status_value: str | None = None, error: str | None = None) -> None:
        state["updated_at"] = utc_now().isoformat()
        state["budget"].update(ledger.snapshot())
        if status_value:
            state["status"] = status_value
        if error:
            state["error"] = error
        atomic_write_json(run_dir / "status.json", state)

    def main_progress(done: int, total: int) -> None:
        state["progress"] = {"done": done, "total": total}
        persist()

    def calibration_progress(done: int, total: int, stage: str) -> None:
        state["progress"] = {"stage": stage, "done": done, "total": total}
        persist()

    exit_code = 0
    try:
        if args.stage == "full":
            main_result = await run_main_comparison(source_cases, expected_decisions, judge, main_progress)
            main_result["summary"] = _summarize_comparison(main_result["per_case"])
            state["main_comparison"] = main_result
            state["stage"] = "faithfulness_calibration"
            persist()
        else:
            state["stage"] = "faithfulness_calibration"
            persist()

        state["faithfulness_calibration"] = await run_faithfulness_calibration(
            judge,
            lambda done, total: calibration_progress(done, total, "faithfulness_calibration"),
        )
        state["stage"] = "answer_relevancy_calibration"
        persist()

        state["answer_relevancy_calibration"] = await run_answer_relevancy_calibration(
            judge,
            lambda done, total: calibration_progress(done, total, "answer_relevancy_calibration"),
        )
        final_status = "completed"
    except BudgetExceeded as exc:
        final_status = "stopped_budget"
        state["error"] = str(exc)[:1000]
        exit_code = 2
    except Exception as exc:  # noqa: BLE001
        final_status = "failed"
        state["error"] = f"{type(exc).__name__}: {exc}"[:2000]
        exit_code = 1

    state["stage"] = "done"
    state["status"] = final_status
    payload = dict(state)
    payload["finished_at"] = utc_now().isoformat()
    payload["budget"].update(ledger.snapshot())
    atomic_write_json(run_dir / "report.json", payload)
    atomic_write_json(run_dir / "status.json", payload)
    print(json.dumps({"run_id": run_id, "status": final_status, **ledger.snapshot()}, ensure_ascii=False))
    return exit_code


def main() -> int:
    return asyncio.run(async_main(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
