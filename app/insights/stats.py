"""Statistics chosen to survive small, messy samples.

Property listings contain typos and outliers - a rent entered as a yearly
figure, a demo listing at an absurd price. A mean would follow them; a
median with an interquartile fence does not.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence

# Below this many values, quartiles say more about noise than about the market.
MIN_FOR_QUARTILES = 4
# How far outside the interquartile range a value may sit before it is dropped.
OUTLIER_FENCE = 1.5
# The spread shown for a sample too small for quartiles.
SMALL_SAMPLE_SPREAD = 0.10


def median(values: Sequence[float]) -> float | None:
    return statistics.median(values) if values else None


def without_outliers(values: Sequence[float]) -> list[float]:
    """Values inside the interquartile fence; all of them if the sample is small."""
    if len(values) < MIN_FOR_QUARTILES:
        return list(values)
    first, _, third = statistics.quantiles(values, n=4)
    spread = third - first
    low, high = first - OUTLIER_FENCE * spread, third + OUTLIER_FENCE * spread
    kept = [value for value in values if low <= value <= high]
    return kept or list(values)


def middle_range(values: Sequence[float]) -> tuple[float, float] | None:
    """The range a typical value falls in: the middle half of the sample.

    For a sample too small for quartiles, a flat ±10% around the median, so
    the figure is never shown as more precise than the data supports.
    """
    if not values:
        return None
    if len(values) < MIN_FOR_QUARTILES:
        middle = statistics.median(values)
        return middle * (1 - SMALL_SAMPLE_SPREAD), middle * (1 + SMALL_SAMPLE_SPREAD)
    first, _, third = statistics.quantiles(values, n=4)
    return first, third


def weighted_quantile(values: Sequence[float], weights: Sequence[float], q: float) -> float | None:
    """The value below which a share `q` of the total weight lies.

    With equal weights this is an ordinary quantile; a heavier observation
    pulls the result towards itself.
    """
    pairs = sorted((value, weight) for value, weight in zip(values, weights, strict=True) if weight > 0)
    if not pairs:
        return None
    target = q * sum(weight for _, weight in pairs)
    running = 0.0
    for value, weight in pairs:
        running += weight
        if running >= target - 1e-9:
            return value
    return pairs[-1][0]


def weighted_middle_range(values: Sequence[float], weights: Sequence[float]) -> tuple[float, float] | None:
    """`middle_range`, with each value counting for its weight."""
    kept = [(value, weight) for value, weight in zip(values, weights, strict=True) if weight > 0]
    if not kept:
        return None
    kept_values = [value for value, _ in kept]
    kept_weights = [weight for _, weight in kept]
    if len(kept) < MIN_FOR_QUARTILES:
        middle = weighted_quantile(kept_values, kept_weights, 0.5) or kept_values[0]
        return middle * (1 - SMALL_SAMPLE_SPREAD), middle * (1 + SMALL_SAMPLE_SPREAD)
    low = weighted_quantile(kept_values, kept_weights, 0.25) or kept_values[0]
    high = weighted_quantile(kept_values, kept_weights, 0.75) or kept_values[-1]
    return low, high
