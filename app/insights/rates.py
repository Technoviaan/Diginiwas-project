"""Published price rates for an area: what property portals say land or flats cost.

Portals publish area-wide rates - "The average price per sqft for Plots in
Vijay Nagar, Indore is Rs. 11,048", "Land rates in Rau, Indore are around
Rs 3250-5800 per sq ft". The same search results also carry single listings -
"₹2.6 Cr. ₹13,000 /sqft · 2,000 sqft" - which say nothing about the area.

The model picks candidate rates out of the snippets. Code accepts one only if:

- the quote appears word for word in a result naming the locality and the city;
- it reads as an area-wide rate: average, range, rates, around, median or trend;
- it is about what was asked - land and plots, or flats and apartments;
- every amount is in the quote, and code, not the model, reads it;
- the quote names exactly one unit. Code converts sq yd, sq m and acres to per
  sq ft; a bigha's size differs by state, so it is kept as written;
- the per-unit rate is plausible.

A government "registry rate" is labelled as such rather than passed off as a
market price. Rates are what portals publish - usually asking prices - so each
one carries its quote and source.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from app.insights.models import LocalitySource
from app.insights.websearch import LocalitySearch
from app.properties import format_inr, parse_inr

logger = logging.getLogger(__name__)

Kind = Literal["land", "flat", "any"]
Unit = Literal["sqft", "sqyd", "sqm", "acre", "bigha"]

# Search results fetched per lookup: the page with an area-wide rate is often
# below the top three, under listing pages.
AREA_RATE_RESULTS = 6
MAX_RATES = 3
RATE_QUERIES: dict[str, str] = {
    "land": "{locality} {city} plot land rate per sq ft",
    "flat": "{locality} {city} flat apartment rate per sq ft",
    "any": "{locality} {city} property rates per sq ft",
}
SQFT_PER_UNIT: dict[str, float] = {"sqft": 1.0, "sqyd": 9.0, "sqm": 10.7639, "acre": 43_560.0}
UNIT_LABELS: dict[str, str] = {
    "sqft": "sq ft",
    "sqyd": "sq yd",
    "sqm": "sq m",
    "acre": "acre",
    "bigha": "bigha",
}
# A rate outside these bounds is a misread snippet, not a market.
MIN_RATE_PER_SQFT = 50
MAX_RATE_PER_SQFT = 300_000
MIN_RATE_PER_BIGHA = 10_000
MAX_RATE_PER_BIGHA = 1_000_000_000
CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
MAX_CACHE_ENTRIES = 512

_UNIT_PATTERNS: tuple[tuple[Unit, re.Pattern[str]], ...] = (
    ("sqyd", re.compile(r"sq\.?\s*(?:yd|yard)s?\b|square\s+yards?|\bgaj\b")),
    ("sqm", re.compile(r"sq\.?\s*(?:m|mt|mtr|metre|meter)s?\b|\bsqm\b|square\s+met(?:re|er)s?")),
    ("sqft", re.compile(r"sq\.?\s*(?:ft|feet)|\bsqft|square\s+f(?:ee|oo)t")),
    ("acre", re.compile(r"\bacres?\b")),
    ("bigha", re.compile(r"\bbighas?\b")),
)
_AREA_RATE = re.compile(r"\b(?:average|avg|range|ranges|ranging|rates?|around|median|trend|between)\b")
_KIND_PATTERNS: dict[str, re.Pattern[str]] = {
    "land": re.compile(r"\b(?:land|plots?)\b"),
    "flat": re.compile(r"\b(?:flats?|apartments?|builder floors?)\b"),
}
_REGISTRY = re.compile(r"\b(?:registry|registration|guideline|circle rate|collector)\b")
_LEADING_AMOUNT = re.compile(
    r"(?:₹|rs\.?|inr)?\s*\d[\d,]*(?:\.\d+)?\s*(?:k|l|lac|lacs|lakh|lakhs|cr|crs|crore|crores)?\b"
)

EXTRACTION_PROMPT = """\
You read search result snippets about property prices in one Indian area and \
report the PRICE RATES they state for the area as a whole.

Reply with JSON and nothing else:
{"rates": [{"average": "<amount or null>", "low": "<amount or null>", "high": "<amount or null>", "quote": "<words from one snippet>", "source": <snippet number>}]}
or
{"rates": []}

