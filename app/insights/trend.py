"""A locality price trend quoted from the web.

Search engines return pages, not market data, so this is an extraction with
teeth. The model reads search snippets and may only repeat a figure that
appears **verbatim** in one of them; everything that cannot be checked in
code is thrown away:

- the quote must appear in a snippet, character for character;
- that snippet must name the locality and the city, because "Vijay Nagar"
  exists in several Indian cities;
- the figure must be within plausible bounds.

The model never does arithmetic. Property portals publish totals over a
period - "changed by 14.8% in the last 3 years, 44.2% in the last 5 year" -
so it reports the total and the number of years, and the yearly rate is
compounded here. Dividing instead of compounding would turn that 44.2% into
8.8% a year rather than 7.6%.

What comes out is a citation - "this page says prices rose 7% a year" -
never a measurement of DigiNiwas data. It carries the quote and the link so
a reader can judge it, and it is cached for days so the number does not
change between two refreshes of the same screen.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from app.insights.models import LocalitySource
from app.insights.websearch import LocalitySearch

logger = logging.getLogger(__name__)

# A yearly change outside this range is a misread snippet, not a market.
MIN_YEARLY_PERCENT = -30.0
MAX_YEARLY_PERCENT = 50.0
# Bounds on what a snippet may claim before it is treated as a misreading.
MIN_TOTAL_PERCENT = -70.0
MAX_TOTAL_PERCENT = 400.0
MIN_YEARS = 1
MAX_YEARS = 10
# Phrased the way portals write their price-rate pages, which is what puts
# the figures into the search snippet.
TREND_QUERY = "{locality} {city} property rates price appreciation last 5 years"
# How long a quoted figure is reused. Long, so the card is stable: search
# results change week to week even when the market doesn't.
CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
MAX_CACHE_ENTRIES = 512

EXTRACTION_PROMPT = """\
You read search result snippets about property prices in one Indian locality \
and report a price change ONLY if a snippet states one plainly.

Reply with JSON and nothing else:
{"found": true, "total_percent": 14.8, "years": 3, "quote": "...", "source": 2}
or
{"found": false}

