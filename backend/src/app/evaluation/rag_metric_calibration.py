from __future__ import annotations

from dataclasses import dataclass


MIN_AUC = 0.90
MIN_PAIRED_ACCURACY = 0.80
MIN_POSITIVE_PASS_RATE = 0.80
MIN_NEGATIVE_REJECTION_RATE = 0.80
MIN_MEAN_GAP = 0.20


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