Rules:
- Only rates for the area as a whole count: an average, a range, or "rates are \
around". One property's price or price per sq ft ("₹2.6 Cr. ₹13,000 /sqft", \
"Price/Sqft : 12000") is NOT an area rate: ignore it.
- A rate is a price per unit of area: per sq ft, sq yd, sq m, acre or bigha. A \
total price ("average price of ₹2.17 Cr", "₹33.4 Lac - ₹4.74 Cr") is NOT a rate: \
ignore it.
- Report a rate from every snippet that states one, at most one per snippet.
- `average`, `low` and `high` are only the amount, copied exactly as the snippet \
writes it with its currency, without the unit ("Rs 6400", not "Rs 6400 per sq ft"). \
Use null for any the snippet doesn't give. Do no arithmetic and no unit conversion.
- `quote` is copied word for word from one snippet, and contains every amount \
you report.
- The snippet must be about the area asked for, in the same city.
- `source` is the number of the snippet the quote came from.
- If no snippet states an area rate, reply {"rates": []}. Never estimate, infer, or use anything outside these snippets.
"""


@dataclass(frozen=True, slots=True)
class AreaRate:
    """One published rate for an area, in the unit the page states it."""

    kind: Literal["land", "flat", "property"]
    basis: Literal["asking price", "registry rate"]
    unit: Unit
    average: int | None
    low: int | None
    high: int | None
    quote: str
    title: str
    source_name: str | None
    source_url: str

    def per_sqft(self, amount: int | None) -> int | None:
        """An amount in this rate's unit, per sq ft. None for a bigha: its size varies by state."""
        factor = SQFT_PER_UNIT.get(self.unit)
        if amount is None or factor is None:
            return None
        return round(amount / factor)

    @property
    def summary(self) -> str:
        """The rate in words, e.g. '₹11,048 per sq ft average; ₹356–₹22,987 per sq ft'."""
        unit = UNIT_LABELS[self.unit]
        parts: list[str] = []
        if self.average is not None:
            parts.append(f"{_rupees(self.average)} per {unit} average")
        if self.low is not None and self.high is not None:
            parts.append(f"{_rupees(self.low)}–{_rupees(self.high)} per {unit}")
        return "; ".join(parts)

    def as_source(self) -> LocalitySource:
        return LocalitySource(
            title=self.title, url=self.source_url, snippet=self.quote, source=self.source_name
        )


class AreaRateFinder:
    def __init__(self, search: LocalitySearch, chat_model: BaseChatModel, *, enabled: bool = False) -> None:
        self._search = search
        self._model = chat_model
        self._enabled = enabled
        self._cache: dict[str, tuple[float, list[AreaRate]]] = {}

    @property
    def enabled(self) -> bool:
        return self._enabled and self._search.enabled

    async def find(self, locality: str, city: str, kind: Kind) -> list[AreaRate] | None:
        """Published rates for the area: [] when none checked out, None when lookup is off."""
        if not self.enabled:
            return None

        key = f"{locality}|{city}|{kind}".casefold()
        entry = self._cache.get(key)
        if entry is not None and time.monotonic() - entry[0] <= CACHE_TTL_SECONDS:
            return entry[1]

        query = RATE_QUERIES[kind].format(locality=locality, city=city)
        sources = [
            source for source in await self._search.sources(locality, city, query=query) if source.snippet
        ]
        rates = await self._extract(locality, city, kind, sources) if sources else []

        if len(self._cache) >= MAX_CACHE_ENTRIES:
            self._cache.clear()
        self._cache[key] = (time.monotonic(), rates)
        return rates

    async def _extract(
        self, locality: str, city: str, kind: Kind, sources: list[LocalitySource]
    ) -> list[AreaRate]:
        listing = "\n".join(
            f"[{index}] {source.title} — {source.snippet}" for index, source in enumerate(sources)
        )
        wanted = {
            "land": "land and plot rates",
            "flat": "flat and apartment rates",
            "any": "property rates of any kind",
        }[kind]
        try:
            reply = await self._model.ainvoke(
                [
                    SystemMessage(content=EXTRACTION_PROMPT),
                    HumanMessage(
                        content=f"Area: {locality}\nCity: {city}\nLooking for: {wanted}\n\n{listing}"
                    ),
                ]
            )
            claims = _parse(reply.content if isinstance(reply.content, str) else str(reply.content))
        except Exception as exc:  # noqa: BLE001 - a lookup that fails reports no rates
            logger.warning("Area rate extraction failed for %s, %s: %s", locality, city, exc)
            return []

        rates: list[AreaRate] = []
        seen: set[str] = set()
        for claim in claims:
            rate = _verify(claim, locality=locality, city=city, kind=kind, sources=sources)
            if rate is None or rate.source_url in seen:
                continue
            seen.add(rate.source_url)
            rates.append(rate)
            if len(rates) >= MAX_RATES:
                break
        return rates


@dataclass(frozen=True, slots=True)
class _Claim:
    average: str | None
    low: str | None
    high: str | None
    quote: str
    source: int