Rules:
- `total_percent` is the whole change over the period, exactly as the snippet \
states it. Do NOT convert it to a yearly figure, and do no arithmetic at all.
- `years` is how many years that change covers.
- When a snippet gives several periods ("14.8% in the last 3 years, 44.2% in \
the last 5 year"), report the 3-year one.
- A fall in prices is a negative `total_percent`.
- `quote` must be copied word for word from one snippet. Never reword it.
- The snippet must be about the locality asked for, in the same city.
- `source` is the number of the snippet the quote came from.
- If no snippet states a price change for this locality, or you cannot tell \
how many years it covers, answer not found. Never estimate, infer, or use \
anything you know outside these snippets.
"""


@dataclass(frozen=True, slots=True)
class TrendEstimate:
    """A trend figure quoted from a web page.

    `total_percent` is what the page said; `yearly_percent` is that change
    compounded over `years`, worked out here rather than by the model.
    """

    yearly_percent: float
    total_percent: float
    years: int
    period: str
    quote: str
    source_name: str | None
    source_url: str


class WebTrendEstimator:
    def __init__(
        self,
        search: LocalitySearch,
        chat_model: BaseChatModel,
        *,
        enabled: bool = False,
        results: int = 5,
    ) -> None:
        self._search = search
        self._model = chat_model
        self._enabled = enabled
        self._results = results
        self._cache: dict[str, tuple[float, TrendEstimate | None]] = {}

    @property
    def enabled(self) -> bool:
        return self._enabled and self._search.enabled

    async def estimate(self, locality: str | None, city: str | None) -> TrendEstimate | None:
        """A trend quoted from the web, or None when nothing checks out."""
        if not self.enabled or not locality or not city:
            return None

        key = f"{locality}|{city}".casefold()
        cached = self._cached(key)
        if cached is not None:
            return cached[0]

        sources = await self._search.sources(
            locality, city, query=TREND_QUERY.format(locality=locality, city=city)
        )
        snippets = [source for source in sources if source.snippet]
        estimate = await self._extract(locality, city, snippets) if snippets else None
        self._remember(key, estimate)
        return estimate

    async def _extract(self, locality: str, city: str, sources: list[LocalitySource]) -> TrendEstimate | None:
        listing = "\n".join(
            f"[{index}] {source.title} — {source.snippet}" for index, source in enumerate(sources)
        )
        try:
            reply = await self._model.ainvoke(
                [
                    SystemMessage(content=EXTRACTION_PROMPT),
                    HumanMessage(content=f"Locality: {locality}\nCity: {city}\n\n{listing}"),
                ]
            )
            claim = _parse(reply.content if isinstance(reply.content, str) else str(reply.content))
        except Exception as exc:  # noqa: BLE001 - a trend is optional; the card still works
            logger.warning("Trend extraction failed for %s, %s: %s", locality, city, exc)
            return None

        if claim is None:
            return None
        return _verify(claim, locality=locality, city=city, sources=sources)

    def _cached(self, key: str) -> tuple[TrendEstimate | None] | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        stored_at, estimate = entry
        if time.monotonic() - stored_at > CACHE_TTL_SECONDS:
            del self._cache[key]
            return None
        return (estimate,)

    def _remember(self, key: str, estimate: TrendEstimate | None) -> None:
        if len(self._cache) >= MAX_CACHE_ENTRIES:
            self._cache.clear()
        self._cache[key] = (time.monotonic(), estimate)


@dataclass(frozen=True, slots=True)
class _Claim:
    total_percent: float
    years: int
    quote: str
    source: int


def _parse(reply: str) -> _Claim | None:
    """The model's JSON, or None if it said no or answered with anything else."""
    match = re.search(r"\{.*\}", reply, re.DOTALL)
    if not match:
        return None
    try:
        payload = json.loads(match.group())
        if not payload.get("found"):
            return None
        return _Claim(
            total_percent=float(payload["total_percent"]),
            years=int(payload["years"]),
            quote=str(payload["quote"]).strip(),
            source=int(payload["source"]),
        )
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


def yearly_rate(total_percent: float, years: int) -> float:
    """The compound yearly rate behind a total change over `years`.

    44.2% over 5 years is 7.6% a year, not 8.8%: growth compounds.
    """
    return ((1 + total_percent / 100) ** (1 / years) - 1) * 100


def _verify(
    claim: _Claim, *, locality: str, city: str, sources: list[LocalitySource]
) -> TrendEstimate | None:
    """Accept the claim only if the snippets actually support it."""
    if not MIN_TOTAL_PERCENT <= claim.total_percent <= MAX_TOTAL_PERCENT:
        logger.warning("Discarding implausible trend %.1f%% for %s", claim.total_percent, locality)
        return None
    if not MIN_YEARS <= claim.years <= MAX_YEARS:
        logger.warning("Discarding trend for %s: %s years is out of range", locality, claim.years)
        return None
    if not 0 <= claim.source < len(sources):
        return None

    yearly = yearly_rate(claim.total_percent, claim.years)
    if not MIN_YEARLY_PERCENT <= yearly <= MAX_YEARLY_PERCENT:
        logger.warning("Discarding implausible yearly trend %.1f%% for %s", yearly, locality)
        return None

    source = sources[claim.source]
    snippet = _flatten(f"{source.title} {source.snippet or ''}")
    if _flatten(claim.quote) not in snippet:
        # The model wrote its own sentence rather than quoting one.
        logger.warning("Discarding trend for %s: quote is not in the snippet", locality)
        return None
    if _flatten(locality) not in snippet or _flatten(city) not in snippet:
        logger.warning("Discarding trend for %s: snippet is about somewhere else", locality)
        return None

    return TrendEstimate(
        yearly_percent=round(yearly, 1),
        total_percent=round(claim.total_percent, 1),
        years=claim.years,
        period=f"Past {claim.years} years" if claim.years > 1 else "Past year",
        quote=claim.quote,
        source_name=source.source,
        source_url=source.url,
    )


def _flatten(text: str) -> str:
    return " ".join(text.split()).casefold()
