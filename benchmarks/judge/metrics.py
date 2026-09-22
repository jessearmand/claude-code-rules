"""Classification, calibration and latency metrics over probability/label pairs. Standard library only."""

from __future__ import annotations

import math
from dataclasses import dataclass


def accuracy(pairs: list[tuple[float, bool]], threshold: float) -> float:
    return sum((p >= threshold) == y for p, y in pairs) / len(pairs)


def confusion(pairs: list[tuple[float, bool]], threshold: float) -> tuple[int, int, int, int]:
    """(agree, false positives, false negatives, n)."""
    fp = sum(p >= threshold and not y for p, y in pairs)
    fn = sum(p < threshold and y for p, y in pairs)
    return len(pairs) - fp - fn, fp, fn, len(pairs)


def auroc(pairs: list[tuple[float, bool]]) -> float | None:
    """Mann-Whitney AUC with tie handling; None when one class is absent."""
    pos = [p for p, y in pairs if y]
    neg = [p for p, y in pairs if not y]
    if not pos or not neg:
        return None
    wins = 0.0
    for a in pos:
        for b in neg:
            wins += 1.0 if a > b else 0.5 if a == b else 0.0
    return wins / (len(pos) * len(neg))


def brier(pairs: list[tuple[float, bool]]) -> float:
    return sum((p - float(y)) ** 2 for p, y in pairs) / len(pairs)


def ece(pairs: list[tuple[float, bool]], bins: int = 10) -> float:
    """Expected calibration error on P(true) against the binary label, equal-width bins."""
    buckets: list[list[tuple[float, bool]]] = [[] for _ in range(bins)]
    for p, y in pairs:
        buckets[min(int(p * bins), bins - 1)].append((p, y))
    total = 0.0
    for bucket in buckets:
        if not bucket:
            continue
        mean_p = sum(p for p, _ in bucket) / len(bucket)
        mean_y = sum(float(y) for _, y in bucket) / len(bucket)
        total += abs(mean_p - mean_y) * len(bucket)
    return total / len(pairs)


def log_loss(pairs: list[tuple[float, bool]], eps: float = 1e-4) -> float:
    total = 0.0
    for p, y in pairs:
        p = min(max(p, eps), 1 - eps)
        total -= math.log(p if y else 1 - p)
    return total / len(pairs)


@dataclass
class LatencySummary:
    n: int
    median: float
    p95: float
    mean: float
    maximum: float

    @classmethod
    def of(cls, values: list[float]) -> LatencySummary | None:
        if not values:
            return None
        ordered = sorted(values)
        return cls(
            len(ordered), _quantile(ordered, 0.5), _quantile(ordered, 0.95), sum(ordered) / len(ordered), ordered[-1]
        )


def _quantile(ordered: list[float], q: float) -> float:
    if len(ordered) == 1:
        return ordered[0]
    position = q * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)
