"""Indian rupee amounts: reading them the way people write them, and formatting them."""

from __future__ import annotations

import re

_INR_UNITS = {
    "k": 1e3, "thousand": 1e3, "hazar": 1e3, "hazaar": 1e3,
    "l": 1e5, "lac": 1e5, "lacs": 1e5, "lakh": 1e5, "lakhs": 1e5,
    "cr": 1e7, "crs": 1e7, "crore": 1e7, "crores": 1e7,
    "m": 1e6, "mn": 1e6, "million": 1e6,
}  # fmt: skip

_INR_AMOUNT = re.compile(
    r"^(?:₹|rs\.?|inr)?\s*"
    r"(\d[\d,]*(?:\.\d+)?|\.\d+)\s*"  # 30 | 30,000 | 1.2
    r"([a-z]+)?\.?\s*"  # k | lakh | cr
    r"(?:(?:/|per)\s*(?:month|mon|mo)|/-)?$"  # optional /month or /-
)


def parse_inr(value: int | float | str) -> int:
    """'30K' -> 30000, '50 lakh' -> 5000000, '1.2 crore' -> 12000000.

    Exists because language models are unreliable at this arithmetic: in
    live testing gpt-4o-mini turned "under 1 crore" into 100000000, ten
    crore. The model passes budgets the way the user said them instead.
    """
    if isinstance(value, bool):
        raise ValueError("a price cannot be true/false")
    if isinstance(value, int | float):
        if value < 0:
            raise ValueError("a price cannot be negative")
        return round(value)

    match = _INR_AMOUNT.match(value.strip().lower())
    unit = match.group(2) if match else None
    if not match or (unit and unit not in _INR_UNITS):
        raise ValueError(
            f"could not read {value!r} as an amount; use e.g. '30K', '50 lakh', "
            "'1.2 crore' or a number of rupees"
        )
    return round(float(match.group(1).replace(",", "")) * _INR_UNITS.get(unit or "", 1))


def format_inr(amount: float | None) -> str | None:
    """8500000 -> '₹85 L', 12000000 -> '₹1.2 Cr', 28000 -> '₹28k'."""
    if amount is None:
        return None
    for threshold, unit in ((1e7, " Cr"), (1e5, " L"), (1e3, "k")):
        if amount >= threshold:
            scaled = f"{amount / threshold:.2f}".rstrip("0").rstrip(".")
            return f"₹{scaled}{unit}"
    return f"₹{amount:,.0f}"
