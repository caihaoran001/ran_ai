from __future__ import annotations

from dataclasses import dataclass
from math import ceil


MIN_AUC = 0.90
MIN_PAIRED_ACCURACY = 0.80
MIN_POSITIVE_PASS_RATE = 0.80
MIN_NEGATIVE_REJECTION_RATE = 0.80
MIN_MEAN_GAP = 0.20
PERFECT_GAP_SELECTION_RULE = "perfect_gap_midpoint_v1"
STABLE_INTERVAL_SELECTION_RULE = "class_rate_interval_midpoint_v1"


@dataclass(frozen=True)
class RagMetricCalibrationAssessment:
    positive_mean: float
    negative_mean: float
    mean_gap: float
    auc: float
    paired_accuracy: float
    positive_pass_rate: float
    negative_rejection_rate: float
    passed: bool
    failed_criteria: tuple[str, ...]


@dataclass(frozen=True)
class RagMetricThresholdSelection:
    """A data-derived threshold that is stable across every selection repeat.

    A score equal to the threshold is accepted as positive.  Therefore the
    lower bound is exclusive (negative scores must be below it) and the upper
    bound is inclusive (positive scores may equal it).
    """

    rule: str
    minimum_classification_rate: float
    lower_exclusive: float
    upper_inclusive: float
    selected_threshold: float
    safety_margin: float


def select_stable_calibration_threshold(
    runs: list[tuple[list[float], list[float]]],
    *,
    minimum_classification_rate: float,
) -> RagMetricThresholdSelection | None:
    """Select the midpoint of the common, data-supported threshold interval.

    First prefer a gap that classifies every observed positive and negative
    correctly across every repeat. If no such gap exists, find every threshold
    at which both classes meet the required rate. The midpoint of the selected
    interval maximizes the distance to either boundary, so a default setting
    never decides a tie.
    """

    if not runs:
        raise ValueError("calibration threshold selection requires at least one run")
    if not 0.0 < minimum_classification_rate <= 1.0:
        raise ValueError("minimum classification rate must be in (0, 1]")

    perfect_lower_bounds: list[float] = []
    perfect_upper_bounds: list[float] = []
    lower_bounds: list[float] = []
    upper_bounds: list[float] = []
    for positive_scores, negative_scores in runs:
        if not positive_scores or len(positive_scores) != len(negative_scores):
            raise ValueError(
                "calibration threshold selection requires equal non-empty score pairs"
            )
        if any(
            not 0.0 <= score <= 1.0
            for score in positive_scores + negative_scores
        ):
            raise ValueError("calibration scores must be between 0 and 1")

        required_correct = ceil(
            len(positive_scores) * minimum_classification_rate - 1e-12
        )
        ordered_positive = sorted(positive_scores)
        ordered_negative = sorted(negative_scores)
        perfect_lower_bounds.append(ordered_negative[-1])
        perfect_upper_bounds.append(ordered_positive[0])
        # At least `required_correct` positives must be >= threshold, while at
        # least that many negatives must remain strictly below it.
        upper_bounds.append(ordered_positive[len(positive_scores) - required_correct])
        lower_bounds.append(ordered_negative[required_correct - 1])

    perfect_lower_exclusive = max(perfect_lower_bounds)
    perfect_upper_inclusive = min(perfect_upper_bounds)
    if perfect_lower_exclusive < perfect_upper_inclusive:
        return _threshold_selection(
            rule=PERFECT_GAP_SELECTION_RULE,
            minimum_classification_rate=1.0,
            lower_exclusive=perfect_lower_exclusive,
            upper_inclusive=perfect_upper_inclusive,
        )

    lower_exclusive = max(lower_bounds)
    upper_inclusive = min(upper_bounds)
    if lower_exclusive >= upper_inclusive:
        return None
    return _threshold_selection(
        rule=STABLE_INTERVAL_SELECTION_RULE,
        minimum_classification_rate=minimum_classification_rate,
        lower_exclusive=lower_exclusive,
        upper_inclusive=upper_inclusive,
    )


def _threshold_selection(
    *,
    rule: str,
    minimum_classification_rate: float,
    lower_exclusive: float,
    upper_inclusive: float,
) -> RagMetricThresholdSelection:
    selected_threshold = (lower_exclusive + upper_inclusive) / 2
    rounded_threshold = round(selected_threshold, 6)
    if lower_exclusive < rounded_threshold < upper_inclusive:
        selected_threshold = rounded_threshold
    return RagMetricThresholdSelection(
        rule=rule,
        minimum_classification_rate=minimum_classification_rate,
        lower_exclusive=lower_exclusive,
        upper_inclusive=upper_inclusive,
        selected_threshold=selected_threshold,
        safety_margin=min(
            selected_threshold - lower_exclusive,
            upper_inclusive - selected_threshold,
        ),
    )


def _auc(positive_scores: list[float], negative_scores: list[float]) -> float:
    comparisons = 0.0
    for positive in positive_scores:
        for negative in negative_scores:
            if positive > negative:
                comparisons += 1.0
            elif positive == negative:
                comparisons += 0.5
    return comparisons / (len(positive_scores) * len(negative_scores))


def assess_rag_metric_calibration(
    positive_scores: list[float],
    negative_scores: list[float],
    *,
    threshold: float,
) -> RagMetricCalibrationAssessment:
    if not positive_scores or len(positive_scores) != len(negative_scores):
        raise ValueError(
            "calibration requires equal non-empty positive and negative scores"
        )
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("calibration threshold must be between 0 and 1")
    if any(not 0.0 <= score <= 1.0 for score in positive_scores + negative_scores):
        raise ValueError("calibration scores must be between 0 and 1")

    positive_mean = sum(positive_scores) / len(positive_scores)
    negative_mean = sum(negative_scores) / len(negative_scores)
    mean_gap = positive_mean - negative_mean
    auc = _auc(positive_scores, negative_scores)
    paired_accuracy = sum(
        positive > negative
        for positive, negative in zip(positive_scores, negative_scores, strict=True)
    ) / len(positive_scores)
    positive_pass_rate = sum(score >= threshold for score in positive_scores) / len(
        positive_scores
    )
    negative_rejection_rate = sum(score < threshold for score in negative_scores) / len(
        negative_scores
    )

    checks = {
        f"AUC低于{MIN_AUC:.2f}": auc >= MIN_AUC,
        f"正负配对准确率低于{MIN_PAIRED_ACCURACY:.2f}": (
            paired_accuracy >= MIN_PAIRED_ACCURACY
        ),
        f"正例在{threshold:.2f}门槛下通过率低于{MIN_POSITIVE_PASS_RATE:.2f}": (
            positive_pass_rate >= MIN_POSITIVE_PASS_RATE
        ),
        f"负例在{threshold:.2f}门槛下拒绝率低于{MIN_NEGATIVE_REJECTION_RATE:.2f}": (
            negative_rejection_rate >= MIN_NEGATIVE_REJECTION_RATE
        ),
        f"正负均值差低于{MIN_MEAN_GAP:.2f}": mean_gap >= MIN_MEAN_GAP,
    }
    failed = tuple(label for label, passed in checks.items() if not passed)
    return RagMetricCalibrationAssessment(
        positive_mean=positive_mean,
        negative_mean=negative_mean,
        mean_gap=mean_gap,
        auc=auc,
        paired_accuracy=paired_accuracy,
        positive_pass_rate=positive_pass_rate,
        negative_rejection_rate=negative_rejection_rate,
        passed=not failed,
        failed_criteria=failed,
    )
