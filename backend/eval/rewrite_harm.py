from __future__ import annotations

from dataclasses import dataclass


REWRITE_RECALL_DROP_THRESHOLD = 0.05
REWRITE_HARM_POLICY_VERSION = "rewrite-harm-v2"


@dataclass(frozen=True)
class RewriteHarmAssessment:
    applicable: bool
    harmed: bool
    score: float
    reason: str
    signal: str


def _normalize_query(value: str | None) -> str:
    return "".join((value or "").split()).casefold()


def assess_rewrite_harm(
    *,
    original_query: str,
    rewritten_query: str | None,
    raw_hit_count: int,
    rewritten_hit_count: int,
    raw_recall: float | None,
    rewritten_recall: float | None,
    recall_drop_threshold: float = REWRITE_RECALL_DROP_THRESHOLD,
) -> RewriteHarmAssessment:
    """Assess whether query rewriting damaged retrieval.

    The hard hit-collapse guard deliberately does not depend on an LLM judge:
    if the original query retrieved evidence but the rewritten query retrieved
    nothing, the rewrite caused an observable loss even when both semantic
    recall scores are zero because the reference fact was too coarse.
    """

    if not _normalize_query(rewritten_query) or _normalize_query(
        original_query
    ) == _normalize_query(rewritten_query):
        return RewriteHarmAssessment(
            applicable=False,
            harmed=False,
            score=0.0,
            reason="未发生实质查询改写，不纳入改写损伤率",
            signal="not_rewritten",
        )

    if raw_hit_count > 0 and rewritten_hit_count == 0:
        return RewriteHarmAssessment(
            applicable=True,
            harmed=True,
            score=1.0,
            reason=(
                f"原问题召回 {raw_hit_count} 条证据，改写后降为 0 条；"
                "即使语义召回尺子同时给低分，也按确定性证据判为损伤"
            ),
            signal="hit_collapse",
        )

    if raw_recall is None or rewritten_recall is None:
        return RewriteHarmAssessment(
            applicable=True,
            harmed=False,
            score=0.0,
            reason="缺少原问题或改写后的语义召回分数，无法完成损伤判断",
            signal="missing_recall_scores",
        )

    recall_drop = max(0.0, raw_recall - rewritten_recall)
    harmed = rewritten_recall + recall_drop_threshold < raw_recall
    return RewriteHarmAssessment(
        applicable=True,
        harmed=harmed,
        score=recall_drop,
        reason=(
            f"原问题事实召回 {raw_recall:.3f} → 改写后 {rewritten_recall:.3f}，"
            f"下降 {recall_drop:.3f}；下降超过 {recall_drop_threshold:.2f} 判为损伤"
        ),
        signal="recall_drop" if harmed else "no_detected_harm",
    )