def _parse(reply: str) -> list[_Claim]:
    """The model's candidate rates; [] for anything that isn't the expected JSON."""
    match = re.search(r"\{.*\}", reply, re.DOTALL)
    if not match:
        return []
    try:
        payload = json.loads(match.group())
    except json.JSONDecodeError:
        return []
    items = (payload.get("rates") or []) if isinstance(payload, dict) else []

    claims: list[_Claim] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            claims.append(
                _Claim(
                    average=_optional_text(item.get("average")),
                    low=_optional_text(item.get("low")),
                    high=_optional_text(item.get("high")),
                    quote=str(item["quote"]).strip(),
                    source=int(item["source"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return claims


def _verify(
    claim: _Claim, *, locality: str, city: str, kind: Kind, sources: list[LocalitySource]
) -> AreaRate | None:
    """Accept a candidate rate only if the snippet supports every part of it."""
    if not 0 <= claim.source < len(sources):
        return None
    source = sources[claim.source]
    snippet = _flatten(f"{source.title} {source.snippet or ''}")
    quote = _flatten(claim.quote)

    if not quote or quote not in snippet:
        return _rejected(locality, "the quote is not in the snippet")
    if _flatten(locality) not in snippet or _flatten(city) not in snippet:
        return _rejected(locality, "the snippet is about somewhere else")
    if not _AREA_RATE.search(quote):
        return _rejected(locality, "it is not an area-wide rate")
    if kind in _KIND_PATTERNS and not _KIND_PATTERNS[kind].search(quote):
        return _rejected(locality, f"it is not about {kind}")
    if claim.average is None and (claim.low is None or claim.high is None):
        return _rejected(locality, "it has no average and no full range")

    amounts: dict[str, int | None] = {}
    for name, text in (("average", claim.average), ("low", claim.low), ("high", claim.high)):
        if text is None:
            amounts[name] = None
            continue
        if not _quotes_amount(quote, text):
            return _rejected(locality, f"the {name} amount is not in the quote")
        try:
            amounts[name] = _amount(text)
        except ValueError:
            return _rejected(locality, f"the {name} amount can't be read")
    if amounts["low"] is None or amounts["high"] is None:
        amounts["low"] = amounts["high"] = None  # half a range is no range
    elif amounts["low"] > amounts["high"]:
        return _rejected(locality, "the range runs backwards")

    units = {unit for unit, pattern in _UNIT_PATTERNS if pattern.search(quote)}
    if len(units) != 1:
        return _rejected(locality, "the quote names no unit, or several")
    unit = units.pop()
    if not all(_plausible(unit, value) for value in amounts.values() if value is not None):
        return _rejected(locality, "the rate is implausible")

    if _KIND_PATTERNS["land"].search(quote):
        about: Literal["land", "flat", "property"] = "land"
    elif _KIND_PATTERNS["flat"].search(quote):
        about = "flat"
    else:
        about = "property"

    return AreaRate(
        kind=about,
        basis="registry rate" if _REGISTRY.search(quote) else "asking price",
        unit=unit,
        average=amounts["average"],
        low=amounts["low"],
        high=amounts["high"],
        quote=claim.quote,
        title=source.title,
        source_name=source.source,
        source_url=source.url,
    )


def _rejected(locality: str, why: str) -> None:
    logger.warning("Discarding an area rate for %s: %s", locality, why)
    return None


def _quotes_amount(quote: str, text: str) -> bool:
    """Whether the quote states this amount, allowing for commas and currency.

    The model writes "Rs 6,400" for "Rs 6400-11500", or "Rs 11500" for the top
    of that range; the number itself must still stand alone in the quote.
    """
    number = _bare(text)
    # The number may end a sentence ("is Rs. 11,048."), but not run on into more digits.
    pattern = rf"(?<![\d.])(?<!\d\.){re.escape(number)}(?!\.?\d)"
    return bool(number) and re.search(pattern, _bare(quote)) is not None


def _bare(text: str) -> str:
    """Text without commas, currency marks or spaces around them: 'Rs. 11,048' -> '11048'."""
    text = re.sub(r"(?<=\d),(?=\d)", "", text.casefold())
    return re.sub(r"(?:₹|\brs\.?|\binr\b)\s*", "", text).strip()


def _amount(text: str) -> int:
    """The leading amount of a figure such as '₹ 4344/ sq.ft' or 'Rs 25 lakh'."""
    match = _LEADING_AMOUNT.match(text.casefold().strip())
    if not match:
        raise ValueError(f"no amount in {text!r}")
    return parse_inr(match.group().strip())


def _plausible(unit: Unit, amount: int) -> bool:
    if unit == "bigha":
        return MIN_RATE_PER_BIGHA <= amount <= MAX_RATE_PER_BIGHA
    return MIN_RATE_PER_SQFT <= amount / SQFT_PER_UNIT[unit] <= MAX_RATE_PER_SQFT


def _rupees(amount: int) -> str:
    return format_inr(amount) or f"₹{amount:,}" if amount >= 100_000 else f"₹{amount:,}"


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _flatten(text: str) -> str:
    return " ".join(text.split()).casefold()
